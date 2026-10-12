"""A client whose prospects are in HubSpot, from its configuration to a running Engine.

HubSpot is a mock transport (nothing reaches the network); the operational store is the real SQL
store over a real database; the consent registry is the real one. What is proved: the runtime uses
exactly the source the client chose and never another; every missing piece is an explicit error and
never a quiet fallback; two clients never see each other's contacts, state or refusals; and nothing
is ever written to HubSpot.
"""

from dataclasses import replace

import httpx
import pytest
from app_support import client_yaml, env_for, fake_builders, write_client
from baseline_support import resolve
from consent_support import make_world
from factories import NOW
from sql_store_support import SqlStoreUnderTest

from sales_app.config import import_document
from sales_app.config_store import InMemoryConfigStore
from sales_app.errors import RuntimeBuildError
from sales_app.secret_store import InMemorySecretStore, client_scope
from sales_app.sourcing import SourceAuthError, SourceUnavailableError
from sales_app.sourcing.composite import CompositeRepository
from sales_app.wiring import build_runtime
from sales_app.wiring.providers import hubspot
from sales_core.approval import ApprovalMode
from sales_core.contracts import AuditEvent, ContactInfo, ProspectStatus
from sales_integrations.fakes import FakeClock
from sales_integrations.hubspot import HubSpotClient

TOKEN_A = "pat-eu1-aaaaaaaa-1111-1111-1111-111111111111"
TOKEN_B = "pat-eu1-bbbbbbbb-2222-2222-2222-222222222222"
HUBSPOT_SETTINGS = {
    "status_mapping": {"NEW": "new", "BAD_TIMING": "paused", "UNQUALIFIED": "closed"},
}
LEAK = ("Ada Lovelace", "leak.me@example.com", "21699888777", TOKEN_A, TOKEN_B)


def person(identifier, status="NEW", *, first="Ada", last="Lovelace", **properties):
    return {
        "id": identifier,
        "properties": {
            "firstname": first,
            "lastname": last,
            "email": None,
            "mobilephone": "+216 99 888 777",
            "hs_lead_status": status,
            **properties,
        },
    }


class HubSpotAccounts:
    """Several HubSpot accounts behind one mock transport: the token says which account is asked."""

    def __init__(self, **accounts: list[dict]) -> None:
        self.accounts = accounts
        self.requests: list[tuple[str, httpx.Request]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        self.requests.append((token, request))
        contacts = self.accounts.get(token)
        if contacts is None:
            return httpx.Response(401, json={"message": "leak.me@example.com is not allowed"})
        path = request.url.path
        if path.endswith("/search"):
            return httpx.Response(200, json={"results": []})
        if path.rsplit("/", 1)[-1].isdigit():
            wanted = path.rsplit("/", 1)[-1]
            found = next((c for c in contacts if c["id"] == wanted), None)
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={})
        return httpx.Response(200, json={"results": contacts})

    @property
    def methods(self) -> set[str]:
        return {request.method for _, request in self.requests}

    def tokens(self) -> set[str]:
        return {token for token, _ in self.requests}


@pytest.fixture
def accounts(monkeypatch):
    hub = HubSpotAccounts(**{TOKEN_A: [person("1")], TOKEN_B: [person("1", first="Bea")]})
    transport = httpx.MockTransport(hub)
    monkeypatch.setattr(
        hubspot,
        "HubSpotClient",
        lambda token: HubSpotClient(token, http=lambda: httpx.Client(transport=transport)),
    )
    return hub


def publish(tmp_path, client_id, source, secrets, **env):
    write_client(tmp_path, client_id)
    store = InMemoryConfigStore()
    document = import_document(client_id, client_yaml(client_id)).document
    store.publish(
        client_id,
        {**document, **({"source": source} if source is not None else {})},
        author="test",
        expected_parent=None,
    )
    resolved = resolve(
        tmp_path,
        client_id,
        ceiling=ApprovalMode.APPROVAL,
        env=env or env_for(client_id),
        store=store,
        secret_store=secrets,
    )
    assert resolved.config is not None, resolved.errors
    return resolved.config


