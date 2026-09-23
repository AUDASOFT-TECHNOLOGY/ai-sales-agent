"""HTTP surface: health and the WhatsApp webhook. Thin routing only; no commercial logic."""

import json
import time

import pytest
from app_support import VERIFY_TOKEN, app_secret, build_test_application, phone_id
from engine_harness import phone_for, prospect
from fastapi.testclient import TestClient

from sales_app.api import create_app
from sales_core.contracts import ActionStatus, ClientId, Direction
from sales_core.engine import InboundReceipt, InboundStatus, RunReport
from sales_integrations.whatsapp import compute_signature


def payload(phone: str, sender: str, text: str = "Hi there", wamid: str = "wamid.1", **extra):
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "100", "phone_number_id": phone},
        "messages": [
            {
                "from": sender,
                "id": wamid,
                "timestamp": "1768467600",
                "type": "text",
                "text": {"body": text},
            }
        ],
    } | extra
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}],
    }


def post(client, body, *, secret=None, headers=None, provider="whatsapp"):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    all_headers = {"Content-Type": "application/json"} | (headers or {})
    if secret is not None:
        all_headers["X-Hub-Signature-256"] = compute_signature(secret, raw)
    return client.post(f"/webhooks/{provider}", content=raw, headers=all_headers)


class Fixture:
    """Two clients (alpha, beta), each with one known prospect, behind the real webhook parser."""

    def __init__(self, tmp_path, **kw):
        self.application, self.made, self.clock = build_test_application(
            tmp_path, "alpha", "beta", **kw
        )
        for cid in ("alpha", "beta"):
            self.made.repositories[cid].add_prospects(prospect("p1", client_id=ClientId(cid)))
        self.client = TestClient(create_app(self.application, run_scheduler=False))

    def send(self, cid="alpha", *, text="Hi there", wamid="wamid.1", secret=None):
        body = payload(phone_id(cid), phone_for("p1").lstrip("+"), text, wamid)
        return post(self.client, body, secret=secret if secret is not None else app_secret(cid))


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)


class TestHealth:
    def test_liveness(self, fx):
        assert fx.client.get("/health").json() == {"status": "ok"}

    def test_client_health_reports_status_and_mode_without_secrets(self, fx):
        response = fx.client.get("/health/clients")
        entries = {e["client_id"]: e for e in response.json()}
        assert set(entries) == {"alpha", "beta"}
        assert entries["alpha"]["mode"] == "approval" and entries["alpha"]["status"] == "ready"
        for secret in ("token-alpha", "secret-alpha", "sk-test", "creds.json", VERIFY_TOKEN):
            assert secret not in response.text

    def test_disabled_clients_are_listed_as_disabled(self, tmp_path):
        (tmp_path / "gamma").mkdir()
        (tmp_path / "gamma" / "client.yaml").write_text("enabled: false\n")
        fixture = Fixture(tmp_path)
        assert {"client_id": "gamma", "status": "disabled"} in fixture.client.get(
            "/health/clients"
        ).json()


