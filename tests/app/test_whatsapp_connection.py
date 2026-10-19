"""A client connects its own WhatsApp Business account with Meta's Embedded Signup.

Meta is replaced by a fake Graph API that records every call: these tests prove what Revansa asks of
Meta and what it keeps, NOT that Meta accepts the app. That needs a real Meta account (see
docs/whatsapp-channel.md).
"""

import base64
import hashlib
import hmac
import json

import pytest
from test_client_agent_api import World

from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore, client_scope
from sales_app.secret_store.catalog import GLOBAL_SCOPE
from sales_app.whatsapp_state import APPROVED, TemplateRecord
from sales_integrations.whatsapp import (
    ACCOUNT,
    DELIVERY,
    TEMPLATE,
    GraphError,
    WhatsAppNotification,
)

BASE = "/client/v1/channel/whatsapp"
ADMIN = "/admin/v1/clients/alpha/whatsapp"
APP_SECRET = "app-secret-marker"
TOKEN = "EAAG-business-token-marker"
WABA = "111111111111"
PHONE = "555555555555"


class FakeGraph:
    def __init__(self, display="+216 20 000 001"):
        self.calls = []
        self.display = display
        self.fail = {}
        self.templates = []

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def exchange_code(self, *, app_id, app_secret, code):
        self._do("exchange", code)
        return TOKEN

    def token_info(self, *, app_id, app_secret, token):
        self._do("token_info")
        return {"app_id": app_id, "user_id": "fbuser-1", "is_valid": True}

    def granted_accounts(self, *, app_id, app_secret, token):
        self._do("granted")
        return [WABA]

    def phone_numbers(self, token, waba):
        self._do("phone_numbers", waba)
        if waba != WABA:  # an account the token does not grant: Meta refuses to read it
            raise GraphError("unsupported get request", code="100", status=400)
        return [
            {
                "id": PHONE,
                "display_phone_number": self.display,
                "verified_name": "Alpha SARL",
                "quality_rating": "GREEN",
                "status": "PENDING",
            }
        ]

    def phone_number(self, token, phone_id):
        self._do("phone_number")
        return {
            "id": phone_id,
            "display_phone_number": self.display,
            "verified_name": "Alpha SARL",
            "quality_rating": "YELLOW",
        }

    def subscribe(self, token, waba):
        self._do("subscribe", waba)

    def subscribed(self, token, waba):
        self._do("subscribed")
        return True

    def register(self, token, phone_id, pin):
        self._do("register", phone_id)
        assert len(pin) == 6 and pin.isdigit()

    def unsubscribe(self, token, waba):
        self._do("unsubscribe", waba)

    def deregister(self, token, phone_id):
        self._do("deregister", phone_id)

    def revoke(self, token):
        self._do("revoke")

    def list_templates(self, token, waba):
        self._do("list_templates")
        return self.templates

    def create_template(self, token, waba, payload):
        self._do("create_template", payload["name"])
        return {"id": "9001", "status": "PENDING", "category": payload["category"]}

    def delete_template(self, token, waba, name):
        self._do("delete_template", name)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=InMemorySecretAudit())
    w.store = w.application.whatsapp._secrets  # the very store the application uses
    w.graph = FakeGraph()
    w.application.whatsapp._graph_factory = lambda version: w.graph
    yield w
    w.application.close()


def configure_platform(world):
    for key, value in (
        ("whatsapp.app_id", "999000111222"),
        ("whatsapp.config_id", "888000111222"),
        ("whatsapp.app_secret", APP_SECRET),
        ("whatsapp.verify_token", "verify-marker"),
    ):
        world.application.credentials.save(GLOBAL_SCOPE, key, value, actor="test")


def secret(world, key, client="alpha"):
    return world.store.get(client_scope(client), key)  # also the keys the Admin API cannot see


def connect(world, browser, **body):
    started = browser.post(f"{BASE}/start")
    assert started.status_code == 200, started.text
    payload = {"state": started.json()["state"], "code": "one-time-code", **body}
    return browser.post(f"{BASE}/complete", json=payload)


