"""`sales-agent config ...`: publishing, inspecting and rolling back versioned client settings."""

import json

import pytest
import yaml
from app_support import client_yaml, env_for, fake_builders, write_client

from sales_app import cli
from sales_app.bootstrap import build_application
from sales_app.config.resolution import strip_settings
from sales_app.config_store import FileConfigStore


@pytest.fixture
def environment(tmp_path, monkeypatch):
    """A clean process environment with temporary clients and config directories."""
    for name in list(__import__("os").environ):
        if name.startswith("SALES_AGENT_") or name == "WHATSAPP_VERIFY_TOKEN":
            monkeypatch.delenv(name)
    clients, config = tmp_path / "clients", tmp_path / "config"
    clients.mkdir()
    monkeypatch.setenv("SALES_AGENT_CLIENTS_DIR", str(clients))
    monkeypatch.setenv("SALES_AGENT_CONFIG_DIR", str(config))
    monkeypatch.setenv("SALES_AGENT_LOCK_FILE", str(tmp_path / "agent.lock"))
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    for name, value in env_for("alpha").items():
        monkeypatch.setenv(name, value)
    return tmp_path


def run(*argv):
    return cli.main(["config", *argv])


def write_settings(path, **overrides):
    document = {
        "schema_version": 1,
        "display_name": "Alpha",
        "timezone": "Africa/Tunis",
        "sheets": {"spreadsheet_id": "sheet-alpha"},
        "llm": {"model": "test-model"},
        "brain": {"settings": {"tone": "warm"}},
    }
    document.update(overrides)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def clients(environment):
    write_client(environment / "clients", "alpha")
    return environment / "clients"


def versions(environment):
    return [v.version for v in FileConfigStore(environment / "config").history("alpha")]


class TestImportPublishShow:
    def test_import_seeds_version_one_from_client_yaml(self, environment, clients, capsys):
        assert run("import", "alpha") == cli.EXIT_OK
        assert "published alpha v1" in capsys.readouterr().out
        record = FileConfigStore(environment / "config").current("alpha")
        assert record.note == "imported from client.yaml" and record.author.startswith("cli")
        assert record.document["display_name"] == "Alpha"
        assert "whatsapp" not in record.document  # operator wiring is not copied

    def test_import_refuses_when_versions_exist(self, environment, clients, capsys):
        assert run("import", "alpha") == 0
        assert run("import", "alpha") == cli.EXIT_CONFIG
        assert "versions already exist" in capsys.readouterr().err
        assert versions(environment) == [1]

    def test_publish_appends_a_normalized_version_with_author_and_note(
        self, environment, clients, capsys
    ):
        settings = write_settings(environment / "s.json", daily_action_limit=42)
        assert run("publish", "alpha", str(settings), "--author", "dhouha", "--note", "raise") == 0
        record = FileConfigStore(environment / "config").current("alpha")
        assert (record.author, record.note) == ("dhouha", "raise")
        assert record.document["daily_action_limit"] == 42
        assert record.document["mode"] == "dry_run"  # defaults are written out

    def test_show_prints_the_version_and_its_metadata(self, environment, clients, capsys):
        write_settings(environment / "s.json")
        run("publish", "alpha", str(environment / "s.json"), "--note", "first")
        capsys.readouterr()
        assert run("show", "alpha") == 0
        shown = json.loads(capsys.readouterr().out)
        assert shown["version"] == 1 and shown["note"] == "first" and shown["parent"] is None
        assert shown["document"]["llm"] == {"provider": "openai", "model": "test-model"}
        assert run("show", "alpha", "--version", "1") == 0

    def test_show_and_history_when_nothing_is_published(self, environment, clients, capsys):
        assert run("history", "alpha") == 0
        assert "no versions published" in capsys.readouterr().out
        assert run("show", "alpha") == cli.EXIT_JOB_FAILED
        assert "no versions published" in capsys.readouterr().err
        assert run("show", "alpha", "--version", "9") == cli.EXIT_JOB_FAILED


class TestRejection:
    def test_an_invalid_document_is_rejected_with_every_problem_and_nothing_is_written(
        self, environment, clients, capsys
    ):
        bad = write_settings(
            environment / "bad.json", timezone="Mars/Olympus", daily_action_limit=0, mode="yolo"
        )
        assert run("publish", "alpha", str(bad)) == cli.EXIT_CONFIG
        err = capsys.readouterr().err
        assert "timezone" in err and "daily_action_limit" in err and "mode" in err
        assert versions(environment) == []
        assert not (environment / "config").exists()

    def test_operator_owned_keys_secrets_and_unknown_keys_are_rejected(
        self, environment, clients, capsys
    ):
        for extra in (
            {"whatsapp": {"token_env": "X"}},
            {"brain": {"settings": {"api_key": "sk-123"}}},
            {"mystery": 1},
        ):
            assert (
                run("publish", "alpha", str(write_settings(environment / "b.json", **extra))) == 2
            )
        assert versions(environment) == []
        err = capsys.readouterr().err
        assert "is not a client setting" in err and "looks like a secret" in err
        assert "sk-123" not in err  # the value is never echoed

    def test_unreadable_input_is_a_configuration_error(self, environment, clients, capsys):
        broken = environment / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        assert run("publish", "alpha", str(broken)) == cli.EXIT_CONFIG
        assert run("publish", "alpha", str(environment / "missing.json")) == cli.EXIT_CONFIG
        assert "cannot read" in capsys.readouterr().err

    def test_unknown_clients_and_invalid_ids_are_configuration_errors(
        self, environment, clients, capsys
    ):
        settings = write_settings(environment / "s.json")
        assert run("publish", "ghost", str(settings)) == cli.EXIT_CONFIG
        assert run("publish", "../alpha", str(settings)) == cli.EXIT_CONFIG
        assert run("history", "../alpha") == cli.EXIT_CONFIG
        assert "invalid client id" in capsys.readouterr().err

    def test_a_corrupt_store_is_reported_and_exits_1(self, environment, clients, capsys):
        run("import", "alpha")
        (environment / "config" / "alpha" / "versions" / "000001.json").write_text("{", "utf-8")
        assert run("show", "alpha") == cli.EXIT_JOB_FAILED
        assert run("publish", "alpha", str(write_settings(environment / "s.json"))) == 1
        assert "error:" in capsys.readouterr().err