class TestHandshake:
    def test_echoes_the_challenge_for_the_right_token(self, fx):
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1234",
        }
        response = fx.client.get("/webhooks/whatsapp", params=params)
        assert response.status_code == 200 and response.text == "1234"

    @pytest.mark.parametrize(
        "params",
        [
            {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
            {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
            {},
        ],
    )
    def test_refuses_anything_else(self, fx, params):
        assert fx.client.get("/webhooks/whatsapp", params=params).status_code == 403

    def test_unknown_provider(self, fx):
        assert fx.client.get("/webhooks/sms").status_code == 404
        assert post(fx.client, {}, provider="sms").status_code == 404


class TestInboundRouting:
    def test_a_message_reaches_only_the_client_that_owns_the_number(self, fx):
        response = fx.send("alpha")
        assert response.status_code == 200 and response.json() == {"received": 1}
        alpha, beta = fx.made.repositories["alpha"], fx.made.repositories["beta"]
        assert [m.direction for m in alpha.messages] == [Direction.INBOUND]
        assert beta.messages == () and beta.list_actions() == []  # untouched

    def test_the_other_client_gets_its_own_messages(self, fx):
        fx.send("beta", wamid="wamid.b")
        assert len(fx.made.repositories["beta"].messages) == 1
        assert fx.made.repositories["alpha"].messages == ()

    def test_the_reply_is_processed_after_the_fast_path_by_that_clients_engine(self, fx):
        fx.send("alpha", text="Tell me more")
        (action,) = fx.made.repositories["alpha"].list_actions()
        assert action.status is ActionStatus.PROPOSED  # approval mode: waits for a human
        assert action.draft.text.startswith("[alpha] hello")  # alpha's Brain, alpha's settings
        assert fx.made.channels["alpha"].attempts == ()
        assert fx.made.repositories["beta"].list_actions() == []

    def test_a_signature_from_another_clients_secret_is_rejected(self, fx):
        response = fx.send("alpha", secret=app_secret("beta"))
        assert response.status_code == 401
        assert fx.made.repositories["alpha"].messages == ()

    def test_duplicate_deliveries_are_handled_once(self, fx):
        fx.send("alpha", wamid="wamid.same")
        fx.send("alpha", wamid="wamid.same")
        alpha = fx.made.repositories["alpha"]
        assert len(alpha.messages) == 1 and len(alpha.list_actions()) == 1

    def test_an_opt_out_never_reaches_the_brain(self, fx):
        fx.send("alpha", text="STOP")
        (action,) = fx.made.repositories["alpha"].list_actions()
        assert action.status is ActionStatus.COMPLETED and action.draft is None
        assert fx.made.channels["alpha"].attempts == ()


class TestWebhookResponses:
    def test_invalid_signature_is_401_and_nothing_is_stored(self, fx):
        response = post(fx.client, payload(phone_id("alpha"), "1"), secret="not-the-secret")
        assert response.status_code == 401
        assert fx.made.repositories["alpha"].messages == ()

    def test_missing_signature_is_401(self, fx):
        assert post(fx.client, payload(phone_id("alpha"), "1")).status_code == 401

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"[]",
            b'{"object": "page"}',
            b'{"object": "whatsapp_business_account", "entry": [{"changes": [{}]}]}',
        ],
    )
    def test_malformed_payloads_are_400(self, fx, body):
        assert post(fx.client, body).status_code == 400

    def test_unknown_phone_number_id_is_200_and_logged_as_an_error(self, fx, caplog):
        with caplog.at_level("ERROR"):
            response = post(fx.client, payload("nobody-owns-this", "1"))
        assert response.status_code == 200 and response.json() == {"received": 0}
        assert "no client" in caplog.text and "nobody-owns-this" in caplog.text
        assert all(r.messages == () for r in fx.made.repositories.values())

    def test_status_only_payloads_are_acknowledged_and_ignored(self, fx):
        body = payload(phone_id("alpha"), "1")
        body["entry"][0]["changes"][0]["value"] = {
            "metadata": {"phone_number_id": phone_id("alpha")},
            "statuses": [{"id": "wamid.9", "status": "delivered"}],
        }
        response = post(fx.client, body, secret=app_secret("alpha"))
        assert response.status_code == 200 and response.json() == {"received": 0}

    def test_a_storage_failure_is_500_so_the_provider_retries_and_the_retry_works(self, fx):
        fx.made.repositories["alpha"].fail_next("find_prospect_by_contact")
        assert fx.send("alpha").status_code == 500
        health = {e["client_id"]: e for e in fx.client.get("/health/clients").json()}
        assert (
            health["alpha"]["status"] == "degraded"
            and "injected failure" in health["alpha"]["last_error"]
        )
        assert health["beta"]["status"] == "ready"  # the other client is unaffected

        assert fx.send("alpha").status_code == 200  # Meta redelivers
        assert len(fx.made.repositories["alpha"].messages) == 1
        recovered = {e["client_id"]: e for e in fx.client.get("/health/clients").json()}
        assert recovered["alpha"]["status"] == "ready"

    def test_a_failure_for_one_client_is_logged_with_its_id(self, fx, caplog):
        fx.made.repositories["alpha"].fail_next("find_prospect_by_contact")
        with caplog.at_level("ERROR"):
            fx.send("alpha")
        assert any(getattr(r, "client_id", None) == "alpha" for r in caplog.records)


class SpyEngine:
    def __init__(self, fail_on_process=False):
        self.events, self.fail_on_process = [], fail_on_process
        self.receipt = InboundReceipt(
            status=InboundStatus.STORED, message_id="m1", prospect_id="p1"
        )

    def receive_inbound(self, message):
        self.events.append(("receive", message))
        return self.receipt

    def process_inbound(self, receipt):
        self.events.append(("process", receipt))
        if self.fail_on_process:
            raise RuntimeError("brain unreachable")
        return RunReport(errors=("something to look at",))


class TestTheWebhookIsThin:
    def test_it_stores_first_then_hands_the_receipt_to_background_processing(self, fx):
        spy = fx.application.registry.get("alpha").engine = SpyEngine()
        response = fx.send("alpha", text="Hello")
        assert response.status_code == 200
        (kind1, message), (kind2, receipt) = spy.events  # nothing else was called
        assert (kind1, kind2) == ("receive", "process")
        assert message.text == "Hello" and message.sender == phone_for("p1").lstrip("+")
        assert receipt is spy.receipt

    def test_a_crash_in_background_processing_is_logged_and_flags_the_client(self, fx, caplog):
        fx.application.registry.get("alpha").engine = SpyEngine(fail_on_process=True)
        with caplog.at_level("ERROR"):
            response = fx.send("alpha")
        assert response.status_code == 200  # the message was stored; recovery will pick it up
        assert any(getattr(r, "client_id", None) == "alpha" for r in caplog.records)
        health = {e["client_id"]: e for e in fx.client.get("/health/clients").json()}
        assert health["alpha"]["status"] == "degraded"

    def test_engine_reported_problems_are_logged_with_the_client_id(self, fx, caplog):
        fx.application.registry.get("alpha").engine = SpyEngine()
        with caplog.at_level("ERROR"):
            fx.send("alpha")
        assert any(
            "something to look at" in r.getMessage() and getattr(r, "client_id", None) == "alpha"
            for r in caplog.records
        )


class TestLifespan:
    def test_the_scheduler_runs_while_the_app_is_up_and_stops_with_it(self, tmp_path):
        application, made, _ = build_test_application(tmp_path, "alpha")
        with TestClient(create_app(application, run_scheduler=True)) as client:
            deadline = time.time() + 3
            runs: dict = {}
            while time.time() < deadline and "daily" not in runs:
                time.sleep(0.05)
                entry = {e["client_id"]: e for e in client.get("/health/clients").json()}["alpha"]
                runs = entry["last_runs"]
            assert "daily" in runs  # the first tick ran the daily cycle (Thursday 10:00 in Tunis)
        assert made.repositories["alpha"].list_actions() == []  # no prospects, so nothing to plan
