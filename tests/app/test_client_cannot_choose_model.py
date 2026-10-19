"""A Client never chooses, reads or changes the model that runs its agent: only an Admin assigns one.

Three guards. The Client API has no route and no field for a provider or a model (checked on its
route list and on its generated OpenAPI contract, so a new route or field must be reviewed here).
A Client who tries to send one is refused (422 on the one body that accepts changes) or, where a body
only reads fixed fields, it is never read. And nothing a Client legitimately does, renaming itself,
can undo what the Admin assigned."""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from test_admin_client_apply import Harness

from sales_app.admin_auth.passwords import hash_password
from sales_app.api import create_app
from sales_app.api.client import COOKIE_NAME as CLIENT_COOKIE
from sales_app.client_profiles import ClientProfile

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
CLIENT_PASSWORD = "client-pass-123"
SRC = Path(__file__).resolve().parents[2] / "src"
MODEL_WORDS = re.compile(r"llm|model|provider|engine|brain", re.IGNORECASE)

# Every route of the Client API. A new one makes the first test fail on purpose: whoever adds it
# must check, here, that it carries no model.
CLIENT_ROUTES = {
    ("GET", "/client/v1/channel/google"),
    ("GET", "/client/v1/channel/google/callback"),
    ("GET", "/client/v1/reporting"),
    ("GET", "/client/v1/reports"),
    ("GET", "/client/v1/reports/{day}"),
    ("POST", "/client/v1/channel/email-activation"),
    ("POST", "/client/v1/channel/google/disconnect"),
    ("POST", "/client/v1/channel/google/start"),
    ("POST", "/client/v1/reports/generate"),
    ("POST", "/client/v1/reports/{day}/resend"),
    ("PUT", "/client/v1/reporting/settings"),
    ("GET", "/client/v1/approvals"),
    ("POST", "/client/v1/approvals/{action_id}/approve"),
    ("POST", "/client/v1/approvals/{action_id}/reject"),
    ("POST", "/client/v1/auth/login"),
    ("POST", "/client/v1/auth/logout"),
    ("GET", "/client/v1/auth/me"),
    ("GET", "/client/v1/profile"),
    ("PATCH", "/client/v1/profile"),
    ("POST", "/client/v1/profile/password"),
    # Lot A: the client's own commercial settings and its agent's switch. Checked below: no field
    # of any of them is a provider, a model or a Brain (the Brain's document travels as `commercial`).
    ("GET", "/client/v1/settings"),
    ("PUT", "/client/v1/settings"),
    ("GET", "/client/v1/readiness"),
    ("GET", "/client/v1/agent"),
    ("POST", "/client/v1/agent/start"),
    ("POST", "/client/v1/agent/pause"),
    # Lot B: the prospect source (a CRM or a spreadsheet, never a model; the API field is `source`).
    ("GET", "/client/v1/source"),
    ("PUT", "/client/v1/source"),
    ("POST", "/client/v1/source/test"),
    # Lot E: the channel (e-mail, Telegram or WhatsApp), chosen and worded by the client; the API
    # field is `channel`. Connecting it is the platform's work: there is no route for a credential,
    # a test of a server or a bot, or a connection (see `TestTheClientApiCarriesNothingTechnical`).
    ("GET", "/client/v1/channel"),
    ("PUT", "/client/v1/channel"),
    # Telegram: the links that let the client's prospects start the platform-connected bot.
    ("GET", "/client/v1/channel/invitations"),
    # The client's prospects, their conversations and its dashboard summary (read-only).
    ("GET", "/client/v1/prospects"),
    ("GET", "/client/v1/conversations/{prospect_id}"),
    ("GET", "/client/v1/summary"),
    # Lanes: each with its own source, channel and products (no model, no provider key).
    ("GET", "/client/v1/lanes"),
    ("POST", "/client/v1/lanes"),
    ("GET", "/client/v1/lanes/{lane_id}"),
    ("PUT", "/client/v1/lanes/{lane_id}"),
    ("DELETE", "/client/v1/lanes/{lane_id}"),
    # Results copied back to the source, chosen per lane by the client (columns / properties).
    ("GET", "/client/v1/lanes/{lane_id}/sync"),
    ("PUT", "/client/v1/lanes/{lane_id}/sync"),
    # The client's own authorisation of Google Sheets and HubSpot (consent on the provider's page;
    # no token, password or key ever crosses these routes). Reviewed: no model, no provider key.
    ("GET", "/client/v1/sheets-access"),
    ("GET", "/client/v1/sheets-access/callback"),
    ("POST", "/client/v1/sheets-access/start"),
    ("POST", "/client/v1/sheets-access/disconnect"),
    ("GET", "/client/v1/hubspot-access"),
    ("GET", "/client/v1/hubspot-access/callback"),
    ("POST", "/client/v1/hubspot-access/start"),
    ("POST", "/client/v1/hubspot-access/disconnect"),
    # Product documents the agent reads, and the payments the client confirms.
    ("GET", "/client/v1/products/{product_id}/resources"),
    ("POST", "/client/v1/products/{product_id}/resources/file"),
    ("POST", "/client/v1/products/{product_id}/resources/link"),
    ("DELETE", "/client/v1/products/{product_id}/resources/{resource_id}"),
    ("GET", "/client/v1/products/{product_id}/resources/{resource_id}/download"),
    ("POST", "/client/v1/products/{product_id}/resources/{resource_id}/retry"),
    ("GET", "/client/v1/payments"),
    ("POST", "/client/v1/payments/{prospect_id}/confirm"),
}


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    harness.application.client_profiles.create(
        ClientProfile(
            client_id="alpha",
            login="alpha.login",
            password_hash=hash_password(CLIENT_PASSWORD),
            must_change_password=False,
            created_at=NOW,
        )
    )
    yield harness
    harness.application.close()