def secrets_with(**tokens):
    secrets = InMemorySecretStore()
    for client_id, token in tokens.items():
        secrets.set(client_scope(client_id), "hubspot.access_token", token, by="test")
    return secrets


def hubspot_source(settings=HUBSPOT_SETTINGS):
    return {"provider": "hubspot", **({"settings": settings} if settings is not None else {})}


class Deployment:
    """What the builders of a deployment hold: one database, one registry per client."""

    def __init__(self, engine, *client_ids: str) -> None:
        self.engine = engine
        self.stores = {c: SqlStoreUnderTest(engine, c) for c in client_ids}
        self.worlds = {c: make_world(client_id=c) for c in client_ids}
        self.builders, self.made = fake_builders(FakeClock(NOW))

    def builders_with(self, **overrides):
        base = replace(
            self.builders,
            sources={"hubspot": hubspot.build_source},
            operational_stores=lambda client_id: self.stores[client_id],
            consent=lambda client_id: self.worlds[client_id].registry,
        )
        return replace(base, **overrides)

    def build(self, config, **overrides):
        return build_runtime(config, self.builders_with(**overrides), FakeClock(NOW))


@pytest.fixture
def deployment(sql_engine):
    return Deployment(sql_engine, "alpha", "beta")


# ---- what the client's configuration asks for ---------------------------------------------------


class TestTheCredentialsAClientOnHubSpotNeeds:
    def test_it_needs_its_own_token_and_no_google_access(self, tmp_path):
        config = publish(
            tmp_path,
            "alpha",
            hubspot_source(),
            None,
            OPENAI_API_KEY="sk",
            ALPHA_TOKEN="t",
            ALPHA_PHONE_ID="p",
            ALPHA_APP_SECRET="s",
        )
        assert config.missing_credentials == ("HubSpot access token is not configured",)
        assert not config.ready

    def test_with_its_token_it_is_ready_and_the_token_stays_out_of_every_repr(self, tmp_path):
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        assert config.ready and config.secrets.get("hubspot.access_token") == TOKEN_A
        assert TOKEN_A not in repr(config.secrets) and TOKEN_A not in repr(
            config.missing_credentials
        )

    def test_the_token_of_a_client_on_sheets_is_not_resolved_at_all(self, tmp_path):
        config = publish(tmp_path, "alpha", None, secrets_with(alpha=TOKEN_A))
        assert config.secrets.get_optional("hubspot.access_token") is None

    def test_a_client_never_gets_another_clients_token(self, tmp_path):
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(beta=TOKEN_B))
        assert config.secrets.get_optional("hubspot.access_token") is None
        assert not config.ready

    def test_a_secret_cannot_be_written_among_its_settings(self, tmp_path):
        with pytest.raises(AssertionError, match="looks like a secret"):
            publish(
                tmp_path,
                "alpha",
                hubspot_source({**HUBSPOT_SETTINGS, "token": TOKEN_A}),
                secrets_with(alpha=TOKEN_A),
            )

    def test_its_settings_are_kept_in_the_document_and_the_spec(self, tmp_path):
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        assert config.spec.source_settings == HUBSPOT_SETTINGS
        assert config.spec.source_apart_from_store


# ---- the runtime builds the source the client chose, or says why it cannot --------------------


