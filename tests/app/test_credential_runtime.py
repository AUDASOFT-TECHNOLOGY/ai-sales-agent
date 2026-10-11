"""Credentials from the store reach the running application without a restart.

Covers: resolution (store first, environment as fallback, per-client and global scopes), startup
with missing credentials (clients are *not ready*, the application still starts), refreshing a
running client (new Engine built offline and installed only when idle, old runtime kept on failure,
`state`/`health` preserved), removal of a credential, the webhook for a client that is not ready,
parsers, the status endpoints and the CLI.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from app_support import (
    VERIFY_TOKEN,
    InlineExecutor,
    app_secret,
    env_for,
    fake_builders,
    make_settings,
    phone_id,
    write_client,
)
from engine_harness import phone_for, prospect
from factories import NOW
from fastapi.testclient import TestClient
from test_admin_integrations_api import PASSWORD, user
from test_app_api import payload, post

from sales_app.admin_auth import AdminRole, InMemoryAdminSessionStore, InMemoryAdminUserStore
from sales_app.agent_state import InMemoryAgentStateStore
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.cli import _validate
from sales_app.config import load_client_configs
from sales_app.config import secrets as secret_keys
from sales_app.errors import ConfigurationError
from sales_app.registry import ClientRegistry
from sales_app.secret_store import (
    CATALOG,
    GLOBAL_SCOPE,
    InMemorySecretAudit,
    InMemorySecretStore,
    SecretStoreCorruptError,
    client_scope,
)
from sales_app.wiring import ChannelProvider, IntegrationBuilders, default_builders
from sales_core.approval import ApprovalMode
from sales_core.contracts import ClientId
from sales_integrations.fakes import FakeClock
from sales_integrations.whatsapp import WhatsAppWebhookParser

PHONES = {"alpha": "1000000001", "beta": "2000000002"}
LEGACY_ONLY = {"GOOGLE_APPLICATION_CREDENTIALS": "creds.json"}  # the Sheets access is still env


class World:
    """An application over fake integrations, a credential store, and the Admin API."""

    def __init__(self, tmp_path, *ids, env=None, store=None, executor="inline", clients_kw=None):
        self.tmp_path = tmp_path
        self.clock = FakeClock(NOW)
        self.store = store if store is not None else InMemorySecretStore(now=self.clock.now)
        self.flags = SimpleNamespace(fail_channel=False, tokens=[])
        for cid in ids:
            write_client(tmp_path / "clients", cid, **(clients_kw or {}))
        builders, self.made = fake_builders(self.clock)
        provider = builders.channels["whatsapp"]
        inner = provider.build

        def build(config):
            self.flags.tokens.append(config.secrets.get_optional("whatsapp.access_token"))
            if self.flags.fail_channel:
                token = config.secrets.get_optional("whatsapp.access_token")
                raise RuntimeError(f"cannot connect with token {token}")
            return inner(config)

        def parser(secrets):
            token = self.store.get(GLOBAL_SCOPE, "whatsapp.verify_token") or VERIFY_TOKEN
            return WhatsAppWebhookParser(app_secrets=secrets, verify_token=token)

        self.builders = IntegrationBuilders(
            repositories=builders.repositories,
            llms=builders.llms,
            channels={"whatsapp": replace(provider, build=build, build_parser=parser)},
        )
        self.users = InMemoryAdminUserStore()
        self.users.create(user("root", AdminRole.OWNER))
        self.refresh_executor = InlineExecutor() if executor == "inline" else executor
        self.application = build_application(
            make_settings(
                tmp_path / "clients",
                admin_dir=tmp_path / "admin",
                config_dir=tmp_path / "config",
                profiles_dir=tmp_path / "profiles",
                secrets_dir=tmp_path / "secrets",
            ),
            env=LEGACY_ONLY if env is None else env,
            builders=self.builders,
            clock=self.clock,
            executor=InlineExecutor(),
            admin_user_store=self.users,
            admin_session_store=InMemoryAdminSessionStore(),
            secret_store=self.store,
            secret_audit=InMemorySecretAudit(),
            refresh_executor=self.refresh_executor,
            agent_state_store=InMemoryAgentStateStore(),
        )
        for cid in ids:  # every client has started its agent: these tests are about credentials
            self.application.agent_state.set(cid, running=True, by="tests", at=NOW)
        self.registry = self.application.registry
        self.manager = self.application.runtime_manager
        self.credentials = self.application.credentials
        self.client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )

    def save(self, scope, key, value):
        return self.credentials.save(scope, key, value, actor="root")

    def make_ready(self, cid="alpha", token="token-1", phone=None, openai="sk-store-key-1"):
        self.save(GLOBAL_SCOPE, "openai.api_key", openai)
        self.save(GLOBAL_SCOPE, "whatsapp.app_secret", app_secret(cid))
        self.save(client_scope(cid), "whatsapp.access_token", token)
        self.save(client_scope(cid), "whatsapp.phone_number_id", phone or PHONES[cid])

    def login(self):
        self.client.post("/admin/v1/auth/login", json={"username": "root", "password": PASSWORD})
        return self.client

    def inbound(self, cid="alpha", phone=None, secret=None, wamid="wamid.1"):
        phone = phone or self.phone_of(cid)
        body = payload(phone, phone_for("p1").lstrip("+"), "Hi there", wamid)
        return post(self.client, body, secret=secret or app_secret(cid))

    def phone_of(self, cid):
        return self.registry.get_config(cid).phone_number_id

    def settle(self):
        """Wait until the refresh worker has finished everything queued so far."""
        if isinstance(self.refresh_executor, ThreadPoolExecutor):
            self.refresh_executor.submit(lambda: None).result(10)

    def close(self):
        self.application.close()


@pytest.fixture
def world(tmp_path):
    made: list[World] = []

    def make(*ids, **kw):
        w = World(tmp_path, *ids, **kw)
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()


# ---- resolution --------------------------------------------------------------------------------


def load(tmp_path, store, env, *ids, ceiling=ApprovalMode.AUTONOMOUS):
    for cid in ids:
        write_client(tmp_path, cid)
    return load_client_configs(tmp_path, env=env, mode_ceiling=ceiling, secret_store=store)


class TestResolution:
    def test_the_runtime_asks_for_the_stores_own_keys(self):
        in_catalog = {spec.key for spec in CATALOG}
        for name in (
            "OPENAI_API_KEY",
            "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID",
            "WHATSAPP_APP_SECRET",
            "WHATSAPP_API_VERSION",
            "WHATSAPP_VERIFY_TOKEN",
        ):
            assert getattr(secret_keys, name) in in_catalog

    def test_the_store_alone_is_enough(self, tmp_path):
        store = InMemorySecretStore()
        store.set(GLOBAL_SCOPE, "openai.api_key", "sk-store", by="r")
        store.set(GLOBAL_SCOPE, "whatsapp.app_secret", "app-store", by="r")
        store.set(client_scope("alpha"), "whatsapp.access_token", "tok-store", by="r")
        store.set(client_scope("alpha"), "whatsapp.phone_number_id", "1234567", by="r")
        (config,) = load(tmp_path, store, LEGACY_ONLY, "alpha").clients
        assert config.ready
        assert config.secrets.get("openai.api_key") == "sk-store"
        assert config.secrets.get("whatsapp.access_token") == "tok-store"
        assert config.phone_number_id == "1234567"

    def test_the_store_wins_over_the_environment(self, tmp_path):
        store = InMemorySecretStore()
        store.set(client_scope("alpha"), "whatsapp.access_token", "tok-store", by="r")
        (config,) = load(tmp_path, store, env_for("alpha"), "alpha").clients
        assert config.secrets.get("whatsapp.access_token") == "tok-store"
        assert config.secrets.get("openai.api_key") == "sk-test"  # nothing stored: environment

    def test_the_environment_alone_still_works(self, tmp_path):
        (config,) = load(tmp_path, InMemorySecretStore(), env_for("alpha"), "alpha").clients
        assert config.ready and config.secrets.get("whatsapp.access_token") == "token-alpha"

    def test_client_credentials_never_cross_clients(self, tmp_path):
        store = InMemorySecretStore()
        store.set(client_scope("alpha"), "whatsapp.access_token", "tok-alpha-store", by="r")
        loaded = load(tmp_path, store, env_for("alpha", "beta"), "alpha", "beta")
        by_id = {c.client_id: c for c in loaded.clients}
        assert by_id["alpha"].secrets.get("whatsapp.access_token") == "tok-alpha-store"
        assert by_id["beta"].secrets.get("whatsapp.access_token") == "token-beta"

    def test_global_credentials_apply_to_every_client(self, tmp_path):
        store = InMemorySecretStore()
        store.set(GLOBAL_SCOPE, "openai.api_key", "sk-shared", by="r")
        loaded = load(tmp_path, store, env_for("alpha", "beta"), "alpha", "beta")
        assert {c.secrets.get("openai.api_key") for c in loaded.clients} == {"sk-shared"}

    def test_the_api_version_is_global_with_the_files_value_as_default(self, tmp_path):
        store = InMemorySecretStore()
        (config,) = load(tmp_path, store, env_for("alpha"), "alpha").clients
        assert config.secrets.get("whatsapp.api_version") == "v21.0"
        store.set(GLOBAL_SCOPE, "whatsapp.api_version", "v23.0", by="r")
        (config,) = load(tmp_path, store, env_for("alpha"), "alpha").clients
        assert config.secrets.get("whatsapp.api_version") == "v23.0"

    def test_a_dry_run_client_does_not_need_whatsapp_credentials(self, tmp_path):
        store = InMemorySecretStore()
        store.set(GLOBAL_SCOPE, "openai.api_key", "sk", by="r")
        (config,) = load(
            tmp_path, store, LEGACY_ONLY, "alpha", ceiling=ApprovalMode.DRY_RUN
        ).clients
        assert config.ready

    def test_an_unusable_store_is_an_error_not_a_missing_credential(self, tmp_path):
        class Broken(InMemorySecretStore):
            def get(self, scope, key):
                raise SecretStoreCorruptError("secrets.json: unreadable")

        loaded = load(tmp_path, Broken(), env_for("alpha"), "alpha")
        assert loaded.clients == () and any("credential store" in e for e in loaded.errors)


# ---- startup -----------------------------------------------------------------------------------


class TestTolerantStartup:
    def test_a_client_without_credentials_does_not_stop_the_application(self, world):
        w = world("alpha")
        assert w.registry.runtimes() == [] and w.registry.get("alpha") is None
        (entry,) = w.registry.health()
        assert entry["status"] == "not_ready" and entry["client_id"] == "alpha"
        assert "OpenAI API key is not configured" in entry["missing_credentials"]
        assert w.client.get("/health").status_code == 200

    def test_ready_and_not_ready_clients_live_side_by_side(self, world, tmp_path):
        w = world("alpha", "beta", env=env_for("alpha"))
        # alpha's own variables are set, beta's are not; the shared ones are, so only beta waits
        assert [r.client_id for r in w.registry.runtimes()] == ["alpha"]
        assert w.registry.missing("beta") and not w.registry.missing("alpha")

    def test_the_health_endpoint_shows_what_is_missing_and_no_secret(self, world):
        w = world("alpha")
        text = w.client.get("/health/clients").text
        assert "not_ready" in text and "missing credentials" in text

    def test_a_structural_error_still_blocks_startup(self, tmp_path):
        write_client(tmp_path / "clients", "alpha", raw="client_id: alpha\nbogus: 1\n")
        with pytest.raises(ConfigurationError):
            build_application(
                make_settings(tmp_path / "clients", secrets_dir=tmp_path / "secrets"),
                env={},
                builders=fake_builders()[0],
                clock=FakeClock(NOW),
                executor=InlineExecutor(),
                secret_store=InMemorySecretStore(),
            )

    def test_a_not_ready_client_is_never_run_by_the_scheduler(self, world):
        w = world("alpha")
        assert w.application.scheduler.tick() == []

    def test_the_cli_validate_names_the_missing_credentials(self, tmp_path, monkeypatch, capsys):
        for name in (
            "OPENAI_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "ALPHA_TOKEN",
            "ALPHA_APP_SECRET",
            "ALPHA_PHONE_ID",
        ):
            monkeypatch.delenv(name, raising=False)
        write_client(tmp_path / "clients", "alpha")
        assert _validate(make_settings(tmp_path / "clients", secrets_dir=tmp_path / "s")) == 0
        out = capsys.readouterr().out
        assert "NOT READY  alpha" in out and "OpenAI API key" in out
        assert "0 client(s) valid, 1 not ready" in out


# ---- applying a saved credential ---------------------------------------------------------------


class TestBecomingReady:
    def test_saving_the_last_missing_credential_starts_the_client(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        assert runtime is not None and not runtime.suspended
        assert w.registry.runtimes() == [runtime] and w.registry.missing("alpha") == ()
        assert w.manager.status("alpha").state == "applied"
        assert w.flags.tokens[-1] == "token-1"

    def test_the_scheduler_then_runs_it(self, world):
        w = world("alpha")
        assert w.application.scheduler.tick() == []
        w.make_ready()
        assert len(w.application.scheduler.tick()) == 1

    def test_inbound_messages_are_routed_by_the_stored_phone_number_id(self, world):
        w = world("alpha")
        w.make_ready()
        w.made.repositories["alpha"].add_prospects(prospect("p1", client_id=ClientId("alpha")))
        response = w.inbound("alpha")
        assert response.status_code == 200 and response.json() == {"received": 1}

    def test_a_partial_set_keeps_it_not_ready_and_says_what_is_left(self, world):
        w = world("alpha")
        w.save(GLOBAL_SCOPE, "openai.api_key", "sk-1")
        status = w.manager.status("alpha")
        assert status.state == "not_ready"
        assert "WhatsApp access token is not configured" in status.errors
        assert not any("OpenAI" in e for e in status.errors)


class TestReplacingACredential:
    def test_the_new_value_is_used_and_state_and_health_survive(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        old_engine, state, health = runtime.engine, runtime.state, runtime.health
        health.record_success("daily", NOW)

        w.save(client_scope("alpha"), "whatsapp.access_token", "token-2")

        assert w.registry.get("alpha") is runtime  # the same runtime object
        assert runtime.engine is not old_engine
        assert runtime.state is state and runtime.health is health
        assert runtime.health.snapshot()["last_runs"] == {"daily": NOW.isoformat()}
        assert runtime.config.secrets.get("whatsapp.access_token") == "token-2"
        assert w.flags.tokens[-1] == "token-2" and w.manager.status("alpha").state == "applied"

    def test_a_global_change_is_applied_to_every_client(self, world):
        w = world("alpha", "beta")
        w.make_ready("alpha")
        w.make_ready("beta", token="token-b", phone="2000000002")
        before = {c: w.registry.get(c).engine for c in ("alpha", "beta")}
        w.save(GLOBAL_SCOPE, "openai.api_key", "sk-rotated")
        for cid in ("alpha", "beta"):
            runtime = w.registry.get(cid)
            assert runtime.engine is not before[cid]
            assert runtime.config.secrets.get("openai.api_key") == "sk-rotated"

    def test_a_changed_phone_number_id_moves_the_routing(self, world):
        w = world("alpha")
        w.make_ready(phone="1000000001")
        old = w.phone_of("alpha")
        w.save(client_scope("alpha"), "whatsapp.phone_number_id", "1000000009")
        assert w.registry.by_routing_key("whatsapp", "1000000009").client_id == "alpha"
        assert w.registry.by_routing_key("whatsapp", old) is None

    def test_the_app_secret_change_applies_to_signature_checks(self, world):
        w = world("alpha")
        w.make_ready()
        w.made.repositories["alpha"].add_prospects(prospect("p1", client_id=ClientId("alpha")))
        assert w.inbound("alpha").status_code == 200
        w.save(GLOBAL_SCOPE, "whatsapp.app_secret", "rotated-app-secret")
        assert w.inbound("alpha", secret=app_secret("alpha"), wamid="w2").status_code == 401
        assert w.inbound("alpha", secret="rotated-app-secret", wamid="w3").status_code == 200

    def test_the_verify_token_change_applies_to_the_handshake(self, world):
        w = world("alpha")
        w.make_ready()
        params = {"hub.mode": "subscribe", "hub.challenge": "42"}
        assert (
            w.client.get(
                "/webhooks/whatsapp", params={**params, "hub.verify_token": VERIFY_TOKEN}
            ).status_code
            == 200
        )
        w.save(GLOBAL_SCOPE, "whatsapp.verify_token", "new-verify-token")
        assert (
            w.client.get(
                "/webhooks/whatsapp", params={**params, "hub.verify_token": VERIFY_TOKEN}
            ).status_code
            == 403
        )
        assert (
            w.client.get(
                "/webhooks/whatsapp", params={**params, "hub.verify_token": "new-verify-token"}
            ).text
            == "42"
        )

    def test_the_default_builders_read_the_stored_verify_token_when_parsers_are_built(self):
        from sales_app.settings import Settings

        store = InMemorySecretStore()
        builders = default_builders(
            Settings(whatsapp_verify_token="from-env"),
            verify_token=lambda: store.get(GLOBAL_SCOPE, "whatsapp.verify_token"),
        )
        build = builders.channels["whatsapp"].build_parser
        query = {"hub.mode": "subscribe", "hub.challenge": "1"}
        assert build({}).handshake({**query, "hub.verify_token": "from-env"}) == "1"
        store.set(GLOBAL_SCOPE, "whatsapp.verify_token", "from-store", by="r")
        assert build({}).handshake({**query, "hub.verify_token": "from-store"}) == "1"
        assert build({}).handshake({**query, "hub.verify_token": "from-env"}) is None


class TestAFailedChangeKeepsTheOldRuntime:
    def test_the_old_runtime_still_works_and_the_reason_is_kept_without_the_secret(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        old_engine = runtime.engine
        w.flags.fail_channel = True

        w.save(client_scope("alpha"), "whatsapp.access_token", "token-BAD-0123456789")

        status = w.manager.status("alpha")
        assert status.state == "failed" and status.errors
        assert "token-BAD-0123456789" not in " ".join(status.errors)  # redacted
        assert "***" in " ".join(status.errors)
        assert runtime.engine is old_engine and not runtime.suspended
        with runtime.lease() as engine:
            assert engine is old_engine
        assert len(w.application.scheduler.tick()) == 1  # and it keeps running

    def test_a_later_fix_is_applied(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        old_engine = runtime.engine
        w.flags.fail_channel = True
        w.save(client_scope("alpha"), "whatsapp.access_token", "token-BAD-0123456789")
        w.flags.fail_channel = False
        w.save(client_scope("alpha"), "whatsapp.access_token", "token-good-0123456789")
        assert w.manager.status("alpha").state == "applied"
        assert runtime.engine is not old_engine
        assert runtime.config.secrets.get("whatsapp.access_token") == "token-good-0123456789"

    def test_a_phone_number_id_held_by_another_client_is_refused_by_the_refresh_too(self, world):
        w = world("alpha", "beta")
        w.make_ready("alpha", phone="1000000001")
        w.make_ready("beta", token="tb", phone="2000000002")
        beta = w.registry.get("beta")
        before = beta.engine
        w.store.set(
            client_scope("beta"), "whatsapp.phone_number_id", "1000000001", by="x"
        )  # bypass
        w.manager.refresh_client("beta")
        status = w.manager.status("beta")
        assert status.state == "failed" and "already used by client 'alpha'" in status.errors[0]
        assert beta.engine is before and w.phone_of("beta") == "2000000002"


def _models(*entries):
    return json.dumps([{"name": n, "id": i, "active": a} for n, i, a in entries])


class TestAModelDeactivatedInSettings:
    """See test_llm_model_activation for the resolution rules."""

    def test_deactivating_the_model_stops_the_client_and_reactivating_resumes_it(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        assert w.registry.runtimes() == [runtime]

        w.save(GLOBAL_SCOPE, "openai.models", _models(("M", "test-model", False)))
        assert runtime.suspended and w.registry.runtimes() == []
        assert w.manager.status("alpha").state == "not_ready"
        assert w.registry.health()[0]["status"] == "not_ready"
        assert w.application.scheduler.tick() == []

        w.save(GLOBAL_SCOPE, "openai.models", _models(("M", "test-model", True)))
        assert w.registry.get("alpha") is runtime and not runtime.suspended

    @pytest.mark.parametrize("models", [None, _models(("M", "test-model", True))])
    def test_a_listed_or_unlisted_model_keeps_running(self, world, models):
        w = world("alpha")
        w.make_ready()
        if models is not None:
            w.save(GLOBAL_SCOPE, "openai.models", models)
        assert len(w.registry.runtimes()) == 1


class TestRemovingACredential:
    def test_the_client_becomes_not_ready_and_stops_taking_work(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        w.credentials.remove(client_scope("alpha"), "whatsapp.access_token", actor="root")

        assert runtime.suspended and w.registry.runtimes() == []
        assert w.manager.status("alpha").state == "not_ready"
        assert w.registry.health()[0]["status"] == "not_ready"
        assert w.application.scheduler.tick() == []

    def test_restoring_it_resumes_the_same_runtime(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        state, health = runtime.state, runtime.health
        w.credentials.remove(client_scope("alpha"), "whatsapp.access_token", actor="root")
        w.save(client_scope("alpha"), "whatsapp.access_token", "token-again")
        assert w.registry.get("alpha") is runtime and not runtime.suspended
        assert runtime.state is state and runtime.health is health
        assert w.registry.runtimes() == [runtime]

    def test_work_already_running_finishes_after_the_credential_is_removed(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        with runtime.lease() as engine:
            w.credentials.remove(client_scope("alpha"), "whatsapp.access_token", actor="root")
            assert engine is runtime.engine  # still the one it started with
        with pytest.raises(Exception, match="not ready"), runtime.lease():
            pass


class TestInboundForAClientThatIsNotReady:
    def test_it_is_answered_200_dropped_counted_and_logged(self, world, caplog):
        w = world("alpha")
        w.save(GLOBAL_SCOPE, "whatsapp.app_secret", app_secret("alpha"))
        w.save(client_scope("alpha"), "whatsapp.phone_number_id", "1000000001")  # no token yet
        assert w.registry.missing("alpha")
        with caplog.at_level("ERROR"):
            response = w.inbound("alpha", phone="1000000001")
        assert response.status_code == 200 and response.json() == {"received": 0}
        assert "not ready" in caplog.text
        entry = w.registry.health()[0]
        assert entry["dropped_inbound"] == 1 and "last_dropped_inbound_at" in entry

    def test_it_is_still_authenticated_before_being_dropped(self, world):
        w = world("alpha")
        w.save(GLOBAL_SCOPE, "whatsapp.app_secret", app_secret("alpha"))
        w.save(client_scope("alpha"), "whatsapp.phone_number_id", "1000000001")
        assert w.inbound("alpha", phone="1000000001", secret="wrong").status_code == 401

    def test_a_client_removed_mid_request_is_dropped_not_a_500(self, world):
        w = world("alpha")
        w.make_ready()
        runtime = w.registry.get("alpha")
        original = w.registry.by_routing_key

        def stale_lookup(provider, key):  # the lookup sees a ready client, then it lost one
            found = original(provider, key)
            w.credentials.remove(client_scope("alpha"), "whatsapp.access_token", actor="root")
            return found

        w.registry.by_routing_key = stale_lookup
        response = w.inbound("alpha")
        assert response.status_code == 200 and response.json() == {"received": 0}
        assert runtime.suspended


# ---- waiting for running work ------------------------------------------------------------------


class TestPendingSwap:
    def test_a_swap_waits_for_running_work_and_is_installed_when_it_ends(self, world):
        pool = ThreadPoolExecutor(1)
        w = world("alpha", executor=pool)
        w.manager._wait = 0.3  # a short grace for the test; production uses APPLY_WAIT_SECONDS
        w.make_ready()
        pool.shutdown(wait=True)  # let the first refreshes finish
        w.manager._executor = pool = ThreadPoolExecutor(1)

        runtime = w.registry.get("alpha")
        old_engine = runtime.engine
        started, release = threading.Event(), threading.Event()
        used = []

        def job():
            with runtime.lease() as engine:
                started.set()
                release.wait(10)
                used.append(engine)

        thread = threading.Thread(target=job)
        thread.start()
        assert started.wait(5)

        w.store.set(client_scope("alpha"), "whatsapp.access_token", "token-new", by="root")
        future = w.manager.refresh_client("alpha")
        future.result(10)  # the grace period passes while the job is still running

        assert w.manager.status("alpha").state == "pending"
        assert runtime.engine is old_engine and runtime.replacement_pending
        with runtime.lease() as engine:  # new work is no longer held back
            assert engine is old_engine

        release.set()
        thread.join(5)
        assert used == [old_engine]  # it finished on the Engine it started with
        assert runtime.engine is not old_engine and not runtime.replacement_pending
        assert w.manager.status("alpha").state == "applied"
        pool.shutdown(wait=True)

    def test_saving_returns_without_waiting_for_the_swap(self, world):
        pool = ThreadPoolExecutor(1)
        w = world("alpha", executor=pool)
        w.manager._wait = 5.0
        w.make_ready()
        w.settle()
        runtime = w.registry.get("alpha")
        started, release = threading.Event(), threading.Event()

        def job():
            with runtime.lease():
                started.set()
                release.wait(10)

        thread = threading.Thread(target=job)
        thread.start()
        assert started.wait(5)

        began = time.monotonic()
        w.save(client_scope("alpha"), "whatsapp.access_token", "token-new")  # does not block
        assert time.monotonic() - began < 2.0
        deadline = time.monotonic() + 5
        while w.manager.status("alpha").state not in {"applying", "pending"}:
            assert time.monotonic() < deadline, "the refresh never started"
            time.sleep(0.01)

        release.set()
        thread.join(5)
        pool.shutdown(wait=True)
        assert w.manager.status("alpha").state == "applied"


# ---- the Admin API -----------------------------------------------------------------------------


class TestAdminApi:
    def test_saving_through_the_api_applies_it_and_the_status_endpoint_says_so(self, world):
        w = world("alpha")
        client = w.login()
        client.put("/admin/v1/settings/integrations/openai.api_key", json={"value": "sk-1"})
        client.put("/admin/v1/settings/integrations/whatsapp.app_secret", json={"value": "app-1"})
        client.put(
            "/admin/v1/clients/alpha/integrations/whatsapp.access_token", json={"value": "t-1"}
        )
        status = client.get("/admin/v1/clients/alpha/runtime-status").json()
        assert status["readiness"] == "not_ready"
        assert status["missing_credentials"] == ["WhatsApp phone number id is not configured"]
        client.put(
            "/admin/v1/clients/alpha/integrations/whatsapp.phone_number_id",
            json={"value": "1000000001"},
        )
        status = client.get("/admin/v1/clients/alpha/runtime-status").json()
        assert status["readiness"] == "ready" and status["apply_state"] == "applied"
        assert status["missing_credentials"] == [] and status["apply_errors"] == []

    def test_a_failure_is_shown_in_the_status_without_the_secret(self, world):
        w = world("alpha")
        w.make_ready()
        w.flags.fail_channel = True
        client = w.login()
        client.put(
            "/admin/v1/clients/alpha/integrations/whatsapp.access_token",
            json={"value": "token-BAD-0123456789"},
        )
        response = client.get("/admin/v1/clients/alpha/runtime-status")
        assert response.json()["apply_state"] == "failed"
        assert "token-BAD-0123456789" not in response.text

    def test_the_list_endpoint_and_scoping(self, world):
        w = world("alpha", "beta")
        w.users.create(user("vera", AdminRole.VIEWER, frozenset({"alpha"})))
        client = w.login()
        listed = client.get("/admin/v1/settings/runtime-status").json()
        assert [c["client_id"] for c in listed] == ["alpha", "beta"]
        client.post("/admin/v1/auth/logout")
        client.post("/admin/v1/auth/login", json={"username": "vera", "password": PASSWORD})
        assert [c["client_id"] for c in client.get("/admin/v1/settings/runtime-status").json()] == [
            "alpha"
        ]
        assert client.get("/admin/v1/clients/beta/runtime-status").status_code == 404

    def test_unknown_client_and_anonymous(self, world):
        w = world("alpha")
        assert w.client.get("/admin/v1/clients/alpha/runtime-status").status_code == 401
        assert w.login().get("/admin/v1/clients/ghost/runtime-status").status_code == 404

    def test_a_not_ready_client_is_listed_and_has_a_detail_page(self, world):
        w = world("alpha")
        client = w.login()
        (summary,) = client.get("/admin/v1/clients").json()
        assert summary["client_id"] == "alpha" and summary["status"] == "active"
        assert summary["health"]["status"] == "not_ready"
        assert "missing credentials" in summary["health"]["last_error"]
        detail = client.get("/admin/v1/clients/alpha")
        assert detail.status_code == 200 and detail.json()["health"]["status"] == "not_ready"
        assert client.get("/admin/v1/agents/alpha").status_code == 200

    def test_a_new_client_gets_no_credential_names_in_its_file(self, world, tmp_path):
        import yaml

        from sales_app.api.admin_clients import _new_client_yaml

        raw = _new_client_yaml("gamma", "Gamma", "openai", "m")
        assert raw["whatsapp"] == {"provider": "whatsapp"}
        assert "TOKEN" not in yaml.safe_dump(raw)


class TestRegistryBehaviour:
    def test_runtimes_excludes_not_ready_but_configs_lists_everyone(self, world):
        w = world("alpha", "beta", env=env_for("alpha"))
        assert [r.client_id for r in w.registry.runtimes()] == ["alpha"]
        assert {c.client_id for c in w.registry.configs()} == {"alpha", "beta"}

    def test_routing_is_derived_from_the_current_config(self, world):
        w = world("alpha", env=env_for("alpha"))
        assert w.registry.by_routing_key("whatsapp", phone_id("alpha")).client_id == "alpha"
        assert w.registry.by_routing_key("sms", phone_id("alpha")) is None
        assert w.registry.by_routing_key("whatsapp", "unknown") is None
        assert w.registry.not_ready_client_for("whatsapp", phone_id("alpha")) is None

    def test_the_old_constructor_form_still_works(self):
        registry = ClientRegistry([], {}, ["gamma"])
        assert registry.health() == [{"client_id": "gamma", "status": "disabled"}]
        assert registry.by_routing_key("whatsapp", "x") is None

    def test_provider_wiring_is_untouched_by_the_not_ready_path(self, world):
        w = world("alpha")
        assert isinstance(w.builders.channels["whatsapp"], ChannelProvider)