@pytest.fixture
def client(h):
    """A Client logged in as alpha (its own session, apart from the Admin's)."""
    browser = TestClient(
        create_app(h.application, run_scheduler=False), base_url="https://testserver"
    )
    login = browser.post(
        "/client/v1/auth/login", json={"login": "alpha.login", "password": CLIENT_PASSWORD}
    )
    assert login.status_code == 200, login.text
    return browser


def unchanged(h, model="test-model"):
    """The model assigned to alpha, published and running, is still `model`."""
    published = h.application.config_store.current("alpha")
    if published is not None:
        assert published.document["llm"]["model"] == model
    assert h.running_config().llm.model == model


class TestTheClientApiCarriesNoModel:
    def test_these_are_all_its_routes(self, h):
        spec = create_app(h.application, run_scheduler=False).openapi()
        found = {
            (method.upper(), path)
            for path, operations in spec["paths"].items()
            if path.startswith("/client/v1")
            for method in operations
        }
        assert found == CLIENT_ROUTES

    def test_no_client_route_is_hidden_from_that_contract(self):
        source = (SRC / "sales_app" / "api" / "client.py").read_text(encoding="utf-8")
        assert "include_in_schema" not in source  # every route is in the contract checked here

    def test_no_request_or_response_field_is_a_provider_or_a_model(self, h):
        spec = create_app(h.application, run_scheduler=False).openapi()
        schemas = spec["components"]["schemas"]

        def names(node, seen=()):
            """Every property name reachable from a schema, following references."""
            if isinstance(node, dict):
                if "$ref" in node:
                    ref = node["$ref"].rsplit("/", 1)[-1]
                    return set() if ref in seen else names(schemas[ref], (*seen, ref))
                found = set(node.get("properties", {}))
                for value in node.values():
                    found |= names(value, seen)
                return found
            if isinstance(node, list):
                return set().union(*(names(item, seen) for item in node)) if node else set()
            return set()

        checked = 0
        for path, operations in spec["paths"].items():
            if not path.startswith("/client/v1"):
                continue
            for operation in operations.values():
                checked += 1
                fields = names(operation)
                parameters = {p["name"] for p in operation.get("parameters", [])}
                assert not {f for f in fields | parameters if MODEL_WORDS.search(f)}, (path, fields)
        assert checked == len(CLIENT_ROUTES)

    def test_the_profile_a_client_reads_says_nothing_of_the_model(self, h, client):
        body = client.get("/client/v1/profile").json()
        assert set(body) == {
            "name",
            "login",
            "whatsapp_number",
            "status",
            "must_change_password",
            "whatsapp_reconnect_required",
        }
        assert "test-model" not in client.get("/client/v1/auth/me").text


TECHNICAL_PATH = re.compile(r"credential|connect|webhook|token|smtp|imap|secret", re.IGNORECASE)
TECHNICAL_FIELD = re.compile(
    r"password|token|secret|credential|webhook|smtp|imap|^username$|from_address|mailbox|"
    r"connected|api_key|^host$|_host$|^port$|_port$",  # a server's host or port, not "reports"
    re.IGNORECASE,
)


