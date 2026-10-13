"""`/admin/v1/.../integrations`: write-only credential management.

Covers: authentication and OWNER-only writes, per-scope access, status without values, that a
sensitive value never appears in any response (success or error), confirmation on removal, unique
phone number ids, the unavailable-storage answer, startup with the master key, and that nothing
readable lands on disk or in the audit trail.
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
from sales_app.errors import ConfigurationError
from sales_app.secret_store import (
    AUDIT_FILENAME,
    SECRETS_FILENAME,
    UnavailableSecretStore,
    generate_master_key,
)
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"
SECRET = "sk-LIVE-very-secret-marker-123"
TOKEN = "EAAG-token-marker-456"
BASE = "/admin/v1/settings/integrations"


def user(username, role, client_ids=frozenset()):
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=role,
        client_ids=client_ids,
        created_at=NOW,
    )


class Fixture:
    def __init__(self, tmp_path, *, key="generate", clients=("alpha", "beta")):
        self.tmp_path = tmp_path
        self.secrets_dir = tmp_path / "secrets"
        self.key = generate_master_key() if key == "generate" else key
        self.users = InMemoryAdminUserStore()
        for cid in clients:
            write_client(tmp_path / "clients", cid)
        clock = FakeClock(NOW)
        builders, _ = fake_builders(clock)
        self.settings = make_settings(
            tmp_path / "clients",
            secrets_dir=self.secrets_dir,
            secrets_key=self.key,
            admin_dir=tmp_path / "admin",
            config_dir=tmp_path / "config",
            profiles_dir=tmp_path / "profiles",
        )
        self.application = build_application(
            self.settings,
            env=env_for(*clients),
            builders=builders,
            clock=clock,
            executor=InlineExecutor(),
            admin_user_store=self.users,
            admin_session_store=InMemoryAdminSessionStore(),
        )
        self.client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )

    def login(self, username):
        response = self.client.post(
            "/admin/v1/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200
        return self.client

    def everything_on_disk(self) -> str:
        return "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in self.secrets_dir.rglob("*")
            if p.is_file()
        )


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path)
    fixture.users.create(user("root", AdminRole.OWNER))
    fixture.users.create(user("olivia", AdminRole.OPERATOR, frozenset({"alpha"})))
    fixture.users.create(user("eve", AdminRole.EDITOR, frozenset({"alpha"})))
    fixture.users.create(user("vera", AdminRole.VIEWER, frozenset({"alpha"})))
    return fixture


def put(client, url, value):
    return client.put(url, json={"value": value})


class TestAuthentication:
    @pytest.mark.parametrize(
        "method,url",
        [
            ("get", BASE),
            ("put", f"{BASE}/openai.api_key"),
            ("delete", f"{BASE}/openai.api_key?confirm=true"),
            ("get", "/admin/v1/clients/alpha/integrations"),
            ("put", "/admin/v1/clients/alpha/integrations/whatsapp.access_token"),
            ("delete", "/admin/v1/clients/alpha/integrations/whatsapp.access_token?confirm=true"),
        ],
    )
    def test_anonymous_is_rejected(self, fx, method, url):
        kwargs = {"json": {"value": "x"}} if method == "put" else {}
        assert getattr(fx.client, method)(url, **kwargs).status_code == 401


class TestOnlyOwnersWrite:
    @pytest.mark.parametrize("who", ["olivia", "eve", "vera"])
    def test_other_roles_cannot_write_or_delete_global(self, fx, who):
        client = fx.login(who)
        assert put(client, f"{BASE}/openai.api_key", SECRET).status_code == 403
        assert client.delete(f"{BASE}/openai.api_key?confirm=true").status_code == 403
        assert fx.application.credentials.status("global", "openai.api_key").configured is False

    @pytest.mark.parametrize("who", ["olivia", "eve", "vera"])
    def test_other_roles_cannot_write_or_delete_a_client_credential(self, fx, who):
        client = fx.login(who)
        url = "/admin/v1/clients/alpha/integrations/whatsapp.access_token"
        assert put(client, url, TOKEN).status_code == 403
        assert client.delete(f"{url}?confirm=true").status_code == 403

    def test_a_scoped_admin_can_read_status_of_their_own_client_and_global(self, fx):
        client = fx.login("vera")
        assert client.get(BASE).status_code == 200
        assert client.get("/admin/v1/clients/alpha/integrations").status_code == 200

    def test_a_scoped_admin_cannot_see_another_client(self, fx):
        client = fx.login("vera")
        assert client.get("/admin/v1/clients/beta/integrations").status_code == 404


class TestGlobalCredentials:
    def test_status_before_anything_is_saved(self, fx):
        body = fx.login("root").get(BASE).json()
        assert {c["key"] for c in body} == {
            "openai.api_key",
            "openai.organization",
            "openai.project",
            "openai.base_url",
            "openai.model_sol",
            "openai.model_lune",
            "openai.models",
            "whatsapp.verify_token",
            "whatsapp.app_secret",
            "whatsapp.api_version",
        }
        assert all(c["configured"] is False and c["value"] is None for c in body)
        assert all(c["scope"] == "global" for c in body)

    def test_save_reports_status_and_never_the_value(self, fx):
        client = fx.login("root")
        response = put(client, f"{BASE}/openai.api_key", SECRET)
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True and body["sensitive"] is True
        assert body["value"] is None and body["updated_by"] == "root"
        assert SECRET not in response.text
        assert SECRET not in client.get(BASE).text

    def test_replace(self, fx):
        client = fx.login("root")
        put(client, f"{BASE}/openai.api_key", "first-value")
        put(client, f"{BASE}/openai.api_key", "second-value")
        assert fx.application.credentials._store.get("global", "openai.api_key") == "second-value"

    def test_non_sensitive_values_are_shown(self, fx):
        client = fx.login("root")
        put(client, f"{BASE}/whatsapp.api_version", "v21.0")
        entry = next(c for c in client.get(BASE).json() if c["key"] == "whatsapp.api_version")
        assert entry["value"] == "v21.0" and entry["sensitive"] is False

    def test_invalid_api_version_is_a_422_that_names_the_rule(self, fx):
        response = put(fx.login("root"), f"{BASE}/whatsapp.api_version", "latest")
        assert response.status_code == 422
        assert "v21.0" in response.json()["detail"]

    def test_unknown_and_foreign_scope_keys_are_404(self, fx):
        client = fx.login("root")
        assert put(client, f"{BASE}/muse_spark.api_key", SECRET).status_code == 404
        assert put(client, f"{BASE}/whatsapp.access_token", TOKEN).status_code == 404

    def test_removal_needs_explicit_confirmation(self, fx):
        client = fx.login("root")
        put(client, f"{BASE}/openai.api_key", SECRET)
        assert client.delete(f"{BASE}/openai.api_key").status_code == 400
        assert client.delete(f"{BASE}/openai.api_key?confirm=false").status_code == 400
        assert fx.application.credentials.status("global", "openai.api_key").configured is True
        response = client.delete(f"{BASE}/openai.api_key?confirm=true")
        assert response.status_code == 200 and response.json()["configured"] is False

    def test_removing_something_not_set_is_not_an_error(self, fx):
        assert fx.login("root").delete(f"{BASE}/openai.api_key?confirm=true").status_code == 200

    def test_responses_are_not_cacheable(self, fx):
        client = fx.login("root")
        assert client.get(BASE).headers["cache-control"] == "no-store"
        assert put(client, f"{BASE}/openai.api_key", SECRET).headers["cache-control"] == "no-store"


class TestBodyHandling:
    @pytest.mark.parametrize(
        "payload",
        [{}, {"value": 123}, {"value": None}, {"value": "x", "extra": 1}, ["x"], "x", {"val": "x"}],
    )
    def test_malformed_bodies_are_422_and_echo_nothing(self, fx, payload):
        client = fx.login("root")
        response = client.put(f"{BASE}/openai.api_key", json=payload)
        assert response.status_code == 422
        assert "input" not in response.text

    def test_a_non_json_body_is_400(self, fx):
        client = fx.login("root")
        response = client.put(
            f"{BASE}/openai.api_key",
            content=b"not json " + SECRET.encode(),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert SECRET not in response.text

    def test_a_refused_value_is_not_echoed(self, fx):
        client = fx.login("root")
        response = put(
            client,
            "/admin/v1/clients/alpha/integrations/whatsapp.phone_number_id",
            "SECRET-ish-abc",
        )
        assert response.status_code == 422
        assert "SECRET-ish-abc" not in response.text


class TestClientCredentials:
    def test_status_lists_the_client_keys(self, fx):
        body = fx.login("root").get("/admin/v1/clients/alpha/integrations").json()
        assert {c["key"] for c in body} == {
            "whatsapp.access_token",
            "whatsapp.phone_number_id",
            "hubspot.access_token",
            "email.password",
            "telegram.bot_token",
            "telegram.webhook_secret",
        }
        assert all(c["scope"] == "client" for c in body)

    def test_save_token_and_phone_number_id(self, fx):
        client = fx.login("root")
        base = "/admin/v1/clients/alpha/integrations"
        token = put(client, f"{base}/whatsapp.access_token", TOKEN)
        phone = put(client, f"{base}/whatsapp.phone_number_id", "123456789012345")
        assert token.json()["value"] is None and TOKEN not in token.text
        assert phone.json()["value"] == "123456789012345"

    def test_credentials_are_kept_per_client(self, fx):
        client = fx.login("root")
        put(client, "/admin/v1/clients/alpha/integrations/whatsapp.access_token", TOKEN)
        beta = client.get("/admin/v1/clients/beta/integrations").json()
        assert not any(c["configured"] for c in beta)

    def test_a_phone_number_id_cannot_be_given_to_two_clients(self, fx):
        client = fx.login("root")
        key = "whatsapp.phone_number_id"
        assert (
            put(client, f"/admin/v1/clients/alpha/integrations/{key}", "123456789").status_code
            == 200
        )
        response = put(client, f"/admin/v1/clients/beta/integrations/{key}", "123456789")
        assert response.status_code == 409
        assert "alpha" in response.json()["detail"]
        beta = client.get("/admin/v1/clients/beta/integrations").json()
        assert not any(c["configured"] for c in beta)

    def test_the_same_client_may_save_its_own_phone_number_id_again(self, fx):
        client = fx.login("root")
        url = "/admin/v1/clients/alpha/integrations/whatsapp.phone_number_id"
        assert put(client, url, "123456789").status_code == 200
        assert put(client, url, "123456789").status_code == 200

    def test_a_freed_phone_number_id_can_be_reused(self, fx):
        client = fx.login("root")
        key = "whatsapp.phone_number_id"
        put(client, f"/admin/v1/clients/alpha/integrations/{key}", "123456789")
        client.delete(f"/admin/v1/clients/alpha/integrations/{key}?confirm=true")
        assert (
            put(client, f"/admin/v1/clients/beta/integrations/{key}", "123456789").status_code
            == 200
        )

    def test_an_unknown_client_is_404(self, fx):
        client = fx.login("root")
        assert client.get("/admin/v1/clients/ghost/integrations").status_code == 404
        assert (
            put(
                client, "/admin/v1/clients/ghost/integrations/whatsapp.access_token", TOKEN
            ).status_code
            == 404
        )
        assert client.get("/admin/v1/clients/..%2Fx/integrations").status_code == 404

    def test_removal_needs_confirmation(self, fx):
        client = fx.login("root")
        url = "/admin/v1/clients/alpha/integrations/whatsapp.access_token"
        put(client, url, TOKEN)
        assert client.delete(url).status_code == 400
        assert client.delete(f"{url}?confirm=true").json()["configured"] is False


class TestNothingReadableIsStored:
    def test_disk_and_audit_hold_no_secret_value(self, fx):
        client = fx.login("root")
        put(client, f"{BASE}/openai.api_key", SECRET)
        put(client, "/admin/v1/clients/alpha/integrations/whatsapp.access_token", TOKEN)
        put(client, "/admin/v1/clients/alpha/integrations/whatsapp.phone_number_id", "123456789")
        client.delete(f"{BASE}/openai.api_key?confirm=true")
        on_disk = fx.everything_on_disk()
        assert SECRET not in on_disk and TOKEN not in on_disk
        audit = (fx.secrets_dir / AUDIT_FILENAME).read_text(encoding="utf-8")
        assert '"actor": "root"' in audit and '"outcome": "stored"' in audit
        assert (fx.secrets_dir / SECRETS_FILENAME).exists()

    def test_the_value_never_reaches_client_yaml_or_config(self, fx):
        put(fx.login("root"), "/admin/v1/clients/alpha/integrations/whatsapp.access_token", TOKEN)
        for path in (fx.tmp_path / "clients").rglob("*"):
            if path.is_file():
                assert TOKEN not in path.read_text(encoding="utf-8")
        assert not (fx.tmp_path / "config").exists()


class TestStorageAvailability:
    def test_without_a_master_key_writes_are_503_and_reads_still_work(self, tmp_path):
        fixture = Fixture(tmp_path, key=None)
        fixture.users.create(user("root", AdminRole.OWNER))
        client = fixture.login("root")
        assert isinstance(fixture.application.credentials._store, UnavailableSecretStore)
        response = put(client, f"{BASE}/openai.api_key", SECRET)
        assert response.status_code == 503
        assert "SALES_AGENT_SECRETS_KEY" in response.json()["detail"]
        assert SECRET not in response.text
        assert client.delete(f"{BASE}/openai.api_key?confirm=true").status_code == 503
        assert client.get(BASE).status_code == 200


class TestStartupWithTheMasterKey:
    def test_a_secrets_file_without_the_key_blocks_startup(self, tmp_path):
        first = Fixture(tmp_path)
        first.users.create(user("root", AdminRole.OWNER))
        put(first.login("root"), f"{BASE}/openai.api_key", SECRET)
        with pytest.raises(ConfigurationError, match="SALES_AGENT_SECRETS_KEY"):
            Fixture(tmp_path, key=None)

    def test_a_secrets_file_with_the_wrong_key_blocks_startup(self, tmp_path):
        first = Fixture(tmp_path)
        first.users.create(user("root", AdminRole.OWNER))
        put(first.login("root"), f"{BASE}/openai.api_key", SECRET)
        with pytest.raises(ConfigurationError) as error:
            Fixture(tmp_path, key=generate_master_key())
        assert SECRET not in str(error.value)

    def test_a_restart_with_the_same_key_keeps_the_credentials(self, tmp_path):
        first = Fixture(tmp_path)
        first.users.create(user("root", AdminRole.OWNER))
        put(first.login("root"), f"{BASE}/openai.api_key", SECRET)
        second = Fixture(tmp_path, key=first.key)
        assert second.application.credentials.status("global", "openai.api_key").configured is True

    def test_no_file_and_no_key_starts_as_today(self, tmp_path):
        fixture = Fixture(tmp_path, key=None)
        assert fixture.application.registry.get("alpha") is not None
