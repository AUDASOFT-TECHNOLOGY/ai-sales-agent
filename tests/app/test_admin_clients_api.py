"""`/admin/v1/clients` and `/admin/v1/clients/{client_id}`: read-only client configuration.

Covers: authentication required, per-client scoping (`AdminUser.can_reach`), an active client
(from the validated `ClientRegistry`), a disabled client (from raw `client.yaml`, never validated
-- see `sales_app.api.admin_clients`), an unknown client, and that no response ever carries a
secret value, only the names of the environment variables that hold one.
"""

from datetime import UTC, datetime

import pytest
from app_support import InlineExecutor, env_for, fake_builders, make_settings, write_client
from fastapi.testclient import TestClient

from sales_app.admin_auth import (
    AdminRole,
    AdminUser,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.client_profiles import InMemoryClientProfileStore
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"


def build(tmp_path, *client_ids, users=None, sessions=None, clock=None, **settings_kw):
    clock = clock or FakeClock(NOW)
    for cid in client_ids:
        write_client(tmp_path, cid)
    builders, made = fake_builders(clock)
    application = build_application(
        make_settings(tmp_path, **settings_kw),
        env=env_for(*client_ids),
        builders=builders,
        clock=clock,
        executor=InlineExecutor(),
        admin_user_store=users if users is not None else InMemoryAdminUserStore(),
        admin_session_store=sessions if sessions is not None else InMemoryAdminSessionStore(),
        client_profile_store=InMemoryClientProfileStore(),
    )
    return application, made


def editor(username="alice", client_ids=frozenset({"alpha"})) -> AdminUser:
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=AdminRole.EDITOR,
        client_ids=client_ids,
        created_at=NOW,
    )


def owner(username="root") -> AdminUser:
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=AdminRole.OWNER,
        created_at=NOW,
    )


class Fixture:
    def __init__(self, tmp_path, *client_ids, users=None, clock=None, **settings_kw):
        self.users = users if users is not None else InMemoryAdminUserStore()
        self.sessions = InMemoryAdminSessionStore()
        self.clock = clock or FakeClock(NOW)
        self.application, self.made = build(
            tmp_path,
            *client_ids,
            users=self.users,
            sessions=self.sessions,
            clock=self.clock,
            **settings_kw,
        )
        self.client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )

    def login(self, username="alice", password=PASSWORD):
        return self.client.post(
            "/admin/v1/auth/login", json={"username": username, "password": password}
        )


@pytest.fixture
def fx(tmp_path):
    # "naga" mirrors a real, disabled client directory: present on disk, excluded from the
    # registry, and must still be reachable (see resolve_client_config's "disabled: not
    # validated, not built" rule). Written before the app boots, like alpha and beta.
    write_client(
        tmp_path,
        "naga",
        enabled=False,
        display_name="Naga",
        mode="approval",
        timezone="Africa/Tunis",
        daily_action_limit=15,
    )
    # Present on disk but skipped entirely by discovery (leading "_", see loader.py) -- must
    # never be reachable through the API either, even by an owner guessing the name.
    write_client(tmp_path, "_template", enabled=False)
    fixture = Fixture(tmp_path, "alpha", "beta")
    fixture.users.create(editor(client_ids=frozenset({"alpha", "naga"})))
    fixture.users.create(owner())
    return fixture


@pytest.fixture
def fx_secrets(tmp_path):
    """Like `fx`, with an enabled credential store (key + directory) to hold Meta credentials."""
    from sales_app.secret_store import generate_master_key

    fixture = Fixture(
        tmp_path,
        "alpha",
        secrets_dir=tmp_path / "_secrets",
        secrets_key=generate_master_key(),
    )
    fixture.users.create(owner())
    return fixture


def as_alice(fx):
    fx.login()
    return fx.client


def as_root(fx):
    fx.login("root")
    return fx.client


class TestListRequiresAuth:
    def test_anonymous_cannot_list_clients(self, fx):
        assert fx.client.get("/admin/v1/clients").status_code == 401

    def test_anonymous_cannot_read_a_client(self, fx):
        assert fx.client.get("/admin/v1/clients/alpha").status_code == 401


class TestListScoping:
    def test_owner_sees_every_client_including_disabled(self, fx):
        client = as_root(fx)
        body = client.get("/admin/v1/clients").json()
        assert sorted(c["client_id"] for c in body) == ["alpha", "beta", "naga"]

    def test_scoped_editor_sees_only_its_own_clients(self, fx):
        client = as_alice(fx)
        body = client.get("/admin/v1/clients").json()
        assert sorted(c["client_id"] for c in body) == ["alpha", "naga"]  # not "beta"

    def test_an_editor_with_no_scope_sees_nothing(self, tmp_path):
        fixture = Fixture(tmp_path, "alpha")
        fixture.users.create(editor(client_ids=frozenset()))
        fixture.login()
        assert fixture.client.get("/admin/v1/clients").json() == []