# The client's own Google authorization (Gmail mode) is the one place it acts on a connection: it
# consents on Google's page, which Revansa never sees the password of, and it can withdraw that
# consent. Neither route takes or gives a token, a password or a server.
OWN_GOOGLE_AUTHORIZATION = {
    "/client/v1/channel/google/disconnect",
    # The same kind of consent for Google Sheets and HubSpot (a click, then the provider's page).
    "/client/v1/sheets-access",
    "/client/v1/sheets-access/callback",
    "/client/v1/sheets-access/start",
    "/client/v1/sheets-access/disconnect",
    "/client/v1/hubspot-access",
    "/client/v1/hubspot-access/callback",
    "/client/v1/hubspot-access/start",
    "/client/v1/hubspot-access/disconnect",
}
# `mailbox_sync` is a status word about reading replies, not a mailbox setting.
STATUS_FIELDS = {
    "/client/v1/channel/google": {"mailbox_sync"},
    "/client/v1/channel/google/disconnect": {"mailbox_sync"},
}


class TestTheClientApiCarriesNothingTechnical:
    """The client never configures a channel's or a source's technology: nothing in its API takes or
    gives a token, a password, a server, a login or a webhook. The platform (Admin) does that."""

    def test_no_client_route_is_about_a_credential_or_a_connection(self, h):
        spec = create_app(h.application, run_scheduler=False).openapi()
        paths = [p for p in spec["paths"] if p.startswith("/client/v1")]
        assert paths and not [
            p for p in paths if TECHNICAL_PATH.search(p) and p not in OWN_GOOGLE_AUTHORIZATION
        ]

    def test_no_request_or_response_field_is_technical(self, h):
        spec = create_app(h.application, run_scheduler=False).openapi()
        schemas = spec["components"]["schemas"]

        def names(node, seen=()):
            if isinstance(node, dict):
                if "$ref" in node:
                    ref = node["$ref"].rsplit("/", 1)[-1]
                    return set() if ref in seen else names(schemas[ref], (*seen, ref))
                found = set(node.get("properties", {}))
                for value in node.values():
                    found |= names(value, seen)
                return found
            if isinstance(node, list):
                return set().union(*(names(item, seen) for item in node)) if node else set()
            return set()

        # `/profile/password` is the client's own login password for Revansa, not a technical secret.
        for path, operations in spec["paths"].items():
            if not path.startswith("/client/v1") or path.startswith("/client/v1/profile"):
                continue
            if path.startswith("/client/v1/auth"):
                continue
            for operation in operations.values():
                fields = names(operation) | {p["name"] for p in operation.get("parameters", [])}
                allowed = STATUS_FIELDS.get(path, set())
                assert not {f for f in fields - allowed if TECHNICAL_FIELD.search(f)}, (
                    path,
                    fields,
                )


FORGED = [
    {"llm": {"provider": "openai", "model": "model-x"}},
    {"model": "model-x"},
    {"llm_model": "model-x"},
    {"llm_provider": "openai"},
    {"provider": "openai", "model": "model-x"},
    {"model_id": "model-x"},
    {"agent": "other-brain"},
    {"name": "A Legitimate Name", "llm": {"model": "model-x"}},  # mixed with a real change
]


class TestAClientWhoTriesToChooseOneIsRefused:
    @pytest.mark.parametrize("body", FORGED, ids=lambda b: "+".join(sorted(b)))
    def test_the_profile_update_answers_422_and_changes_nothing(self, h, client, body):
        response = client.patch("/client/v1/profile", json=body)
        assert response.status_code == 422
        unchanged(h)
        assert h.detail()["display_name"] == "Alpha"  # not even the legitimate part went through

    def test_the_422_is_the_contract_of_that_body(self):
        from sales_app.api.client import UpdateProfileRequest

        assert UpdateProfileRequest.model_config["extra"] == "forbid"
        assert set(UpdateProfileRequest.model_fields) == {"name", "login", "whatsapp_number"}

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            (
                "/client/v1/auth/login",
                {"login": "alpha.login", "password": CLIENT_PASSWORD, "model": "model-x"},
            ),
            (
                "/client/v1/profile/password",
                {
                    "current_password": CLIENT_PASSWORD,
                    "new_password": "another-pass-456",
                    "confirm_password": "another-pass-456",
                    "llm": {"model": "model-x"},
                },
            ),
        ],
        ids=["login", "password"],
    )
    def test_where_a_body_only_reads_fixed_fields_a_forged_one_is_never_read(
        self, h, client, path, body
    ):
        assert client.post(path, json=body).status_code == 200
        unchanged(h)

    def test_what_a_client_may_change_still_works_and_leaves_the_model_alone(self, h, client):
        assert client.patch("/client/v1/profile", json={"name": "Alpha Renamed"}).status_code == 200
        assert client.get("/client/v1/profile").json()["name"] == "Alpha Renamed"
        assert h.application.config_store.current("alpha").document["display_name"] == (
            "Alpha Renamed"
        )
        assert h.detail()["display_name"] == "Alpha Renamed"  # the Admin sees it at once too
        unchanged(h, model="test-model")