class TestHistoryDiffRollback:
    @pytest.fixture
    def three_versions(self, environment, clients):
        run("import", "alpha")
        run("publish", "alpha", str(write_settings(environment / "b.json", daily_action_limit=30)))
        run("publish", "alpha", str(write_settings(environment / "c.json", daily_action_limit=40)))

    def test_history_lists_versions_oldest_first(self, environment, three_versions, capsys):
        capsys.readouterr()
        assert run("history", "alpha") == 0
        lines = capsys.readouterr().out.splitlines()
        assert [line.split()[0] for line in lines] == ["v1", "v2", "v3"]
        assert "imported from client.yaml" in lines[0]

    def test_diff_shows_what_changed(self, environment, three_versions, capsys):
        capsys.readouterr()
        assert run("diff", "alpha", "1", "3") == 0
        out = capsys.readouterr().out
        assert "--- v1" in out and "+++ v3" in out
        assert '-  "daily_action_limit": 20' in out and '+  "daily_action_limit": 40' in out

    def test_diff_of_identical_versions_and_unknown_versions(
        self, environment, three_versions, capsys
    ):
        capsys.readouterr()
        assert run("diff", "alpha", "2", "2") == 0 and "no differences" in capsys.readouterr().out
        assert run("diff", "alpha", "1", "9") == cli.EXIT_JOB_FAILED

    def test_rollback_publishes_the_old_document_as_a_new_version(
        self, environment, three_versions, capsys
    ):
        assert run("rollback", "alpha", "--to", "1") == 0
        store = FileConfigStore(environment / "config")
        latest = store.current("alpha")
        assert latest.version == 4 and latest.note == "rollback to v1"
        assert latest.document == store.get("alpha", 1).document
        assert [v.version for v in store.history("alpha")] == [1, 2, 3, 4]  # history is kept

    def test_rollback_to_an_unknown_version_writes_nothing(self, environment, three_versions):
        assert run("rollback", "alpha", "--to", "9") == cli.EXIT_JOB_FAILED
        assert versions(environment) == [1, 2, 3]

    def test_rollback_refuses_a_version_that_no_longer_fits_the_wiring(
        self, environment, three_versions, capsys
    ):
        # The operator's wiring changed since version 1 was published and no longer validates.
        wiring = strip_settings(client_yaml("alpha"))
        wiring["whatsapp"]["token_env"] = "not valid!"
        (environment / "clients" / "alpha" / "client.yaml").write_text(
            yaml.safe_dump(wiring), encoding="utf-8"
        )
        assert run("rollback", "alpha", "--to", "1") == cli.EXIT_CONFIG
        assert versions(environment) == [1, 2, 3]


class TestValidate:
    def test_a_file_is_validated_without_publishing(self, environment, clients, capsys):
        good = write_settings(environment / "good.json")
        assert run("validate", "alpha", "--file", str(good)) == 0
        assert "is valid" in capsys.readouterr().out
        bad = write_settings(environment / "bad.json", timezone="Nope/Nope")
        assert run("validate", "alpha", "--file", str(bad)) == cli.EXIT_CONFIG
        assert versions(environment) == []

    def test_the_current_version_is_validated_against_todays_wiring(
        self, environment, clients, capsys
    ):
        run("import", "alpha")
        capsys.readouterr()
        assert run("validate", "alpha") == 0
        assert "alpha v1 is valid" in capsys.readouterr().out

    def test_validate_with_no_version_and_no_file_exits_1(self, environment, clients):
        assert run("validate", "alpha") == cli.EXIT_JOB_FAILED


class TestTheAgentUsesWhatWasPublished:
    def test_validate_reports_the_config_version_a_client_runs_on(
        self, environment, monkeypatch, capsys
    ):
        raw = strip_settings(client_yaml("alpha"))
        (environment / "clients" / "alpha").mkdir()
        (environment / "clients" / "alpha" / "client.yaml").write_text(yaml.safe_dump(raw))
        builders, _ = fake_builders()
        monkeypatch.setattr(
            cli, "build_application", lambda s, **kw: build_application(s, builders=builders, **kw)
        )
        assert cli.main(["validate"]) == cli.EXIT_CONFIG  # no version yet: says how to fix it
        assert "config import alpha" in capsys.readouterr().err

        write_settings(environment / "s.json", display_name="Alpha")
        assert run("publish", "alpha", str(environment / "s.json")) == 0
        capsys.readouterr()
        assert cli.main(["validate"]) == cli.EXIT_OK
        assert "ok  alpha: mode dry_run [config v1]" in capsys.readouterr().out


class TestParser:
    def test_a_subcommand_is_required(self, environment):
        with pytest.raises(SystemExit) as raised:
            cli.main(["config"])
        assert raised.value.code == 2