class TestActiveClientSummaryAndDetail:
    def test_summary_fields(self, fx):
        client = as_root(fx)
        body = client.get("/admin/v1/clients").json()
        alpha = next(c for c in body if c["client_id"] == "alpha")
        assert alpha["display_name"] == "Alpha"
        assert alpha["status"] == "active"
        assert alpha["mode"] == "approval"
        assert alpha["timezone"] == "Africa/Tunis"
        assert alpha["daily_action_limit"] == 20
        assert alpha["providers"] == {
            "sheets": "google_sheets",
            "llm": "openai",
            "whatsapp": "whatsapp",
        }
        assert alpha["llm_model"] == "test-model"

    def test_detail_has_configuration_but_no_secret_values(self, fx):
        client = as_root(fx)
        response = client.get("/admin/v1/clients/alpha")
        assert response.status_code == 200
        body = response.json()
        assert body["client_id"] == "alpha"
        assert body["sheets"]["spreadsheet_id"] == "sheet-alpha"
        assert body["sheets"]["credentials_env"] == "GOOGLE_APPLICATION_CREDENTIALS"
        assert body["llm"]["api_key_env"] == "OPENAI_API_KEY"
        assert body["whatsapp"]["token_env"] == "ALPHA_TOKEN"
        # The names of the env vars are shown; the secret values that live in them never appear.
        payload = response.text
        assert "sk-test" not in payload  # OPENAI_API_KEY value
        assert "token-alpha" not in payload  # ALPHA_TOKEN value
        assert "secret-alpha" not in payload  # ALPHA_APP_SECRET value
        assert "phone-alpha" not in payload  # ALPHA_PHONE_ID value (resolved phone_number_id)

    def test_a_scoped_editor_can_read_a_client_in_its_scope(self, fx):
        client = as_alice(fx)
        assert client.get("/admin/v1/clients/alpha").status_code == 200

    def test_a_scoped_editor_gets_404_not_403_outside_its_scope(self, fx):
        client = as_alice(fx)
        assert client.get("/admin/v1/clients/beta").status_code == 404


class TestDisabledClient:
    def test_naga_appears_in_the_list_as_disabled(self, fx):
        client = as_root(fx)
        body = client.get("/admin/v1/clients").json()
        naga = next(c for c in body if c["client_id"] == "naga")
        assert naga["status"] == "disabled"
        assert naga["display_name"] == "Naga"
        assert naga["mode"] == "approval"
        assert naga["timezone"] == "Africa/Tunis"
        assert naga["daily_action_limit"] == 15
        assert naga["llm_model"] == "test-model"

    def test_naga_detail_is_readable_and_marked_disabled(self, fx):
        client = as_root(fx)
        response = client.get("/admin/v1/clients/naga")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "disabled"
        assert body["client_id"] == "naga"


class TestClientHealth:
    def test_active_client_reports_the_registry_health(self, fx):
        body = as_root(fx).get("/admin/v1/clients").json()
        alpha = next(c for c in body if c["client_id"] == "alpha")
        assert alpha["health"]["status"] == "ready"
        assert alpha["health"]["consecutive_failures"] == 0
        assert alpha["health"]["last_error"] is None

    def test_degraded_client_surfaces_its_last_error(self, fx):
        fx.application.registry.get("alpha").health.record_failure("dispatch", NOW, "boom")
        alpha = as_root(fx).get("/admin/v1/clients/alpha").json()
        assert alpha["health"]["status"] == "degraded"
        assert alpha["health"]["last_error"] == "dispatch: boom"
        assert alpha["health"]["consecutive_failures"] == 1

    def test_disabled_client_has_no_health(self, fx):
        body = as_root(fx).get("/admin/v1/clients").json()
        assert next(c for c in body if c["client_id"] == "naga")["health"] is None


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=="


def new_client(**overrides):
    body = {
        "display_name": "Acme Corp",
        "login": "Acme.Admin",
        "temporary_password": "temp-pass-123",
        "llm_provider": "openai",
        "llm_model": "gpt-4o",
        "status": "inactive",
        "whatsapp_number": "+216 22 768 307",
    }
    return body | overrides