class TestAClientSessionOpensNothingOfTheAdmin:
    ADMIN_WRITES: ClassVar[list] = [
        ("PUT", "/admin/v1/clients/alpha/llm", {"provider": "openai", "model": "model-x"}),
        ("PATCH", "/admin/v1/clients/alpha", {"llm_provider": "openai", "llm_model": "model-x"}),
        ("POST", "/admin/v1/settings/openai/models", {"name": "X", "id": "x-id", "active": True}),
        (
            "PUT",
            "/admin/v1/settings/openai/models/model-x",
            {"name": "X", "id": "z", "active": True},
        ),
        ("DELETE", "/admin/v1/settings/openai/models/model-x", None),
        ("POST", "/admin/v1/settings/openai/models/model-x/test?confirm=true", None),
        ("PUT", "/admin/v1/settings/integrations/openai.models", {"value": "[]"}),
    ]

    def token(self, client):
        return client.cookies.get(CLIENT_COOKIE)

    @pytest.mark.parametrize(("method", "path", "body"), ADMIN_WRITES)
    def test_every_admin_route_that_touches_a_model_refuses_it(self, h, client, method, path, body):
        token = self.token(client)
        assert token
        for cookie in (
            CLIENT_COOKIE,
            "revansa_admin_session",
        ):  # as it is, and passed off as an Admin's
            stranger = TestClient(
                create_app(h.application, run_scheduler=False), base_url="https://testserver"
            )
            response = stranger.request(
                method, path, json=body, headers={"Cookie": f"{cookie}={token}"}
            )
            assert response.status_code == 401, (cookie, response.text)
        unchanged(h)
        assert h.application.config_store.current("alpha") is None


class TestWhatAClientDoesCannotUndoTheAdminsChoice:
    def test_renaming_itself_keeps_a_model_the_runtime_has_not_caught_up_with(self, h, client):
        # The Admin's assignment is published but could not be applied: the client still runs on
        # the old model. A rename publishes a new version; it must start from what is published.
        assert h.assign("boom").status_code == 200
        assert h.status()["apply_state"] == "failed"
        assert h.running_config().llm.model == "test-model"
        assert (
            client.patch("/client/v1/profile", json={"name": "Chosen By Client"}).status_code == 200
        )
        document = h.application.config_store.current("alpha").document
        assert document["llm"] == {"provider": "openai", "model": "boom"}
        assert document["display_name"] == "Chosen By Client"

    def test_the_next_good_assignment_still_replaces_it(self, h, client):
        h.assign("boom")
        client.patch("/client/v1/profile", json={"name": "Chosen By Client"})
        assert h.assign("model-ok").status_code == 200
        h.wait_for("applied")
        assert h.running_config().llm.model == "model-ok"
        assert h.running_config().display_name == "Chosen By Client"


