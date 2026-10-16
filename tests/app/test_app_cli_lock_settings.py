"""CLI commands, the single-instance lock, process settings and operational logging."""

import json
import logging

import pytest
from app_support import SpyEngine, env_for, fake_builders, write_client
from pydantic import ValidationError

from sales_app import cli
from sales_app.bootstrap import build_application
from sales_app.lockfile import InstanceAlreadyRunningError, InstanceLock
from sales_app.logs import JsonFormatter, client_logger
from sales_app.settings import Settings
from sales_core.approval import ApprovalMode


@pytest.fixture
def environment(tmp_path, monkeypatch):
    """A clean process environment pointing at a temporary clients directory and lock file."""
    for name in list(__import__("os").environ):
        if name.startswith("SALES_AGENT_") or name == "WHATSAPP_VERIFY_TOKEN":
            monkeypatch.delenv(name)
    clients = tmp_path / "clients"
    clients.mkdir()
    monkeypatch.setenv("SALES_AGENT_CLIENTS_DIR", str(clients))
    monkeypatch.setenv("SALES_AGENT_LOCK_FILE", str(tmp_path / "agent.lock"))
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)  # never read a developer's .env
    for name, value in env_for("alpha", "beta").items():
        monkeypatch.setenv(name, value)
    return clients


@pytest.fixture
def fakes(monkeypatch):
    """Make the CLI build applications with fake adapters (the real Sheets adapter is pending)."""
    builders, made = fake_builders()
    seen: dict = {}

    def build(settings, **kwargs):
        seen.update(kwargs)
        return build_application(settings, builders=builders, **kwargs)

    monkeypatch.setattr(cli, "build_application", build)
    return made, seen