class TestTheRuntimeUsesTheChosenSource:
    def test_a_hubspot_client_runs_on_the_composite_over_its_own_store(self, tmp_path, deployment):
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        runtime = deployment.build(config)
        repository = runtime.engine._repo
        assert isinstance(repository, CompositeRepository)
        assert repository.client_id == "alpha"
        assert deployment.made.repositories == {}  # nothing of the Sheets path was built

    def test_a_sheets_client_is_built_exactly_as_before(self, tmp_path, deployment):
        config = publish(tmp_path, "alpha", None, None)
        runtime = deployment.build(config)
        assert runtime.engine._repo is deployment.made.repositories["alpha"]

    def test_a_sheets_client_needs_neither_a_store_nor_a_registry(self, tmp_path, deployment):
        config = publish(tmp_path, "alpha", None, None)
        runtime = deployment.build(config, operational_stores=None, consent=None)
        assert runtime.engine._repo is deployment.made.repositories["alpha"]

    def test_the_prospects_are_hubspots_under_the_hubspot_namespace(
        self, tmp_path, deployment, accounts
    ):
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        repository = deployment.build(config).engine._repo
        (prospect,) = repository.list_prospects()
        assert prospect.prospect_id == "hubspot:1" and prospect.status is ProspectStatus.NEW
        assert repository.get_prospect("hubspot:1").display_name == "Ada Lovelace"
        assert accounts.tokens() == {TOKEN_A}