class TestCreateClient:
    def test_owner_creates_an_inactive_client_that_is_listed_and_readable(self, fx, tmp_path):
        client = as_root(fx)
        response = client.post("/admin/v1/clients", json=new_client(logo=PNG))
        assert response.status_code == 201
        body = response.json()
        assert body["client_id"] == "acme-corp"
        assert body["display_name"] == "Acme Corp"
        assert body["status"] == "disabled"
        assert body["llm_model"] == "gpt-4o"
        assert (tmp_path / "acme-corp" / "client.yaml").is_file()

        listed = client.get("/admin/v1/clients").json()
        assert "acme-corp" in [c["client_id"] for c in listed]
        detail = client.get("/admin/v1/clients/acme-corp").json()
        assert detail["account"] == {
            "login": "acme.admin",
            "whatsapp_number": "+216 22 768 307",
            "logo": PNG,
            "must_change_password": True,
        }
        assert detail["revenue"] is None
        assert detail["prospects_contacted"] is None

    def test_the_password_is_never_stored_or_returned_in_clear(self, fx, tmp_path):
        client = as_root(fx)
        response = client.post("/admin/v1/clients", json=new_client())
        profile = fx.application.client_profiles.get("acme-corp")
        assert profile.password_hash.startswith("$2")  # bcrypt
        assert "temp-pass-123" not in profile.password_hash
        assert "temp-pass-123" not in response.text
        assert "temp-pass-123" not in client.get("/admin/v1/clients/acme-corp").text
        assert "temp-pass-123" not in (tmp_path / "acme-corp" / "client.yaml").read_text()
        assert "acme.admin" not in (tmp_path / "acme-corp" / "client.yaml").read_text()

    def test_the_whatsapp_number_is_persisted_in_the_profile_only(self, fx, tmp_path):
        as_root(fx).post(
            "/admin/v1/clients", json=new_client(whatsapp_number="  +216   99 697 335 ")
        )
        assert fx.application.client_profiles.get("acme-corp").whatsapp_number == "+216 99 697 335"
        yaml_text = (tmp_path / "acme-corp" / "client.yaml").read_text()
        assert "216" not in yaml_text
        assert "phone_number_id" not in yaml_text

    def test_the_whatsapp_number_is_not_a_phone_number_id(self, fx):
        client = as_root(fx)
        client.post("/admin/v1/clients", json=new_client())
        creds = fx.application.credentials
        # Creating the client stored nothing in the secret store: the Meta id is a separate thing.
        assert creds.reveal("client:acme-corp", "whatsapp.phone_number_id") is None
        assert creds.reveal("client:acme-corp", "whatsapp.access_token") is None
        detail = client.get("/admin/v1/clients/acme-corp").json()
        assert detail["account"]["whatsapp_number"] == "+216 22 768 307"

    def test_a_new_client_is_not_connected_to_meta(self, fx):
        client = as_root(fx)
        client.post("/admin/v1/clients", json=new_client())
        detail = client.get("/admin/v1/clients/acme-corp").json()
        assert detail["meta"] == {"status": "not_connected", "credentials_stored": False}

    def test_stored_meta_credentials_are_reported_as_stored_never_connected_and_never_shown(
        self, fx_secrets
    ):
        fx = fx_secrets
        client = as_root(fx)
        client.post("/admin/v1/clients", json=new_client())
        creds = fx.application.credentials
        creds.save("client:acme-corp", "whatsapp.phone_number_id", "123456789012", actor="test")
        creds.save("client:acme-corp", "whatsapp.access_token", "EAAB-secret-token", actor="test")
        response = client.get("/admin/v1/clients/acme-corp")
        assert response.json()["meta"] == {
            "status": "credentials_stored",
            "credentials_stored": True,
        }
        assert "connected" not in response.json()["meta"]["status"].replace("not_connected", "")
        for leaked in ("123456789012", "EAAB-secret-token"):
            assert leaked not in response.text

    def test_the_credentials_of_one_client_are_not_another_clients(self, fx_secrets):
        fx = fx_secrets
        client = as_root(fx)
        client.post("/admin/v1/clients", json=new_client())
        client.post(
            "/admin/v1/clients",
            json=new_client(
                display_name="Other", login="other.login", whatsapp_number="+216 99 697 335"
            ),
        )
        creds = fx.application.credentials
        creds.save("client:acme-corp", "whatsapp.phone_number_id", "123456789012", actor="test")
        creds.save("client:acme-corp", "whatsapp.access_token", "tok", actor="test")
        assert client.get("/admin/v1/clients/other").json()["meta"]["status"] == "not_connected"
        from sales_app.secret_store import DuplicateSecretError

        with pytest.raises(DuplicateSecretError):
            creds.save("client:other", "whatsapp.phone_number_id", "123456789012", actor="test")

    def test_a_whatsapp_number_already_used_by_another_client_is_refused(self, fx, tmp_path):
        client = as_root(fx)
        assert client.post("/admin/v1/clients", json=new_client()).status_code == 201
        response = client.post(
            "/admin/v1/clients",
            json=new_client(
                display_name="Other", login="other.login", whatsapp_number="+216-22-768-307"
            ),
        )
        assert response.status_code == 409
        assert not (tmp_path / "other").exists()

    def test_a_new_client_cannot_be_created_active(self, fx, tmp_path):
        response = as_root(fx).post("/admin/v1/clients", json=new_client(status="active"))
        assert response.status_code == 400
        assert not (tmp_path / "acme-corp").exists()

    def test_duplicate_login_is_refused_and_leaves_nothing_behind(self, fx, tmp_path):
        client = as_root(fx)
        assert client.post("/admin/v1/clients", json=new_client()).status_code == 201
        response = client.post(
            "/admin/v1/clients", json=new_client(display_name="Other", login="ACME.admin")
        )
        assert response.status_code == 409
        assert not (tmp_path / "other").exists()

    def test_same_name_gets_a_distinct_client_id(self, fx):
        client = as_root(fx)
        client.post("/admin/v1/clients", json=new_client())
        second = client.post(
            "/admin/v1/clients",
            json=new_client(login="second.login", whatsapp_number="+216 99 697 335"),
        )
        assert second.json()["client_id"] == "acme-corp-2"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"login": "x"},
            {"temporary_password": "short"},
            {"display_name": "   "},
            {"display_name": "!!!"},
            {"llm_provider": "nonexistent"},
            {"logo": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="},
            {"logo": "data:image/png;base64,AAAA"},
            {"logo": "not a data url"},
            {"whatsapp_number": ""},
            {"whatsapp_number": "abc"},
            {"whatsapp_number": "+216 12"},
            {"whatsapp_number": "+216 22 768 307 ext 5"},
        ],
    )
    def test_invalid_input_is_refused(self, fx, tmp_path, overrides):
        response = as_root(fx).post("/admin/v1/clients", json=new_client(**overrides))
        assert response.status_code in (400, 422)
        assert not (tmp_path / "acme-corp").exists()

    def test_a_whatsapp_number_is_required(self, fx, tmp_path):
        body = new_client()
        del body["whatsapp_number"]
        assert as_root(fx).post("/admin/v1/clients", json=body).status_code == 422
        assert not (tmp_path / "acme-corp").exists()

    def test_only_an_owner_may_create_a_client(self, fx):
        assert fx.client.post("/admin/v1/clients", json=new_client()).status_code == 401
        client = as_alice(fx)
        assert client.post("/admin/v1/clients", json=new_client()).status_code == 403