class TestStart:
    def test_unavailable_until_the_admin_set_the_meta_app(self, world):
        browser = world.browser("alpha")
        assert browser.get(BASE).json()["state"] == "unavailable"
        assert browser.post(f"{BASE}/start").status_code == 503

    def test_it_gives_the_browser_the_public_ids_and_a_state_only(self, world):
        configure_platform(world)
        body = world.browser("alpha").post(f"{BASE}/start").json()
        assert body["app_id"] == "999000111222" and body["config_id"] == "888000111222"
        assert set(body) == {"app_id", "config_id", "api_version", "state"}
        assert APP_SECRET not in json.dumps(body)

    def test_another_clients_state_is_refused(self, world):
        configure_platform(world)
        state = world.browser("alpha").post(f"{BASE}/start").json()["state"]
        response = world.browser("beta").post(
            f"{BASE}/complete", json={"state": state, "code": "abcd"}
        )
        assert response.status_code == 400 and response.json()["detail"] == "invalid"
        assert world.graph.calls == []  # Meta was not even asked

    def test_a_state_is_single_use(self, world):
        configure_platform(world)
        browser = world.browser("alpha")
        state = browser.post(f"{BASE}/start").json()["state"]
        body = {"state": state, "code": "abcd"}
        assert browser.post(f"{BASE}/complete", json=body).status_code == 200
        assert browser.post(f"{BASE}/complete", json=body).json()["detail"] == "invalid"

    def test_a_forged_state_is_refused(self, world):
        configure_platform(world)
        browser = world.browser("alpha")
        state = browser.post(f"{BASE}/start").json()["state"]
        forged = state[:-1] + ("0" if state[-1] != "0" else "1")
        response = browser.post(f"{BASE}/complete", json={"state": forged, "code": "abcd"})
        assert response.json()["detail"] == "invalid"


class TestComplete:
    def test_the_account_is_confirmed_with_meta_and_kept_server_side_only(self, world):
        configure_platform(world)
        browser = world.browser("alpha")
        response = connect(world, browser, waba_id=WABA, phone_number_id=PHONE)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "connected" and body["verified_name"] == "Alpha SARL"
        assert body["status"] == "ready"
        # What Revansa asked of Meta, in order: exchange, check the grant, subscribe, register.
        names = [c[0] for c in world.graph.calls]
        assert names.index("exchange") < names.index("granted") < names.index("subscribe")
        assert "register" in names
        # Kept in the client's own scope; nothing of it in any response.
        assert secret(world, "whatsapp.access_token") == TOKEN
        assert secret(world, "whatsapp.phone_number_id") == PHONE
        assert secret(world, "whatsapp.waba_id") == WABA
        assert (
            TOKEN not in response.text and PHONE not in response.text and WABA not in response.text
        )
        assert secret(world, "whatsapp.access_token", "beta") is None

    def test_the_ids_the_browser_reports_are_only_hints(self, world):
        configure_platform(world)
        # The browser claims another account; Meta says the token grants only WABA.
        response = connect(
            world, world.browser("alpha"), waba_id="222222222222", phone_number_id="999999999"
        )
        assert response.status_code == 200
        assert secret(world, "whatsapp.waba_id") == WABA

    def test_a_number_that_is_not_the_clients_is_refused_and_nothing_is_kept(self, world):
        configure_platform(world)
        world.graph.display = "+216 99 999 999"
        response = connect(world, world.browser("alpha"))
        assert response.status_code == 409 and response.json()["detail"] == "number_mismatch"
        assert secret(world, "whatsapp.access_token") is None
        assert secret(world, "whatsapp.phone_number_id") is None
        names = [c[0] for c in world.graph.calls]
        assert "subscribe" not in names and "revoke" in names  # the token is withdrawn at once

    def test_a_number_already_used_by_another_client_is_refused(self, world):
        configure_platform(world)
        world.application.credentials.save(
            client_scope("beta"), "whatsapp.phone_number_id", PHONE, actor="t"
        )
        response = connect(world, world.browser("alpha"))
        assert response.status_code == 409 and response.json()["detail"] == "in_use"
        assert secret(world, "whatsapp.access_token") is None
        names = [c[0] for c in world.graph.calls]
        assert "subscribe" not in names and "register" not in names  # refused before any change

    def test_meta_refusing_the_code_keeps_nothing(self, world):
        configure_platform(world)
        world.graph.fail["exchange"] = GraphError("bad code", code="100", status=400)
        response = connect(world, world.browser("alpha"))
        assert response.status_code == 502 and response.json()["detail"] == "error"
        assert secret(world, "whatsapp.access_token") is None

    def test_meta_unreachable_is_a_retryable_503(self, world):
        configure_platform(world)
        world.graph.fail["phone_numbers"] = GraphError("down", transient=True)
        assert connect(world, world.browser("alpha")).status_code == 503

    def test_connecting_clears_the_number_changed_mark(self, world):
        import dataclasses

        configure_platform(world)
        store = world.application.client_profiles
        store.update(dataclasses.replace(store.get("alpha"), whatsapp_reconnect_required=True))
        assert world.browser("alpha").get(BASE).json()["status"] == "action_required"
        assert connect(world, world.browser("alpha")).status_code == 200
        assert store.get("alpha").whatsapp_reconnect_required is False

    def test_the_body_accepts_nothing_else(self, world):
        configure_platform(world)
        browser = world.browser("alpha")
        state = browser.post(f"{BASE}/start").json()["state"]
        response = browser.post(
            f"{BASE}/complete", json={"state": state, "code": "abcd", "access_token": "mine"}
        )
        assert response.status_code == 422 and "mine" not in response.text