class TestValidate:
    def test_reports_each_clients_effective_mode(self, environment, fakes, capsys):
        write_client(environment, "alpha", mode="autonomous")
        write_client(environment, "beta", raw="enabled: false\n")
        assert cli.main(["validate"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "ok  alpha: mode approval (configured autonomous, clamped)" in out
        assert "skipped (enabled: false): beta" in out
        assert "1 client(s) valid" in out

    def test_lists_every_problem_and_exits_2(self, environment, fakes, capsys):
        write_client(environment, "alpha", timezone="Mars/Olympus")
        write_client(environment, "beta", daily_action_limit=0)
        assert cli.main(["validate"]) == cli.EXIT_CONFIG
        err = capsys.readouterr().err
        assert "alpha:" in err and "beta:" in err and "2 problem" in err

    def test_with_the_real_integration_table_a_missing_credentials_file_is_reported(
        self, environment, capsys
    ):
        write_client(environment, "alpha")
        assert cli.main(["validate"]) == cli.EXIT_CONFIG
        err = capsys.readouterr().err
        assert "alpha: repository:" in err and "creds.json" in err

    def test_a_directory_with_only_disabled_clients_is_valid(self, environment, fakes, capsys):
        write_client(environment, "alpha", raw="enabled: false\n")
        assert cli.main(["validate"]) == cli.EXIT_OK
        assert "0 client(s) valid" in capsys.readouterr().out


class TestRun:
    def test_runs_one_job_once(self, environment, fakes, capsys):
        write_client(environment, "alpha")
        assert cli.main(["run", "--client", "alpha", "--job", "dispatch"]) == cli.EXIT_OK
        assert "alpha dispatch: ok" in capsys.readouterr().out

    def test_a_failing_job_exits_1_and_prints_the_problem(
        self, environment, fakes, monkeypatch, capsys
    ):
        write_client(environment, "alpha")
        _made, _seen = fakes
        inner = cli.build_application

        def build_with_broken_engine(settings, **kwargs):
            application = inner(settings, **kwargs)
            spy = application.registry.get("alpha").engine = SpyEngine()
            spy.fail_on.add("prepare")
            return application

        monkeypatch.setattr(cli, "build_application", build_with_broken_engine)
        assert cli.main(["run", "--client", "alpha", "--job", "daily"]) == cli.EXIT_JOB_FAILED
        captured = capsys.readouterr()
        assert "prepare exploded" in captured.err and "FAILED" in captured.out

    def test_unknown_or_disabled_clients_are_a_configuration_error(
        self, environment, fakes, capsys
    ):
        write_client(environment, "alpha")
        write_client(environment, "beta", raw="enabled: false\n")
        assert cli.main(["run", "--client", "beta", "--job", "dispatch"]) == cli.EXIT_CONFIG
        assert "no enabled client 'beta'" in capsys.readouterr().err

    def test_dry_run_forces_the_safest_mode_for_that_run(self, environment, fakes, capsys):
        write_client(environment, "alpha", mode="approval")
        made, seen = fakes
        assert (
            cli.main(["run", "--client", "alpha", "--job", "dispatch", "--dry-run"]) == cli.EXIT_OK
        )
        assert seen["mode_ceiling"] is ApprovalMode.DRY_RUN
        assert made.channels == {}  # a dry run never even built the real channel

    def test_invalid_arguments_are_rejected_by_the_parser(self, environment):
        for argv in ([], ["run"], ["run", "--client", "a", "--job", "bogus"], ["nonsense"]):
            with pytest.raises(SystemExit) as exit_info:
                cli.main(argv)
            assert exit_info.value.code == 2


def _route_paths(app) -> set[str]:
    """Every route path, flattening `include_router` mounts (FastAPI represents one in
    `app.routes` as a single wrapper without its own `.path`, not as individual routes)."""
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            paths.update(sub.path for sub in included.routes)
    return paths


class TestServe:
    def test_starts_exactly_one_worker_with_the_configured_address(
        self, environment, fakes, monkeypatch
    ):
        import uvicorn

        write_client(environment, "alpha")
        monkeypatch.setenv("SALES_AGENT_PORT", "9123")
        started = {}
        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(app=app, **kw))
        assert cli.main(["serve"]) == cli.EXIT_OK
        assert started["workers"] == 1  # the single-writer guarantee is in-process
        assert started["port"] == 9123 and started["host"] == "127.0.0.1"
        assert _route_paths(started["app"]) >= {
            "/health",
            "/health/clients",
            "/webhooks/{provider}",
            "/admin/v1/auth/login",
            "/admin/v1/auth/logout",
            "/admin/v1/auth/me",
        }

    def test_invalid_configuration_prevents_startup(self, environment, fakes, monkeypatch, capsys):
        import uvicorn

        write_client(environment, "alpha", timezone="Mars/Olympus")
        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("must not start"))
        assert cli.main(["serve"]) == cli.EXIT_CONFIG
        assert "unknown timezone" in capsys.readouterr().err

    def test_zero_enabled_clients_still_serves_the_admin_and_runs_no_agent(
        self, environment, fakes, monkeypatch, capsys
    ):
        import uvicorn

        write_client(environment, "alpha", raw="enabled: false\n")
        made, seen = fakes
        started = {}
        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(app=app, **kw))
        assert cli.main(["serve"]) == cli.EXIT_OK
        assert seen["require_clients"] is False
        assert "/admin/v1/auth/login" in _route_paths(started["app"])
        assert "serving the Admin API only" in capsys.readouterr().out
        assert made.channels == {} and made.repositories == {}  # nothing was even built

    def test_zero_enabled_clients_means_the_scheduler_has_nothing_to_run(self, environment):
        write_client(environment, "alpha", raw="enabled: false\n")
        builders, made = fake_builders()
        application = build_application(
            Settings(), env={}, builders=builders, require_clients=False
        )
        assert application.registry.runtimes() == []
        assert application.scheduler.tick() == []
        assert made.channels == {} and made.repositories == {}

    def test_zero_enabled_clients_the_admin_can_still_sign_in(self, environment):
        from datetime import UTC, datetime

        from fastapi.testclient import TestClient

        from sales_app.admin_auth import (
            AdminRole,
            AdminUser,
            InMemoryAdminSessionStore,
            InMemoryAdminUserStore,
        )
        from sales_app.admin_auth.passwords import hash_password
        from sales_app.api import create_app

        users = InMemoryAdminUserStore()
        users.create(
            AdminUser(
                id="a1",
                username="root",
                password_hash=hash_password("correct-horse-battery-staple"),
                role=AdminRole.OWNER,
                created_at=datetime.now(UTC),
            )
        )
        builders, _ = fake_builders()
        application = build_application(
            Settings(),
            env={},
            builders=builders,
            require_clients=False,
            admin_user_store=users,
            admin_session_store=InMemoryAdminSessionStore(),
        )
        client = TestClient(create_app(application, run_scheduler=False), base_url="https://t")
        wrong = client.post("/admin/v1/auth/login", json={"username": "root", "password": "x"})
        assert wrong.status_code == 401  # authentication is not bypassed
        ok = client.post(
            "/admin/v1/auth/login",
            json={"username": "root", "password": "correct-horse-battery-staple"},
        )
        assert ok.status_code == 200
        assert client.get("/admin/v1/auth/me").status_code == 200

    def test_a_second_instance_is_refused(self, environment, fakes, tmp_path, capsys):
        write_client(environment, "alpha")
        with InstanceLock(tmp_path / "agent.lock"):
            assert cli.main(["serve"]) == cli.EXIT_RUNNING
            assert cli.main(["run", "--client", "alpha", "--job", "dispatch"]) == cli.EXIT_RUNNING
        assert "another instance" in capsys.readouterr().err

    def test_the_lock_is_released_when_the_server_stops(
        self, environment, fakes, monkeypatch, tmp_path
    ):
        import uvicorn

        write_client(environment, "alpha")
        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
        cli.main(["serve"])
        with InstanceLock(tmp_path / "agent.lock"):  # acquirable again
            pass