class TestProspectsContacted:
    def test_counts_distinct_prospects_with_a_sent_message(self, fx):
        from factories import make_action

        from sales_core.contracts import ActionStatus

        repo = fx.made.repositories["alpha"]
        repo.save_actions(
            [
                make_action(
                    client_id="alpha",
                    action_id="a1",
                    prospect_id="p1",
                    idempotency_key="k1",
                    status=ActionStatus.SENT,
                ),
                make_action(
                    client_id="alpha",
                    action_id="a2",
                    prospect_id="p1",
                    idempotency_key="k2",
                    status=ActionStatus.SENT,
                ),
                make_action(
                    client_id="alpha",
                    action_id="a3",
                    prospect_id="p2",
                    idempotency_key="k3",
                    status=ActionStatus.SENT,
                ),
                make_action(
                    client_id="alpha",
                    action_id="a4",
                    prospect_id="p3",
                    idempotency_key="k4",
                    status=ActionStatus.PLANNED,
                ),
            ]
        )
        detail = as_root(fx).get("/admin/v1/clients/alpha").json()
        assert detail["prospects_contacted"] == 2
        assert detail["account"] is None  # alpha has no profile on record


class TestUnknownClient:
    def test_unknown_client_id_is_404(self, fx):
        client = as_root(fx)
        assert client.get("/admin/v1/clients/does-not-exist").status_code == 404

    def test_a_directory_that_is_not_a_client_is_404(self, fx):
        # `_template` is excluded from discovery entirely (leading underscore); even an owner
        # must not be able to fetch it by guessing the name.
        client = as_root(fx)
        assert client.get("/admin/v1/clients/_template").status_code == 404