class TestDisconnect:
    def test_meta_is_told_and_everything_is_forgotten(self, world):
        configure_platform(world)
        browser = world.browser("alpha")
        connect(world, browser)
        world.application.whatsapp.state.save_template(
            TemplateRecord(client_id="alpha", name="hello", language="fr", status=APPROVED)
        )
        world.graph.calls.clear()
        body = browser.post(f"{BASE}/disconnect").json()
        assert body["state"] == "not_connected"
        names = [c[0] for c in world.graph.calls]
        assert names == ["unsubscribe", "revoke"]  # the number is released only on the Admin's word
        for key in ("access_token", "phone_number_id", "waba_id", "registration_pin", "fb_user_id"):
            assert secret(world, f"whatsapp.{key}") is None
        assert not world.application.whatsapp.templates("alpha")

    def test_it_is_forgotten_even_when_meta_refuses(self, world):
        configure_platform(world)
        browser = world.browser("alpha")
        connect(world, browser)
        world.graph.fail["unsubscribe"] = GraphError("no", status=400)
        world.graph.fail["revoke"] = GraphError("no", status=400)
        assert browser.post(f"{BASE}/disconnect").json()["state"] == "not_connected"
        assert secret(world, "whatsapp.access_token") is None

    def test_the_admin_sees_each_step_and_may_release_the_number(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        world.graph.calls.clear()
        response = world.admin().post(f"{ADMIN}/disconnect", json={"release_number": True})
        assert response.json()["steps"] == {
            "unsubscribe_webhooks": "done",
            "deregister_number": "done",
            "revoke_token": "done",
        }
        assert [c[0] for c in world.graph.calls] == ["unsubscribe", "deregister", "revoke"]

    def test_a_token_meta_refused_needs_no_remote_step(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        world.application.whatsapp.mark_reauth_required("alpha")
        world.graph.calls.clear()
        report = world.application.whatsapp.disconnect("alpha")
        assert set(report.steps.values()) == {"skipped"} and world.graph.calls == []


class TestWhatMetaTellsUs:
    def service(self, world):
        return world.application.whatsapp

    def test_a_template_approval_updates_the_template(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        service = self.service(world)
        service.state.save_template(
            TemplateRecord(
                client_id="alpha",
                name="hello_v1",
                language="fr",
                body="Hi {{1}}",
                parameter_names=("1",),
            )
        )
        service.handle(
            WhatsAppNotification(
                TEMPLATE,
                WABA,
                None,
                {
                    "field": "message_template_status_update",
                    "name": "hello_v1",
                    "language": "fr",
                    "event": "APPROVED",
                    "template_id": "42",
                    "reason": "",
                },
            )
        )
        (template,) = service.templates("alpha")
        assert template.status == "APPROVED" and template.meta_id == "42"
        assert service.choose_opening("alpha", "hello_v1", "fr")
        # Rejected later: it no longer opens conversations.
        service.handle(
            WhatsAppNotification(
                TEMPLATE,
                WABA,
                None,
                {
                    "field": "message_template_status_update",
                    "name": "hello_v1",
                    "language": "fr",
                    "event": "DISABLED",
                    "reason": "policy",
                },
            )
        )
        assert service.state.opening_template("alpha") is None

    def test_an_opening_template_needs_exactly_one_variable(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        service = self.service(world)
        for name, params in (("none", ()), ("two", ("1", "2"))):
            service.state.save_template(
                TemplateRecord(
                    client_id="alpha",
                    name=name,
                    language="fr",
                    status=APPROVED,
                    parameter_names=params,
                )
            )
            assert service.choose_opening("alpha", name, "fr") is False

    def test_delivery_statuses_only_move_forward_and_show_in_the_conversation_data(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        service = self.service(world)

        def note(status, **extra):
            data = {
                "message_id": "wamid.9",
                "status": status,
                "recipient": "x",
                "at": "2026-05-01T09:30:00+00:00",
                **extra,
            }
            service.handle(WhatsAppNotification(DELIVERY, WABA, PHONE, data))

        note("read")
        note("delivered")  # late, out of order: must not go back
        assert service.state.delivery("alpha", "wamid.9").status == "read"
        note("failed", error_code="131026", error_title="undeliverable")
        assert service.state.delivery("alpha", "wamid.9").status == "read"  # read is final
        note("failed", message_id="x")  # unknown shapes do not raise

    def test_a_failure_with_a_refused_token_marks_the_client_to_reconnect(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        data = {
            "message_id": "w",
            "status": "failed",
            "recipient": "x",
            "at": "2026-05-01T09:30:00+00:00",
            "error_code": "190",
            "error_title": "token",
        }
        self.service(world).handle(WhatsAppNotification(DELIVERY, WABA, PHONE, data))
        body = world.browser("alpha").get(BASE).json()
        assert body["state"] == "reauth_required" and body["status"] == "action_required"

    def test_the_owner_removing_the_app_forgets_the_account(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        self.service(world).handle(
            WhatsAppNotification(
                ACCOUNT, WABA, None, {"field": "account_update", "event": "PARTNER_APP_UNINSTALLED"}
            )
        )
        assert secret(world, "whatsapp.access_token") is None

    def test_notifications_for_an_unknown_account_are_ignored(self, world):
        configure_platform(world)
        self.service(world).handle(
            WhatsAppNotification(ACCOUNT, "424242424242", None, {"event": "PARTNER_APP_UNINSTALLED"})
        )


class TestTemplatesForTheAdmin:
    def connected(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        return world.admin()

    def test_a_template_is_validated_then_submitted_to_meta(self, world):
        admin = self.connected(world)
        bad = admin.post(
            f"{ADMIN}/templates",
            json={
                "name": "Bad Name",
                "language": "fr",
                "category": "UTILITY",
                "body": "x {{1}} y",
                "examples": ["a"],
            },
        )
        assert bad.status_code == 422
        good = admin.post(
            f"{ADMIN}/templates",
            json={
                "name": "hello_v1",
                "language": "fr",
                "category": "UTILITY",
                "body": "Bonjour, un message de Alpha : {{1}} Merci.",
                "examples": ["exemple"],
            },
        )
        assert good.status_code == 200, good.text
        assert good.json()["status"] == "PENDING" and good.json()["parameters"] == ["1"]
        assert ("create_template", "hello_v1") in world.graph.calls

    def test_pending_templates_cannot_be_chosen_to_open_conversations(self, world):
        admin = self.connected(world)
        admin.post(
            f"{ADMIN}/templates",
            json={
                "name": "hello_v1",
                "language": "fr",
                "category": "UTILITY",
                "body": "Bonjour : {{1}} Merci.",
                "examples": ["e"],
            },
        )
        response = admin.put(
            f"{ADMIN}/templates/opening", json={"name": "hello_v1", "language": "fr"}
        )
        assert response.status_code == 409

    def test_sync_reads_what_meta_lists(self, world):
        admin = self.connected(world)
        world.graph.templates = [
            {
                "id": "1",
                "name": "hello_v1",
                "language": "fr",
                "status": "APPROVED",
                "category": "UTILITY",
                "components": [{"type": "BODY", "text": "Bonjour : {{1}} Merci."}],
            }
        ]
        rows = admin.post(f"{ADMIN}/templates/sync").json()
        assert rows[0]["status"] == "APPROVED" and rows[0]["usable_as_opening"] is True
        assert (
            admin.put(
                f"{ADMIN}/templates/opening", json={"name": "hello_v1", "language": "fr"}
            ).status_code
            == 200
        )
        assert admin.get(ADMIN).json()["opening_template"] == "hello_v1"

    def test_meta_refusing_a_template_is_shown_to_the_admin(self, world):
        admin = self.connected(world)
        world.graph.fail["create_template"] = GraphError(
            "Template name already exists", code="100", status=400
        )
        response = admin.post(
            f"{ADMIN}/templates",
            json={
                "name": "hello_v1",
                "language": "fr",
                "category": "UTILITY",
                "body": "Bonjour : {{1}} Merci.",
                "examples": ["e"],
            },
        )
        assert response.status_code == 400 and "already exists" in response.json()["detail"]

    def test_a_client_cannot_reach_the_admin_routes(self, world):
        self.connected(world)
        assert world.browser("alpha").get(ADMIN).status_code == 401
        assert world.browser("alpha").get("/admin/v1/whatsapp/platform").status_code == 401

    def test_the_admin_account_view_carries_no_secret(self, world):
        admin = self.connected(world)
        text = admin.get(ADMIN).text + admin.get("/admin/v1/whatsapp/platform").text
        assert TOKEN not in text and APP_SECRET not in text
        body = admin.get(ADMIN).json()
        assert body["waba_id"] == WABA and body["state"] == "connected"

    def test_refresh_asks_meta_and_a_refused_token_marks_a_reconnection(self, world):
        admin = self.connected(world)
        assert admin.post(f"{ADMIN}/refresh").json()["quality"] == "YELLOW"
        world.graph.fail["phone_number"] = GraphError("expired", code="190", status=401)
        assert admin.post(f"{ADMIN}/refresh").status_code == 401
        assert world.browser("alpha").get(BASE).json()["state"] == "reauth_required"

    def test_the_platform_view_says_what_is_missing_and_which_urls_meta_needs(self, world):
        admin = world.admin()
        body = admin.get("/admin/v1/whatsapp/platform").json()
        assert body["ready"] is False and "Meta app id" in body["missing"]
        configure_platform(world)
        body = admin.get("/admin/v1/whatsapp/platform").json()
        assert body["ready"] is True and body["missing"] == []


def signed_request(payload, secret=APP_SECRET):
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    encoded = b64(json.dumps(payload).encode())
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{b64(signature)}.{encoded}"


class TestMetasOwnCalls:
    def test_deauthorisation_forgets_the_account_of_that_meta_user(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        response = world.anonymous().post(
            "/webhooks/whatsapp/deauthorize",
            content="signed_request=" + signed_request({"user_id": "fbuser-1"}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        assert secret(world, "whatsapp.access_token") is None

    def test_data_deletion_answers_with_a_status_url_and_a_code(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        response = world.anonymous().post(
            "/webhooks/whatsapp/data-deletion",
            content="signed_request=" + signed_request({"user_id": "fbuser-1"}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200 and response.json()["confirmation_code"]
        assert secret(world, "whatsapp.access_token") is None

    @pytest.mark.parametrize(
        "forged", [signed_request({"user_id": "fbuser-1"}, "wrong"), "garbage", ""]
    )
    def test_a_call_the_app_secret_does_not_sign_is_refused_and_changes_nothing(
        self, world, forged
    ):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        response = world.anonymous().post(
            "/webhooks/whatsapp/deauthorize",
            content="signed_request=" + forged,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 401
        assert secret(world, "whatsapp.access_token") == TOKEN