class TestNeverAQuietFallback:
    """Each of these is an explicit error naming the client and the stage, and in none of them is
    the client built on the Sheets path instead."""

    def refused(self, tmp_path, deployment, source, secrets, **overrides):
        config = publish(tmp_path, "alpha", source, secrets)
        with pytest.raises(RuntimeBuildError) as caught:
            deployment.build(config, **overrides)
        assert deployment.made.repositories == {}
        message = str(caught.value)
        assert message.startswith("alpha: ")
        assert not any(word in message for word in LEAK)
        return message

    def test_a_source_the_runtime_cannot_build(self, tmp_path, deployment):
        message = self.refused(
            tmp_path, deployment, hubspot_source(), secrets_with(alpha=TOKEN_A), sources={}
        )
        assert "cannot be run yet" in message and "hubspot" in message

    def test_no_operational_store_in_the_deployment(self, tmp_path, deployment):
        message = self.refused(
            tmp_path, deployment, hubspot_source(), secrets_with(alpha=TOKEN_A),
            operational_stores=None,
        )  # fmt: skip
        assert "operational store" in message and "SALES_AGENT_DATABASE_URL" in message

    def test_no_consent_registry_in_the_deployment(self, tmp_path, deployment):
        message = self.refused(
            tmp_path, deployment, hubspot_source(), secrets_with(alpha=TOKEN_A), consent=None
        )
        assert "consent registry" in message

    def test_a_consent_registry_that_is_not_set_up(self, tmp_path, deployment):
        deployment.worlds["alpha"] = make_world(client_id="alpha", initialise=False)
        message = self.refused(tmp_path, deployment, hubspot_source(), secrets_with(alpha=TOKEN_A))
        assert "consent registry: not ready (not_initialised)" in message

    def test_a_store_that_cannot_be_used(self, tmp_path, deployment):
        deployment.stores["alpha"].verify = lambda: ["a table is missing"]
        message = self.refused(tmp_path, deployment, hubspot_source(), secrets_with(alpha=TOKEN_A))
        assert "operational store: a table is missing" in message

    def test_a_store_that_fails_to_open(self, tmp_path, deployment):
        def broken(client_id):
            raise RuntimeError("database says password=hunter2")

        message = self.refused(
            tmp_path, deployment, hubspot_source(), secrets_with(alpha=TOKEN_A),
            operational_stores=broken,
        )  # fmt: skip
        assert "operational store" in message  # the stage is named; the cause is the caller's log

    def test_no_settings_means_no_mapping_and_no_guess(self, tmp_path, deployment):
        message = self.refused(
            tmp_path, deployment, hubspot_source(None), secrets_with(alpha=TOKEN_A)
        )
        assert "source: source.settings: status_mapping" in message

    @pytest.mark.parametrize(
        "settings",
        [
            {"status_mapping": {}},
            {"status_mapping": {"NEW": "active"}},
            {"status_mapping": {"X": "closed"}},  # nothing maps to new
            {"status_mapping": {"NEW": "new"}, "unknown_key": "x"},
            {"status_mapping": {"NEW": "new"}, "status_property": "Bad Name"},
        ],
    )
    def test_a_settings_block_that_is_not_acceptable(self, tmp_path, deployment, settings):
        self.refused(tmp_path, deployment, hubspot_source(settings), secrets_with(alpha=TOKEN_A))

    def test_a_missing_token_never_builds(self, tmp_path, deployment):
        config = publish(tmp_path, "alpha", hubspot_source(), None)
        assert not config.ready  # the registry would not even try
        with pytest.raises(RuntimeBuildError, match="access token is not configured"):
            deployment.build(config)
        assert deployment.made.repositories == {}

    def test_a_source_that_is_not_reachable_fails_each_read_and_nothing_replaces_it(
        self, tmp_path, deployment, accounts
    ):
        accounts.accounts.pop(TOKEN_A)  # HubSpot now refuses the token
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        repository = deployment.build(config).engine._repo
        with pytest.raises(SourceAuthError) as caught:
            repository.list_prospects()
        assert not any(word in str(caught.value) for word in LEAK)
        with pytest.raises(SourceAuthError):
            repository.get_prospect("hubspot:1")
        assert deployment.made.repositories == {}

    def test_a_source_that_is_down_is_an_error_not_an_empty_list(
        self, tmp_path, deployment, monkeypatch
    ):
        def down(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(down)
        monkeypatch.setattr(
            hubspot,
            "HubSpotClient",
            lambda token: HubSpotClient(token, http=lambda: httpx.Client(transport=transport)),
        )
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        with pytest.raises(SourceUnavailableError):
            deployment.build(config).engine._repo.list_prospects()


# ---- two clients are two worlds ----------------------------------------------------------------


class TestClientsAreIsolated:
    @pytest.fixture
    def pair(self, tmp_path, deployment, accounts):
        secrets = secrets_with(alpha=TOKEN_A, beta=TOKEN_B)
        alpha = deployment.build(publish(tmp_path, "alpha", hubspot_source(), secrets))
        beta = deployment.build(publish(tmp_path, "beta", hubspot_source(), secrets))
        return alpha.engine._repo, beta.engine._repo

    def test_each_reads_only_its_own_account_with_its_own_token(self, pair, accounts):
        alpha, beta = pair
        assert alpha.list_prospects()[0].display_name == "Ada Lovelace"
        assert beta.list_prospects()[0].display_name == "Bea Lovelace"
        by_token = {t: set() for t in (TOKEN_A, TOKEN_B)}
        for token, request in accounts.requests:
            by_token[token].add(request.url.path)
        assert accounts.tokens() == {TOKEN_A, TOKEN_B}

    def test_the_same_native_id_in_two_accounts_is_two_prospects_with_two_states(self, pair):
        alpha, beta = pair
        (a,), (b,) = alpha.list_prospects(), beta.list_prospects()
        assert a.prospect_id == b.prospect_id == "hubspot:1"
        alpha.update_prospects([replace(a, status=ProspectStatus.PAUSED, stage="seen")])
        assert alpha.get_prospect("hubspot:1").stage == "seen"
        assert beta.get_prospect("hubspot:1").stage is None
        assert beta.get_prospect("hubspot:1").status is ProspectStatus.NEW

    def test_one_clients_refusal_does_not_block_the_others_contact(self, pair, deployment):
        alpha, beta = pair
        phone = ContactInfo(phone="+216 99 888 777")
        deployment.worlds["alpha"].block(phone)
        assert alpha.list_prospects()[0].status is ProspectStatus.OPTED_OUT
        assert beta.list_prospects()[0].status is ProspectStatus.NEW

    def test_a_store_refuses_the_other_clients_objects(self, pair):
        alpha, beta = pair
        (b,) = beta.list_prospects()
        with pytest.raises(ValueError):
            alpha.update_prospects([b])

    def test_the_stores_hold_each_clients_rows_apart(self, pair, deployment):
        alpha, _ = pair
        (a,) = alpha.list_prospects()
        alpha.update_prospects([replace(a, stage="only-alpha")])
        assert set(deployment.stores["alpha"].list_states()) == {"hubspot:1"}
        assert deployment.stores["beta"].list_states() == {}


# ---- consent, write-back and idempotency are what they were ------------------------------------


class TestNothingIsEverWrittenToHubSpot:
    @pytest.fixture
    def repository(self, tmp_path, deployment, accounts):
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        return deployment.build(config).engine._repo

    def test_what_revansa_does_goes_to_its_own_store_and_hubspot_is_only_read(
        self, repository, deployment, accounts
    ):
        (prospect,) = repository.list_prospects()
        repository.update_prospects(
            [
                replace(
                    prospect, status=ProspectStatus.ACTIVE, stage="talking", conversation_id="c-1"
                )
            ]
        )
        local = replace(
            prospect,
            prospect_id="inbound-2026-01-15-21611111111",
            contact=ContactInfo(phone="+21611111111"),
        )
        repository.create_prospects([local])
        assert repository.get_prospect("hubspot:1").status is ProspectStatus.ACTIVE
        assert deployment.stores["alpha"].list_states()["hubspot:1"].stage == "talking"
        assert accounts.methods <= {"GET", "POST"}
        posts = [r for _, r in accounts.requests if r.method == "POST"]
        assert all(r.url.path.endswith("/search") for r in posts)

    def test_a_field_the_crm_owns_is_refused_not_written_elsewhere(self, repository, accounts):
        (prospect,) = repository.list_prospects()
        with pytest.raises(Exception, match="owned by the source"):
            repository.update_prospects([replace(prospect, display_name="Somebody Else")])
        assert accounts.methods == {"GET"}


class TestConsentIsKept:
    @pytest.fixture
    def repository(self, tmp_path, deployment, accounts):
        accounts.accounts[TOKEN_A] = [
            person("1"),
            person(
                "2",
                "UNKNOWN_VALUE",
                first="Out",
                mobilephone="+21611111111",
                hs_email_optout="true",
            ),
            person("3", first="Dnc", mobilephone="+21622222222", hs_email_optout="true"),
            person("4", first="Odd", mobilephone="+21633333333", hs_email_optout="maybe"),
        ]
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        return deployment.build(config).engine._repo

    def test_do_not_contact_makes_a_kept_record_opted_out_and_anything_odd_counts(self, repository):
        found = {p.prospect_id: p.status for p in repository.list_prospects()}
        assert found == {
            "hubspot:1": ProspectStatus.NEW,
            "hubspot:3": ProspectStatus.OPTED_OUT,
            "hubspot:4": ProspectStatus.OPTED_OUT,
        }

    def test_the_refusal_of_a_left_out_record_is_recorded_in_the_registry(
        self, repository, deployment
    ):
        repository.list_prospects()  # record 2 is left out (unmapped status) but asked not to be contacted
        assert (
            deployment.worlds["alpha"].registry.snapshot().blocks(ContactInfo(phone="+21611111111"))
        )
        assert (
            not deployment.worlds["alpha"]
            .registry.snapshot()
            .blocks(ContactInfo(phone="+21699888777"))
        )
        assert any(p.native_id == "2" for p in repository.problems())

    def test_an_opt_out_keyword_is_recorded_and_follows_the_person(self, repository, deployment):
        event = AuditEvent(
            timestamp=NOW,
            client_id="alpha",
            event_type="prospect.opted_out",
            actor="system:inbound",
            prospect_id="hubspot:1",
        )
        repository.append_audit_events([event])
        assert (
            deployment.worlds["alpha"].registry.snapshot().blocks(ContactInfo(phone="+21699888777"))
        )
        assert repository.get_prospect("hubspot:1").status is ProspectStatus.OPTED_OUT

    def test_a_registry_that_breaks_blocks_everyone(self, repository, deployment):
        deployment.worlds["alpha"].store.set(
            "consent:alpha", "hmac_key", "not-a-key", by="attacker"
        )
        assert {p.status for p in repository.list_prospects()} == {ProspectStatus.OPTED_OUT}


# ---- one client's missing consent registry never stops the others -------------------------------


class TestStartupIsolation:
    """At startup a HubSpot client whose consent registry is absent or invalid is kept apart,
    not ready and with the reason; every other client starts and works as usual."""

    @pytest.fixture
    def loaded(self, tmp_path):
        from sales_app.config import LoadedConfig

        secrets = secrets_with(alpha=TOKEN_A)
        alpha = publish(tmp_path, "alpha", hubspot_source(), secrets)
        beta = publish(tmp_path, "beta", None, secrets)
        return LoadedConfig((alpha, beta), (), ())

    def start(self, loaded, deployment):
        from sales_app.registry import ClientRegistry

        return ClientRegistry.build(loaded, deployment.builders_with(), FakeClock(NOW))

    def assert_isolated(self, registry, reason):
        assert [r.client_id for r in registry.runtimes()] == ["beta"]
        assert registry.get("alpha") is None  # no runtime, so no agent, no engine, no repository
        assert registry.missing("alpha") and reason in registry.missing("alpha")[0]
        health = {e["client_id"]: e for e in registry.health()}
        assert health["alpha"]["status"] == "not_ready"
        assert reason in health["alpha"]["last_error"]
        assert health["beta"]["status"] == "ready"
        assert not any(word in str(health) for word in LEAK)

    def test_a_registry_that_was_never_set_up(self, loaded, deployment):
        deployment.worlds["alpha"] = make_world(client_id="alpha", initialise=False)
        self.assert_isolated(self.start(loaded, deployment), "not ready (not_initialised)")

    def test_a_registry_that_is_corrupt(self, loaded, deployment):
        deployment.worlds["alpha"].store.set("consent:alpha", "hmac_key", "not-a-key", by="x")
        self.assert_isolated(self.start(loaded, deployment), "not ready (key_invalid)")

    def test_a_registry_that_cannot_be_opened(self, loaded, deployment):
        def broken(client_id):
            raise OSError("cannot open /secret/path/leak.me@example.com")

        deployment.builders_with = lambda **o: replace(
            Deployment.builders_with(deployment), consent=broken
        )
        self.assert_isolated(self.start(loaded, deployment), "OSError")

    def test_a_deployment_with_no_registry_at_all(self, loaded, deployment):
        deployment.builders_with = lambda **o: replace(
            Deployment.builders_with(deployment), consent=None
        )
        self.assert_isolated(self.start(loaded, deployment), "consent registry")

    def test_the_other_client_really_works(self, loaded, deployment):
        registry = self.start(loaded, deployment)
        with registry.get("beta").lease() as engine:
            assert engine is registry.get("beta").engine

    def test_the_client_never_runs_without_a_registry_whatever_asks(self, loaded, deployment):
        registry = self.start(loaded, deployment.__class__(deployment.engine, "alpha", "beta"))
        # a fresh deployment's registries are set up: alpha is built; now break it and restart
        deployment.worlds["alpha"] = make_world(client_id="alpha", initialise=False)
        registry = self.start(loaded, deployment)
        assert registry.get("alpha") is None
        assert deployment.made.repositories.get("alpha") is None
        assert registry.get_config("alpha").missing_credentials  # still known, still refused

    def test_once_the_registry_is_set_up_the_client_builds(self, tmp_path, deployment):
        deployment.worlds["alpha"] = make_world(client_id="alpha", initialise=False)
        config = publish(tmp_path, "alpha", hubspot_source(), secrets_with(alpha=TOKEN_A))
        with pytest.raises(RuntimeBuildError):
            deployment.build(config)
        deployment.worlds["alpha"].registry.initialise(actor="cli:test")
        assert deployment.build(config).client_id == "alpha"

    def test_other_build_errors_still_stop_the_start_loudly(self, loaded, deployment):
        from sales_app.errors import ConfigurationError

        deployment.stores["alpha"].verify = lambda: ["a table is missing"]
        with pytest.raises(ConfigurationError, match="a table is missing"):
            self.start(loaded, deployment)
