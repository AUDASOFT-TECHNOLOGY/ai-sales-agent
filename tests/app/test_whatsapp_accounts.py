# ruff: noqa: F811
"""The WhatsApp account of each client as a relation: owner verified, associations unique, routing
persisted, billing never claimed without evidence.

Meta is replaced by a fake Graph API: these tests prove what Revansa asks of Meta, checks and keeps,
NOT that Meta names the owner of a WABA the way the fake does. That needs a real Meta account (see
docs/whatsapp-channel.md, "Needs a real Meta account").
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from test_whatsapp_connection import (  # noqa: F401 - `world` is a fixture
    ADMIN,
    BASE,
    OWNER,
    PHONE,
    PLATFORM_BUSINESS,
    TOKEN,
    WABA,
    FakeGraph,
    configure_platform,
    connect,
    secret,
    signed_request,
    world,
)

from sales_app.persistence.tables import metadata
from sales_app.persistence.whatsapp_rows import SqlWhatsAppState
from sales_app.whatsapp_state import (
    BILLING_CONFIRMED,
    BILLING_TO_CONFIGURE,
    BILLING_UNVERIFIABLE,
    AccountConflict,
    AccountRecord,
    InMemoryWhatsAppState,
)
from sales_integrations.whatsapp import ACCOUNT, DELIVERY, WhatsAppNotification

PHONE_B, WABA_B, OWNER_B, TOKEN_B = "666666666666", "222222222222", "800000000002", "EAAG-beta"


class BetaGraph(FakeGraph):
    """Another client's account at Meta: its own Portfolio, WABA, number and token."""

    def __init__(self):
        super().__init__(display="+216 20 000 002")
        self.owner = {"id": OWNER_B, "name": "Beta SARL Holding"}

    def exchange_code(self, *, app_id, app_secret, code):
        self._do("exchange", code)
        return TOKEN_B

    def granted_accounts(self, *, app_id, app_secret, token):
        return [WABA_B]

    def phone_numbers(self, token, waba):
        if waba != WABA_B:
            from sales_integrations.whatsapp import GraphError

            raise GraphError("unsupported get request", code="100", status=400)
        return [
            {
                "id": PHONE_B,
                "display_phone_number": self.display,
                "verified_name": "Beta SARL",
                "quality_rating": "GREEN",
                "status": "PENDING",
            }
        ]


def service(world):
    return world.application.whatsapp


def status_failed(waba, phone, at, code="131042", mid="wamid.1"):
    return WhatsAppNotification(
        DELIVERY,
        waba,
        phone,
        {
            "message_id": mid,
            "status": "failed",
            "at": at.isoformat(),
            "error_code": code,
            "error_title": "payment",
        },
    )


def status_ok(waba, phone, at, mid="wamid.2"):
    return WhatsAppNotification(
        DELIVERY,
        waba,
        phone,
        {"message_id": mid, "status": "delivered", "at": at.isoformat()},
    )