class TestARenameByTheClientDoesNotRebuildTheAgent:
    def test_the_name_is_published_and_shown_to_the_client_at_once(self, h, client):
        engine = h.application.registry.get("alpha").engine
        built = len(h.built)
        response = client.patch("/client/v1/profile", json={"name": "Chosen By Client"})
        assert response.status_code == 200
        assert (
            response.json()["name"] == "Chosen By Client"
        )  # the Client API reads what is published
        assert client.get("/client/v1/profile").json()["name"] == "Chosen By Client"
        assert h.application.config_store.current("alpha").document["display_name"] == (
            "Chosen By Client"
        )
        assert h.application.registry.get("alpha").engine is engine
        assert len(h.built) == built  # no Engine was built for a rename

    def test_the_admin_sees_the_published_name_at_once_while_the_agent_is_not_rebuilt(
        self, h, client
    ):
        engine = h.application.registry.get("alpha").engine
        built = len(h.built)
        client.patch("/client/v1/profile", json={"name": "Chosen By Client"})
        # Every Admin screen that names the client shows the name the client chose...
        assert h.detail()["display_name"] == "Chosen By Client"
        assert h.listed()["display_name"] == "Chosen By Client"
        agent = h.client.get("/admin/v1/agents/alpha").json()
        assert agent["display_name"] == "Chosen By Client"
        listed = {
            a["client_id"]: a["display_name"] for a in h.client.get("/admin/v1/agents").json()
        }
        assert listed["alpha"] == "Chosen By Client"
        # ...while nothing was rebuilt, and what runs is still shown as what runs.
        assert h.application.registry.get("alpha").engine is engine and len(h.built) == built
        assert h.running_config().display_name == "Alpha"
        assert h.detail()["llm_model"] == "test-model"

    def test_only_the_name_is_taken_from_what_is_published(self, h, client):
        # A model published but not applied is still shown as the model that runs.
        h.assign("boom")  # published, refresh fails: the agent keeps its model
        client.patch("/client/v1/profile", json={"name": "Chosen By Client"})
        detail = h.detail()
        assert detail["display_name"] == "Chosen By Client"
        assert detail["llm_model"] == "test-model"

    def test_the_name_is_the_newest_published_one(self, h, client):
        client.patch("/client/v1/profile", json={"name": "First"})
        h.client.patch("/admin/v1/clients/alpha", json={"display_name": "Admin Chose"})
        assert h.detail()["display_name"] == "Admin Chose"
        client.patch("/client/v1/profile", json={"name": "Client Again"})
        assert h.detail()["display_name"] == "Client Again"

    def test_a_clients_rename_changes_no_other_client_shown_name(self, tmp_path):
        d = Harness(tmp_path, disabled=("dormant",))
        try:
            d.application.client_profiles.create(
                ClientProfile(
                    client_id="alpha",
                    login="alpha.login",
                    password_hash=hash_password(CLIENT_PASSWORD),
                    must_change_password=False,
                    created_at=NOW,
                )
            )
            before = d.detail("dormant")["display_name"]
            browser = TestClient(
                create_app(d.application, run_scheduler=False), base_url="https://testserver"
            )
            browser.post(
                "/client/v1/auth/login", json={"login": "alpha.login", "password": CLIENT_PASSWORD}
            )
            browser.patch("/client/v1/profile", json={"name": "Only Alpha"})
            assert d.detail("alpha")["display_name"] == "Only Alpha"
            assert d.detail("dormant")["display_name"] == before
        finally:
            d.application.close()

    def test_a_disabled_agent_shows_the_published_name_too(self, tmp_path):
        d = Harness(tmp_path, disabled=("dormant",))
        try:
            d.client.patch("/admin/v1/clients/dormant", json={"display_name": "Sleeper"})
            assert d.client.get("/admin/v1/agents/dormant").json()["display_name"] == "Sleeper"
            assert d.detail("dormant")["display_name"] == "Sleeper"
        finally:
            d.application.close()

    def test_a_client_cannot_make_the_agent_rebuild_by_renaming_itself_again_and_again(
        self, h, client
    ):
        built = len(h.built)
        for number in range(5):
            assert (
                client.patch("/client/v1/profile", json={"name": f"Name {number}"}).status_code
                == 200
            )
        assert len(h.built) == built
        versions = h.application.config_store.history("alpha")
        assert versions[-1].document["display_name"] == "Name 4"

    def test_the_admins_own_rename_still_applies_at_once(self, h):
        response = h.client.patch("/admin/v1/clients/alpha", json={"display_name": "Admin Name"})
        assert response.status_code == 200
        assert h.running_config().display_name == "Admin Name"


class TestTheAdminsOwnLaterChangesKeepTheModelToo:
    def test_a_quota_change_does_not_undo_an_assignment_still_being_applied(self, h):
        h.assign("boom")
        response = h.client.patch("/admin/v1/clients/alpha", json={"daily_action_limit": 9})
        assert response.status_code == 200
        document = h.application.config_store.current("alpha").document
        assert document["llm"]["model"] == "boom" and document["daily_action_limit"] == 9

    def test_two_changes_in_a_row_on_a_disabled_client_keep_both(self, tmp_path):
        d = Harness(tmp_path, disabled=("dormant",))
        try:
            d.client.patch(
                "/admin/v1/clients/dormant", json={"llm_provider": "openai", "llm_model": "model-d"}
            )
            d.client.patch("/admin/v1/clients/dormant", json={"daily_action_limit": 9})
            document = d.application.config_store.current("dormant").document
            assert document["llm"]["model"] == "model-d" and document["daily_action_limit"] == 9
            assert d.detail("dormant")["llm"]["model"] == "model-d"
        finally:
            d.application.close()
