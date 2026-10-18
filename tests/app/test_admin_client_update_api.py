"""`PATCH /admin/v1/clients/{client_id}`: an owner edits an existing client from Client Details.

The profile (login, password hash, logo, WhatsApp number) stays in the client profile store, never
in `client.yaml`; the name, quota and LLM go through the versioned settings store; the status is the
`enabled` line of `client.yaml`. No response may carry a password, a hash or a Meta credential.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_admin_clients_api)

import pytest
from test_admin_clients_api import (  # noqa: F401
    NOW,
    PNG,
    Fixture,
    as_alice,
    as_root,
    fx,
    fx_secrets,
    new_client,
    owner,
)

from sales_app.admin_auth.passwords import hash_password, verify_password
from sales_app.client_profiles import ClientProfile

URL = "/admin/v1/clients/acme-corp"


@pytest.fixture
def acme(fx):
    """An owner session and a freshly created (inactive) client, as the Admin creates them."""
    client = as_root(fx)
    assert client.post("/admin/v1/clients", json=new_client()).status_code == 201
    return client


def give_profile(fx, client_id):
    fx.application.client_profiles.create(
        ClientProfile(
            client_id=client_id,
            login=f"{client_id}.login",
            password_hash=hash_password("temp-pass-123"),
            created_at=NOW,
        )
    )


def profile(fx):
    return fx.application.client_profiles.get("acme-corp")


class TestUpdateProfile:
    def test_the_name_is_changed(self, fx, acme):
        response = acme.patch(URL, json={"display_name": "Acme Industries"})
        assert response.status_code == 200
        assert response.json()["display_name"] == "Acme Industries"
        assert acme.get(URL).json()["display_name"] == "Acme Industries"
        names = {c["client_id"]: c["display_name"] for c in acme.get("/admin/v1/clients").json()}
        assert names["acme-corp"] == "Acme Industries"
        assert response.json()["client_id"] == "acme-corp"  # the id never changes with the name

    def test_the_logo_is_changed_and_removed(self, fx, acme):
        assert acme.patch(URL, json={"logo": PNG}).json()["account"]["logo"] == PNG
        assert profile(fx).logo == PNG
        assert acme.patch(URL, json={"logo": None}).json()["account"]["logo"] is None
        assert profile(fx).logo is None

    def test_an_invalid_logo_is_refused(self, fx, acme):
        response = acme.patch(URL, json={"logo": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="})
        assert response.status_code == 400
        assert profile(fx).logo is None

    def test_a_field_that_is_not_sent_is_left_alone(self, fx, acme):
        acme.patch(URL, json={"logo": PNG})
        acme.patch(URL, json={"display_name": "Acme Industries"})
        assert profile(fx).logo == PNG

    def test_the_login_is_changed_and_normalised(self, fx, acme):
        response = acme.patch(URL, json={"login": "  New.Login "})
        assert response.json()["account"]["login"] == "new.login"
        assert fx.application.client_profiles.get_by_login("new.login") is not None
        assert fx.application.client_profiles.get_by_login("acme.admin") is None

    def test_a_login_used_by_another_client_is_refused(self, fx, acme):
        acme.post(
            "/admin/v1/clients",
            json=new_client(
                display_name="Other", login="other.login", whatsapp_number="+216 99 697 335"
            ),
        )
        response = acme.patch(URL, json={"login": "OTHER.login"})
        assert response.status_code == 409
        assert profile(fx).login == "acme.admin"

    def test_keeping_the_same_login_is_not_a_conflict(self, fx, acme):
        assert acme.patch(URL, json={"login": "Acme.Admin"}).status_code == 200

    def test_the_password_is_changed_and_stored_hashed(self, fx, acme):
        before = profile(fx).password_hash
        response = acme.patch(URL, json={"new_password": "brand-new-pass"})
        assert response.status_code == 200
        stored = profile(fx)
        assert stored.password_hash != before
        assert stored.password_hash.startswith("$2")
        assert verify_password("brand-new-pass", stored.password_hash)
        assert not verify_password("temp-pass-123", stored.password_hash)

    def test_the_password_is_kept_when_none_is_sent(self, fx, acme):
        before = profile(fx).password_hash
        acme.patch(URL, json={"display_name": "Acme Industries", "new_password": None})
        assert profile(fx).password_hash == before

    def test_a_too_short_password_is_refused(self, fx, acme):
        before = profile(fx).password_hash
        assert acme.patch(URL, json={"new_password": "short"}).status_code == 422
        assert profile(fx).password_hash == before

    def test_the_whatsapp_number_is_normalised_and_stored_in_the_profile_only(
        self, fx, acme, tmp_path
    ):
        response = acme.patch(URL, json={"whatsapp_number": "  +216   99 697 335 "})
        assert response.json()["account"]["whatsapp_number"] == "+216 99 697 335"
        assert profile(fx).whatsapp_number == "+216 99 697 335"
        yaml_text = (tmp_path / "acme-corp" / "client.yaml").read_text()
        assert "216" not in yaml_text and "phone_number_id" not in yaml_text
        assert (
            fx.application.credentials.reveal("client:acme-corp", "whatsapp.phone_number_id")
            is None
        )

    def test_a_whatsapp_number_used_by_another_client_is_refused(self, fx, acme):
        acme.post(
            "/admin/v1/clients",
            json=new_client(
                display_name="Other", login="other.login", whatsapp_number="+216 99 697 335"
            ),
        )
        response = acme.patch(URL, json={"whatsapp_number": "+216-99-697-335"})
        assert response.status_code == 409
        assert profile(fx).whatsapp_number == "+216 22 768 307"

    @pytest.mark.parametrize("number", ["abc", "+216 12", ""])
    def test_an_invalid_whatsapp_number_is_refused(self, fx, acme, number):
        assert acme.patch(URL, json={"whatsapp_number": number}).status_code in (400, 422)
        assert profile(fx).whatsapp_number == "+216 22 768 307"


class TestUpdateSettings:
    def test_the_llm_model_is_changed(self, fx, acme):
        response = acme.patch(URL, json={"llm_provider": "openai", "llm_model": "gpt-4o-mini"})
        assert response.status_code == 200
        assert response.json()["llm"]["model"] == "gpt-4o-mini"
        assert response.json()["llm_model"] == "gpt-4o-mini"

    def test_a_model_that_is_not_allowed_is_refused(self, tmp_path):
        fixture = Fixture(
            tmp_path, "alpha", allowed_llm_models={"openai": ("test-model", "gpt-4o")}
        )
        fixture.users.create(owner())
        client = as_root(fixture)
        client.post("/admin/v1/clients", json=new_client())
        refused = client.patch(URL, json={"llm_provider": "openai", "llm_model": "gpt-5"})
        assert refused.status_code == 400
        assert client.get(URL).json()["llm_model"] == "gpt-4o"

    def test_provider_and_model_must_come_together(self, fx, acme):
        assert acme.patch(URL, json={"llm_model": "gpt-4o"}).status_code == 400

    def test_the_daily_quota_is_changed(self, fx, acme):
        response = acme.patch(URL, json={"daily_action_limit": 55})
        assert response.status_code == 200
        assert response.json()["daily_action_limit"] == 55
        assert acme.get(URL).json()["daily_action_limit"] == 55

    @pytest.mark.parametrize("quota", [0, -3, 1001])
    def test_an_invalid_daily_quota_is_refused(self, fx, acme, quota):
        assert acme.patch(URL, json={"daily_action_limit": quota}).status_code == 422
        assert acme.get(URL).json()["daily_action_limit"] == 20


class TestUpdateStatus:
    def test_an_active_client_is_deactivated_and_reactivated(self, fx):
        client = as_root(fx)
        give_profile(fx, "alpha")
        url = "/admin/v1/clients/alpha"
        assert client.get(url).json()["status"] == "active"
        deactivated = client.patch(url, json={"status": "inactive"}).json()
        assert deactivated["status"] == "disabled"
        assert deactivated["restart_required"] is True  # its runtime stops at the next start
        assert {c["client_id"]: c["status"] for c in client.get("/admin/v1/clients").json()}[
            "alpha"
        ] == "disabled"
        reactivated = client.patch(url, json={"status": "active"}).json()
        assert reactivated["status"] == "active"
        assert reactivated["restart_required"] is False

    def test_an_account_not_configured_yet_can_still_be_activated(self, fx, tmp_path):
        """Activating the account is not starting the agent: no model, Sheet or Brain is needed."""
        client = as_root(fx)
        client.post("/admin/v1/clients", json=new_client())
        response = client.patch(URL, json={"status": "active"})
        assert response.status_code == 200, response.text
        assert client.get(URL).json()["status"] == "active"
        assert "enabled: true" in (tmp_path / "acme-corp" / "client.yaml").read_text()

    def test_a_disabled_client_is_activated_once_it_is_valid(self, fx):
        client = as_root(fx)
        give_profile(fx, "naga")
        response = client.patch("/admin/v1/clients/naga", json={"status": "active"})
        assert response.status_code == 200
        assert response.json()["status"] == "active"
        assert response.json()["restart_required"] is False  # put in force at once, no restart


class TestUpdateSafety:
    def test_no_password_hash_or_meta_secret_is_ever_returned(self, fx_secrets):
        # `fx_secrets` has a credential store: store real Meta values for the client first.
        from test_admin_clients_api import as_root as root

        client = root(fx_secrets)
        creds = fx_secrets.application.credentials
        creds.save("client:alpha", "whatsapp.access_token", "EAAG-secret-token", actor="test")
        creds.save("client:alpha", "whatsapp.phone_number_id", "109876543210987", actor="test")
        client.post("/admin/v1/clients", json=new_client())
        response = client.patch(
            URL, json={"new_password": "brand-new-pass", "whatsapp_number": "+216 99 697 335"}
        )
        assert response.status_code == 200
        stored = fx_secrets.application.client_profiles.get("acme-corp")
        for text in (
            response.text,
            client.get(URL).text,
            client.get("/admin/v1/clients/alpha").text,
        ):
            assert "brand-new-pass" not in text
            assert stored.password_hash not in text
            assert "password" not in text.lower().replace("must_change_password", "")
            assert "EAAG-secret-token" not in text
            assert "109876543210987" not in text

    def test_changing_the_number_leaves_the_meta_status_as_it_was(self, fx_secrets):
        from test_admin_clients_api import as_root as root

        client = root(fx_secrets)
        client.post("/admin/v1/clients", json=new_client())
        creds = fx_secrets.application.credentials
        creds.save("client:acme-corp", "whatsapp.access_token", "EAAG-secret-token", actor="test")
        creds.save("client:acme-corp", "whatsapp.phone_number_id", "109876543210987", actor="test")
        response = client.patch(URL, json={"whatsapp_number": "+216 99 697 335"})
        assert response.json()["meta"] == {
            "status": "credentials_stored",
            "credentials_stored": True,
        }

    def test_a_client_without_a_profile_needs_login_password_and_number_together(self, fx):
        client = as_root(fx)
        assert client.patch("/admin/v1/clients/alpha", json={"login": "x.login"}).status_code == 400
        assert fx.application.client_profiles.get("alpha") is None

    def test_a_profile_is_created_for_a_client_that_had_none(self, fx):
        client = as_root(fx)
        response = client.patch(
            "/admin/v1/clients/alpha",
            json={
                "login": "Alpha.Login",
                "new_password": "admin-set-pass",
                "whatsapp_number": "+216 55 123 456",
            },
        )
        assert response.status_code == 200
        assert response.json()["account"]["login"] == "alpha.login"
        stored = fx.application.client_profiles.get("alpha")
        assert verify_password("admin-set-pass", stored.password_hash)
        assert "admin-set-pass" not in response.text

    def test_only_an_owner_may_edit_a_client(self, fx):
        assert (
            fx.client.patch("/admin/v1/clients/alpha", json={"display_name": "X"}).status_code
            == 401
        )
        assert (
            as_alice(fx).patch("/admin/v1/clients/alpha", json={"display_name": "X"}).status_code
            == 403
        )

    def test_an_unknown_client_is_404(self, fx):
        assert (
            as_root(fx).patch("/admin/v1/clients/nope", json={"display_name": "X"}).status_code
            == 404
        )