class TestOwnership:
    def test_the_owner_meta_names_is_kept_and_shown(self, world):
        configure_platform(world)
        body = connect(world, world.browser("alpha")).json()
        assert body["ownership"] == "verified" and body["business_name"] == "Alpha Holding"
        row = service(world).state.account("alpha")
        assert (row.business_id, row.waba_id, row.phone_number_id) == (OWNER, WABA, PHONE)
        admin = world.admin().get(ADMIN).json()
        assert admin["business_id"] == OWNER and admin["ownership"] == "verified"

    def test_reaching_the_account_is_not_proof_of_ownership(self, world):
        configure_platform(world)
        world.graph.owner = None  # the token reads the account, Meta names no owner
        response = connect(world, world.browser("alpha"))
        assert response.status_code == 502 and response.json()["detail"] == "ownership_unverifiable"
        names = [c[0] for c in world.graph.calls]
        assert "subscribe" not in names and "register" not in names
        assert secret(world, "whatsapp.access_token") is None
        # Only the account adopted at start (owner never verified) is there: nothing was claimed.
        assert service(world).state.account("alpha").ownership == "legacy_unverified"

    def test_an_account_owned_by_revansa_is_refused(self, world):
        configure_platform(world)
        world.graph.owner = {"id": PLATFORM_BUSINESS, "name": "Revansa"}
        response = connect(world, world.browser("alpha"))
        assert response.status_code == 409 and response.json()["detail"] == "platform_owned"
        assert secret(world, "whatsapp.access_token") is None
        assert "subscribe" not in [c[0] for c in world.graph.calls]

    def test_missing_permissions_are_refused_before_anything_is_read(self, world):
        configure_platform(world)
        world.graph.missing = ["whatsapp_business_messaging"]
        response = connect(world, world.browser("alpha"))
        assert response.status_code == 403 and response.json()["detail"] == "denied"
        assert "waba_owner" not in [c[0] for c in world.graph.calls]

    def test_without_revansas_own_portfolio_id_nobody_can_connect(self, world):
        configure_platform(world)
        world.application.credentials.remove(
            "global", "whatsapp.platform_business_id", actor="test"
        )
        browser = world.browser("alpha")
        assert browser.get(BASE).json()["state"] == "unavailable"
        assert browser.post(f"{BASE}/start").status_code == 503

    def test_a_failed_connection_gives_back_the_previous_account(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        before = service(world).state.account("alpha")
        from sales_integrations.whatsapp import GraphError

        world.graph.owner = {"id": "800000000099", "name": "Elsewhere"}
        world.graph.fail["subscribe"] = GraphError("no", code="100", status=400)
        assert connect(world, world.browser("alpha")).status_code == 502
        after = service(world).state.account("alpha")
        assert (after.business_id, after.waba_id) == (before.business_id, before.waba_id)


class TestTwoClients:
    def connect_both(self, world):
        configure_platform(world)
        assert connect(world, world.browser("alpha")).status_code == 200
        alpha_graph, world.graph = world.graph, BetaGraph()
        assert connect(world, world.browser("beta")).status_code == 200
        return alpha_graph

    def test_each_client_holds_its_own_portfolio_account_number_and_token(self, world):
        self.connect_both(world)
        state = service(world).state
        a, b = state.account("alpha"), state.account("beta")
        assert (a.business_id, a.waba_id, a.phone_number_id) == (OWNER, WABA, PHONE)
        assert (b.business_id, b.waba_id, b.phone_number_id) == (OWNER_B, WABA_B, PHONE_B)
        assert secret(world, "whatsapp.access_token", "alpha") == TOKEN
        assert secret(world, "whatsapp.access_token", "beta") == TOKEN_B
        assert secret(world, "whatsapp.waba_id", "beta") == WABA_B

    def test_each_agent_sends_with_its_own_resources_only(self, world):
        self.connect_both(world)
        for cid, token, phone in (("alpha", TOKEN, PHONE), ("beta", TOKEN_B, PHONE_B)):
            config = world.application.registry.get_config(cid)
            assert config.secrets.get_optional("whatsapp.access_token") == token
            assert config.phone_number_id == phone
            channel = service(world).channel_for(config)
            assert channel._headers["Authorization"] == f"Bearer {token}"
            assert f"/{phone}/messages" in channel._url

    def test_a_portfolio_account_or_number_of_another_client_is_refused(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        other = BetaGraph()
        world.graph = other
        # Same Portfolio as alpha: one Portfolio per client.
        other.owner = {"id": OWNER, "name": "Alpha Holding"}
        response = connect(world, world.browser("beta"))
        assert response.status_code == 409 and response.json()["detail"] == "business_in_use"
        assert secret(world, "whatsapp.access_token", "beta") is None
        assert service(world).state.account("beta").ownership == "legacy_unverified"
        # Same account as alpha, other Portfolio.
        other.owner = {"id": OWNER_B, "name": "Beta"}
        state = service(world).state
        with pytest.raises(AccountConflict) as raised:
            state.claim_account(AccountRecord("beta", OWNER_B, WABA, PHONE_B))
        assert raised.value.field == "waba"
        with pytest.raises(AccountConflict) as raised:
            state.claim_account(AccountRecord("beta", OWNER_B, WABA_B, PHONE))
        assert raised.value.field == "phone"
        assert state.account("beta").business_id is None  # still the account adopted at start

    def test_disconnecting_one_client_leaves_the_other_untouched(self, world):
        self.connect_both(world)
        world.graph = FakeGraph()
        assert world.browser("alpha").post(f"{BASE}/disconnect").status_code == 200
        state = service(world).state
        assert state.account("alpha") is None
        assert state.account("beta").phone_number_id == PHONE_B
        assert secret(world, "whatsapp.access_token", "beta") == TOKEN_B
        assert world.browser("beta").get(BASE).json()["state"] == "connected"
        # Alpha's number and account are free again.
        state.claim_account(AccountRecord("gamma", "800000000003", "333333333333", PHONE))


class TestRouting:
    def test_a_status_goes_to_the_client_of_the_number_without_reading_any_secret(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        world.graph = BetaGraph()
        connect(world, world.browser("beta"))
        sv = service(world)
        reads = []
        original = sv._secrets.get
        sv._secrets.get = lambda scope, key: (reads.append((scope, key)), original(scope, key))[1]
        assert sv.client_for_account(WABA_B, PHONE_B) == "beta"
        assert sv.client_for_account(None, PHONE) == "alpha"
        assert sv.client_for_account(WABA, None) == "alpha"
        assert reads == []

    def test_a_contradictory_or_unknown_event_belongs_to_nobody(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        sv = service(world)
        assert sv.client_for_account(WABA_B, PHONE) is None  # alpha's number, another account
        assert sv.client_for_account("424242424242", None) is None
        assert sv.client_for_account(None, "999999999999") is None

    def test_an_account_event_disconnects_only_its_own_client(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        world.graph = BetaGraph()
        connect(world, world.browser("beta"))
        service(world).handle(
            WhatsAppNotification(
                ACCOUNT, WABA_B, None, {"field": "account_update", "event": "ACCOUNT_DELETED"}
            )
        )
        assert secret(world, "whatsapp.access_token", "beta") is None
        assert service(world).state.account("beta") is None
        assert secret(world, "whatsapp.access_token", "alpha") == TOKEN
        assert service(world).state.account("alpha") is not None

    def test_a_deauthorisation_matching_two_clients_changes_nothing(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        world.graph = BetaGraph()
        connect(world, world.browser("beta"))
        # The fake gives both connections the same Meta user: the call cannot say which client.
        response = world.anonymous().post(
            "/webhooks/whatsapp/deauthorize",
            content="signed_request=" + signed_request({"user_id": "fbuser-1"}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        assert secret(world, "whatsapp.access_token", "alpha") == TOKEN
        assert secret(world, "whatsapp.access_token", "beta") == TOKEN_B


class TestBilling:
    def test_billing_is_unverifiable_until_meta_says_otherwise_and_never_confirmed(self, world):
        configure_platform(world)
        body = connect(world, world.browser("alpha")).json()
        assert body["billing"] == BILLING_UNVERIFIABLE
        now = datetime.now(UTC) + timedelta(minutes=1)
        service(world).handle(status_ok(WABA, PHONE, now))
        assert world.browser("alpha").get(BASE).json()["billing"] != BILLING_CONFIRMED

    def test_a_refused_send_for_lack_of_a_payment_method_shows_to_configure(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        service(world).handle(status_failed(WABA, PHONE, datetime.now(UTC)))
        assert world.browser("alpha").get(BASE).json()["billing"] == BILLING_TO_CONFIGURE
        assert world.admin().get(ADMIN).json()["billing"] == BILLING_TO_CONFIGURE
        # A later delivery lifts the mark back to "unverifiable", never to "confirmed".
        service(world).handle(
            status_ok(WABA, PHONE, datetime.now(UTC) + timedelta(hours=1), mid="wamid.9")
        )
        assert world.browser("alpha").get(BASE).json()["billing"] == BILLING_UNVERIFIABLE

    def test_one_clients_billing_signal_never_touches_another(self, world):
        configure_platform(world)
        connect(world, world.browser("alpha"))
        world.graph = BetaGraph()
        connect(world, world.browser("beta"))
        service(world).handle(status_failed(WABA_B, PHONE_B, datetime.now(UTC)))
        assert world.browser("beta").get(BASE).json()["billing"] == BILLING_TO_CONFIGURE
        assert world.browser("alpha").get(BASE).json()["billing"] == BILLING_UNVERIFIABLE


class TestNoManualEntry:
    @pytest.mark.parametrize(
        "key", ["whatsapp.access_token", "whatsapp.phone_number_id", "whatsapp.waba_id"]
    )
    def test_the_admin_cannot_type_a_clients_token_or_ids(self, world, key):
        admin = world.admin()
        put = admin.put(f"/admin/v1/clients/alpha/integrations/{key}", json={"value": "123456789"})
        assert put.status_code == 409
        delete = admin.delete(f"/admin/v1/clients/alpha/integrations/{key}?confirm=true")
        assert delete.status_code == 409
        assert secret(world, key) is None


class TestLegacyAdoption:
    def test_an_existing_account_is_adopted_unverified_and_secrets_stay(self, world):
        sv = service(world)
        world.store.set("client:gamma", "whatsapp.access_token", TOKEN_B, by="legacy")
        world.store.set("client:gamma", "whatsapp.waba_id", WABA_B, by="legacy")
        assert sv.adopt_legacy([("gamma", PHONE_B)]) == 1
        row = sv.state.account("gamma")
        assert row.ownership == "legacy_unverified" and row.business_id is None
        assert sv.client_for_account(WABA_B, PHONE_B) == "gamma"
        assert secret(world, "whatsapp.access_token", "gamma") == TOKEN_B  # nothing removed
        assert sv.adopt_legacy([("gamma", PHONE_B)]) == 0  # once

    def test_a_number_claimed_twice_is_adopted_for_neither_guess(self, world):
        sv = service(world)
        assert sv.adopt_legacy([("gamma", PHONE), ("naga", PHONE)]) == 1
        assert sv.client_for_account(None, PHONE) == "gamma"
        assert sv.state.account("naga") is None

    def test_reconnecting_with_embedded_signup_verifies_the_owner(self, world):
        configure_platform(world)
        sv = service(world)
        assert sv.state.account("alpha").ownership == "legacy_unverified"  # adopted at start
        assert connect(world, world.browser("alpha")).status_code == 200
        row = sv.state.account("alpha")
        assert row.ownership == "verified" and row.business_id == OWNER


class TestTheDatabaseIsTheGuard:
    @pytest.fixture
    def sql(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'wa.db'}")
        metadata.create_all(engine)
        yield SqlWhatsAppState(engine)
        engine.dispose()

    def test_simultaneous_claims_of_one_number_let_one_client_win(self, sql):
        barrier = threading.Barrier(2)
        outcomes = {}

        def claim(client):
            barrier.wait()
            try:
                sql.claim_account(
                    AccountRecord(
                        client, f"80000000000{client[-1]}", f"22222222222{client[-1]}", PHONE
                    )
                )
                outcomes[client] = "won"
            except AccountConflict as conflict:
                outcomes[client] = conflict.field

        threads = [threading.Thread(target=claim, args=(c,)) for c in ("c1", "c2")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(outcomes.values()) == ["phone", "won"]
        winner = next(c for c, o in outcomes.items() if o == "won")
        assert sql.account_for_phone(PHONE).client_id == winner

    def test_portfolio_and_account_are_unique_per_client_too(self, sql):
        sql.claim_account(AccountRecord("c1", OWNER, WABA, PHONE))
        with pytest.raises(AccountConflict) as raised:
            sql.claim_account(AccountRecord("c2", OWNER, "333333333333", "777777777777"))
        assert raised.value.field == "business"
        with pytest.raises(AccountConflict) as raised:
            sql.claim_account(AccountRecord("c2", OWNER_B, WABA, "777777777777"))
        assert raised.value.field == "waba"
        # A client may replace its own account, and a dropped one frees its ids.
        sql.claim_account(AccountRecord("c1", OWNER, WABA, "888888888888"))
        sql.drop_account("c1")
        sql.claim_account(AccountRecord("c2", OWNER, WABA, PHONE))
        assert sql.account_for_waba(WABA).client_id == "c2"

    def test_the_in_memory_register_behaves_the_same(self):
        state = InMemoryWhatsAppState()
        state.claim_account(AccountRecord("c1", OWNER, WABA, PHONE))
        with pytest.raises(AccountConflict):
            state.claim_account(AccountRecord("c2", OWNER_B, "333333333333", PHONE))