class TestInstanceLock:
    def test_only_one_holder_at_a_time(self, tmp_path):
        first, second = InstanceLock(tmp_path / "x.lock"), InstanceLock(tmp_path / "x.lock")
        first.acquire()
        with pytest.raises(InstanceAlreadyRunningError, match="another instance"):
            second.acquire()
        first.release()
        second.acquire()  # free again
        second.release()

    def test_context_manager_releases_even_on_error(self, tmp_path):
        with pytest.raises(RuntimeError), InstanceLock(tmp_path / "x.lock"):
            raise RuntimeError("boom")
        with InstanceLock(tmp_path / "x.lock"):
            pass

    def test_releasing_twice_is_harmless_and_a_lock_cannot_be_taken_twice_by_one_object(
        self, tmp_path
    ):
        lock = InstanceLock(tmp_path / "x.lock")
        lock.acquire()
        with pytest.raises(RuntimeError, match="already held"):
            lock.acquire()
        lock.release()
        lock.release()

    def test_a_dead_holder_leaves_no_stale_lock(self, tmp_path):
        lock = InstanceLock(tmp_path / "x.lock")
        lock.acquire()
        lock._unlock(lock._fd)
        assert InstanceLock(tmp_path / "x.lock").acquire() is None


class TestSettings:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        for name in list(__import__("os").environ):
            if name.startswith("SALES_AGENT_") or name == "WHATSAPP_VERIFY_TOKEN":
                monkeypatch.delenv(name)

    def test_defaults_are_conservative(self):
        settings = Settings()
        assert (
            settings.mode_ceiling is ApprovalMode.APPROVAL
        )  # autonomy needs a deployment decision
        assert settings.host == "127.0.0.1" and settings.tick_seconds == 30
        assert settings.whatsapp_verify_token is None
        assert settings.allowed_llm_models == {}  # unset: no restriction (see llm_registry)

    def test_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("SALES_AGENT_MODE_CEILING", "autonomous")
        monkeypatch.setenv("SALES_AGENT_PORT", "9000")
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "abc")
        settings = Settings()
        assert settings.mode_ceiling is ApprovalMode.AUTONOMOUS
        assert settings.port == 9000 and settings.whatsapp_verify_token == "abc"

    def test_an_invalid_ceiling_is_refused(self, monkeypatch):
        monkeypatch.setenv("SALES_AGENT_MODE_CEILING", "yolo")
        with pytest.raises(ValidationError):
            Settings()

    def test_allowed_llm_models_is_read_as_json_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(
            "SALES_AGENT_ALLOWED_LLM_MODELS", '{"openai": ["gpt-4o", "gpt-4o-mini"]}'
        )
        settings = Settings()
        assert settings.allowed_llm_models == {"openai": ("gpt-4o", "gpt-4o-mini")}


class TestLogging:
    def _record(self, logger, message):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        logger.logger.addHandler(handler)
        logger.logger.setLevel(logging.DEBUG)
        try:
            logger.error(message)
        finally:
            logger.logger.removeHandler(handler)
        return records[0]

    def test_records_carry_the_client_id_as_json(self):
        record = self._record(client_logger("sales_app.test", "alpha"), "job failed")
        entry = json.loads(JsonFormatter().format(record))
        assert entry["client_id"] == "alpha" and entry["message"] == "job failed"
        assert entry["level"] == "ERROR" and entry["logger"] == "sales_app.test"

    def test_records_without_a_client_omit_the_field(self):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
        assert "client_id" not in json.loads(JsonFormatter().format(record))

    def test_exceptions_are_included(self):
        try:
            raise ValueError("bad")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "x", logging.ERROR, __file__, 1, "oops", None, sys.exc_info()
            )
        assert "ValueError: bad" in json.loads(JsonFormatter().format(record))["exception"]
