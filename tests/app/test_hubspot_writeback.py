"""Writing Revansa's results back into a client's HubSpot account, against a simulated HubSpot only.

What is proved: nothing is written unless the operator named every property and the deployment
turned write-back on; a status is only ever tightened, in the CRM's own word (the inverse of the
validated mapping, refused if ambiguous); a value a person put there is never replaced; a restart
never writes twice; 401/403/429/5xx are told apart and leak nothing; clients and sources are kept
apart. What is NOT proved here, and cannot be without a real account, is in
`docs/writeback-hubspot.md`.
"""

import itertools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from consent_support import Clock, make_world
from hubspot_writeback_support import (
    PROPERTIES,
    TOKEN_A,
    TOKEN_B,
    FakeHubSpot,
    settings,
    writeback_block,
)
from sourcing_fakes import FakeProspectSource, default_capabilities

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.sourcing import (
    ProspectField,
    ProspectState,
    SourceConflictError,
    SourceReadOnlyError,
)
from sales_app.sourcing.fields import SOURCE_ONLY_FIELDS
from sales_app.sourcing.ports import ProspectChange
from sales_app.sourcing.statuses import StatusMappingError, StatusWords
from sales_app.sourcing.writeback import (
    FailureCode,
    InMemoryWriteBackLedger,
    Outcome,
    WriteBackPolicy,
    WriteBackSyncer,
)
from sales_app.wiring.providers import hubspot
from sales_app.wiring.writeback import WritebackSupport, default_policies, sql_ledgers
from sales_core.approval import ApprovalMode
from sales_core.contracts import ClientId, ProspectId, ProspectStatus, Scheduling
from sales_integrations.hubspot import HubSpotClient

S = ProspectStatus
F = ProspectField
A, B = "client-a", "client-b"
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
EARLIER = datetime(2026, 2, 20, 8, 30, tzinfo=UTC)


class Ledger(InMemoryWriteBackLedger):
    def verify(self):
        return []


class Secrets:
    def __init__(self, token):
        self._token = token

    def __contains__(self, key):
        return True

    def get(self, key):
        return self._token


ACCOUNTS: dict[str, FakeHubSpot] = {}


@pytest.fixture
def hubs(monkeypatch):
    """The simulated accounts, by token; the provider's client is built over them."""
    accounts: dict[str, FakeHubSpot] = {}

    def make(token, **kwargs):
        return HubSpotClient(token, http=ACCOUNTS[token].client_factory(), **kwargs)

    ACCOUNTS.clear()
    ACCOUNTS.update(accounts)
    monkeypatch.setattr(hubspot, "HubSpotClient", make)
    return ACCOUNTS


def build(hub, client=A, ns="hs_a", raw=None):
    ACCOUNTS.setdefault(hub.token, hub)
    config = SimpleNamespace(client_id=client, secrets=Secrets(hub.token))
    instance = SimpleNamespace(id=ns, settings=raw if raw is not None else settings())
    return hubspot.build_source_instance(config, instance)


class Env:
    """One client, one HubSpot source, the real syncer; time only moves when told to."""

    def __init__(self, hubs, hub=None, *, client=A, ns="hs_a", raw=None, ledger=None, **options):
        self.hub = hub or FakeHubSpot()
        hubs[self.hub.token] = self.hub
        self.client, self.ns = client, ns
        self.source = build(self.hub, client, ns, raw)
        self.store = FakeStore(client)
        self.world = make_world(client_id=client)
        self.ledger = ledger or Ledger(client)
        self.clock = Clock(T0)
        counter = itertools.count(1)
        fields, opt_out = hubspot.writeback_of(raw if raw is not None else settings())
        self.syncer = WriteBackSyncer(
            self.source,
            self.store,
            self.world.registry,
            self.ledger,
            WriteBackPolicy(fields=fields, propagate_opt_out=opt_out),
            clock=self.clock,
            new_run_id=lambda: f"run{next(counter)}",
            **options,
        )

    def revansa(self, native, status=None, **schedule):
        public = ProspectId(f"{self.ns}:{native}")
        before = self.store.list_states().get(public)
        self.store.save_states(
            [
                ProspectState(
                    client_id=ClientId(self.client),
                    prospect_id=public,
                    status=status,
                    schedule=Scheduling(**schedule),
                    revision=before.revision if before is not None else 0,
                )
            ]
        )

    def outcomes(self):
        return [(e.field, e.outcome, e.code) for e in self.ledger.entries()]


def FakeStore(client):
    from sourcing_fakes import FakeOperationalStore

    return FakeOperationalStore(ClientId(client))


# ---- the client: one bounded write ---------------------------------------------------------------


def raw_client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    return HubSpotClient(TOKEN_A, http=lambda: httpx.Client(transport=transport), **kwargs)


def test_an_update_is_one_patch_of_the_named_properties_with_the_token_only_in_the_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "7", "properties": {}})

    client = raw_client(handler, writable_properties=["hs_lead_status"])
    client.update_contact("7", {"hs_lead_status": "UNQUALIFIED"})
    (request,) = seen
    assert request.method == "PATCH" and request.url.path == "/crm/v3/objects/contacts/7"
    assert request.headers["Authorization"] == f"Bearer {TOKEN_A}"
    assert request.content == b'{"properties":{"hs_lead_status":"UNQUALIFIED"}}'
    assert TOKEN_A not in str(request.url) and TOKEN_A not in repr(client)


def test_a_property_that_was_not_declared_is_refused_before_any_request():
    seen = []
    client = raw_client(
        lambda r: seen.append(r) or httpx.Response(200, json={}),
        writable_properties=["revansa_followups"],
    )
    with pytest.raises(ValueError):
        client.update_contact("7", {"hs_lead_status": "UNQUALIFIED"})
    with pytest.raises(ValueError):
        client.update_contact("7", {"revansa_followups": "1", "lifecyclestage": "customer"})
    assert seen == []


@pytest.mark.parametrize("name", ["email", "firstname", "mobilephone", "hs_email_optout"])
def test_what_says_who_a_person_is_can_never_be_declared_writable(name):
    with pytest.raises(ValueError):
        HubSpotClient(TOKEN_A, writable_properties=[name])


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, "HubSpotAuthError"),
        (403, "HubSpotAuthError"),
        (404, "HubSpotNotFoundError"),
        (429, "HubSpotRateLimitedError"),
        (500, "HubSpotUnavailableError"),
        (503, "HubSpotUnavailableError"),
        (400, "HubSpotDataError"),
    ],
)
def test_a_failing_write_is_told_apart_and_says_nothing_hubspot_said(status, error):
    client = raw_client(
        lambda r: httpx.Response(status, json={"message": "leak@example.com", "token": TOKEN_A}),
        writable_properties=["revansa_followups"],
    )
    with pytest.raises(Exception) as caught:
        client.update_contact("7", {"revansa_followups": "2"})
    assert type(caught.value).__name__ == error
    assert "leak@example.com" not in str(caught.value) and TOKEN_A not in str(caught.value)


def test_a_rate_limited_write_says_how_long_to_wait():
    client = raw_client(
        lambda r: httpx.Response(429, headers={"Retry-After": "30"}),
        writable_properties=["revansa_followups"],
    )
    with pytest.raises(Exception) as caught:
        client.update_contact("7", {"revansa_followups": "2"})
    assert caught.value.retry_after == 30.0


def test_a_lost_connection_is_unavailable_and_repeats_nothing():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {TOKEN_A}")

    client = raw_client(handler, writable_properties=["revansa_followups"])
    with pytest.raises(Exception) as caught:
        client.update_contact("7", {"revansa_followups": "2"})
    assert type(caught.value).__name__ == "HubSpotUnavailableError"
    assert TOKEN_A not in str(caught.value)


def test_a_property_definition_is_read_with_its_type_its_options_and_whether_it_can_be_set():
    body = {
        "name": "hs_lead_status",
        "type": "enumeration",
        "options": [{"value": "NEW"}, {"value": "OLD", "hidden": True}],
        "modificationMetadata": {"readOnlyValue": False},
    }
    client = raw_client(lambda r: httpx.Response(200, json=body))
    found = client.get_property("hs_lead_status")
    assert (found.type, found.options, found.read_only) == ("enumeration", ("NEW",), False)
    calculated = raw_client(
        lambda r: httpx.Response(200, json={"type": "number", "calculated": True})
    )
    assert calculated.get_property("x_y").read_only is True
    assert raw_client(lambda r: httpx.Response(404, json={})).get_property("x_y") is None


# ---- the operator's settings -----------------------------------------------------------------------


def test_without_a_writeback_block_nothing_is_writable_and_nothing_is_ever_sent(hubs):
    hub = FakeHubSpot()
    hubs[hub.token] = hub
    hub.add("1")
    source = build(hub, raw={"status_mapping": settings()["status_mapping"]})
    assert source.capabilities.writable == frozenset()
    assert hubspot.writeback_of({"status_mapping": settings()["status_mapping"]}) == (
        frozenset(),
        False,
    )
    change = ProspectChange(prospect_id="1", new={F.STATUS: S.CLOSED}, expected={F.STATUS: S.NEW})
    with pytest.raises(SourceReadOnlyError):
        source.update_prospects([change])
    assert source.read_writeback_values() == {}
    assert hub.requests == []


def test_a_block_declares_exactly_what_may_be_written(hubs):
    hub = FakeHubSpot()
    source = build(hub)
    caps = source.capabilities
    assert caps.writable == {F.STATUS, *(f for f in F if f in _MIRRORED)}
    assert caps.mirrored == caps.writable - {F.STATUS}
    assert not caps.writable & SOURCE_ONLY_FIELDS
    only = build(
        hub, raw=settings(writeback={"properties": {"follow_up_count": "revansa_followups"}})
    )
    assert only.capabilities.writable == {F.FOLLOW_UP_COUNT}


_MIRRORED = {F.LAST_CONTACTED_AT, F.LAST_INBOUND_AT, F.FOLLOW_UP_COUNT, F.NEXT_ACTION}


@pytest.mark.parametrize(
    "block",
    [
        {},  # writes nothing
        {"status": False, "properties": {}},
        {"properties": {"next_action_type": "revansa_next_action"}},  # half a next action
        {"status": False, "propagate_opt_out": True, "properties": {"follow_up_count": "x_y"}},
        {"properties": {"follow_up_count": "x_y", "last_inbound_at": "x_y"}},  # one property twice
        {"properties": {"follow_up_count": "email"}},  # an identity property
        {"properties": {"follow_up_count": "hs_lead_status"}},  # the status property
        {"properties": {"follow_up_count": "hs_email_optout"}},
        {"properties": {"follow_up_count": "Not A Name"}},
        {"unknown": True},
    ],
)
def test_a_writeback_block_that_does_not_make_sense_is_refused(block):
    with pytest.raises(ValueError):
        hubspot.writeback_of(settings(writeback=block))


def test_a_status_that_cannot_be_inverted_without_guessing_is_refused():
    mapping = dict(settings()["status_mapping"])
    two_closed = {**mapping, "LOST": "closed"}
    with pytest.raises(ValueError, match="'closed'.*has 2"):
        hubspot.writeback_of(settings(status_mapping=two_closed))
    only_new = {"NEW": "new", "UNQUALIFIED": "closed"}  # nothing says "paused"
    with pytest.raises(ValueError, match="'paused'.*has 0"):
        hubspot.writeback_of(settings(status_mapping=only_new))
    no_optout = {k: v for k, v in mapping.items() if v != "opted_out"}
    with pytest.raises(ValueError, match="'opted_out'"):
        hubspot.writeback_of(
            settings(status_mapping=no_optout, writeback=writeback_block(propagate_opt_out=True))
        )
    # the same word written two ways is still two values
    with pytest.raises(StatusMappingError):
        StatusWords.from_config(
            {"NEW": "new", "Closed": "closed", "closed": "closed", "p": "paused"}
        )


def test_properties_only_without_the_status_need_no_inverse():
    ambiguous = {"NEW": "new", "A": "closed", "B": "closed"}
    fields, opt_out = hubspot.writeback_of(
        settings(status_mapping=ambiguous, writeback={"properties": PROPERTIES})
    )
    assert F.STATUS not in fields and not opt_out


def test_the_inverse_is_the_one_word_of_each_status():
    words = StatusWords.from_config(settings()["status_mapping"], propagate_opt_out=True)
    assert words.word(S.PAUSED) == "BAD_TIMING" and words.word(S.CLOSED) == "UNQUALIFIED"
    assert words.word(S.OPTED_OUT) == "DO_NOT_CONTACT" and words.word(S.NEW) is None


def test_the_legacy_single_source_never_writes_whatever_its_settings_say(hubs):
    hub = FakeHubSpot()
    hubs[hub.token] = hub
    config = SimpleNamespace(
        client_id=A, secrets=Secrets(hub.token), spec=SimpleNamespace(source_settings=settings())
    )
    source = hubspot.build_source(config)
    assert source.capabilities.writable == frozenset()


# ---- writing -----------------------------------------------------------------------------------------


def test_a_restriction_revansa_holds_is_written_in_the_crms_own_word_and_recorded(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.hub.add("2")
    env.revansa("1", S.PAUSED)
    env.revansa("2", S.CLOSED)
    report = env.syncer.run_once()
    assert report.applied == 2 and report.stopped is None
    assert env.hub.value("1", "hs_lead_status") == "BAD_TIMING"
    assert env.hub.value("2", "hs_lead_status") == "UNQUALIFIED"
    assert [e.outcome for e in env.ledger.entries()] == [Outcome.APPLIED] * 2


def test_every_field_lands_in_its_own_property_and_nothing_else_changes(hubs):
    env = Env(hubs)
    env.hub.add("1")
    before = dict(env.hub.contacts["1"])
    env.revansa(
        "1",
        S.PAUSED,
        last_contacted_at=T0,
        last_inbound_at=T0 - timedelta(hours=3),
        follow_up_count=3,
        next_action_type="follow_up",
        next_action_due_at=T0 + timedelta(days=2),
    )
    env.syncer.run_once()
    held = env.hub.contacts["1"]
    assert held["hs_lead_status"] == "BAD_TIMING"
    assert held["revansa_last_contact"] == "2026-03-01T09:00:00Z"
    assert held["revansa_last_reply"] == "2026-03-01T06:00:00Z"
    assert held["revansa_followups"] == "3"
    assert held["revansa_next_action"] == "follow_up"
    assert held["revansa_next_due"] == "2026-03-03T09:00:00Z"
    changed = {k for k in held if held[k] != before.get(k)}
    assert changed == {"hs_lead_status", *PROPERTIES.values()}  # a person's data is untouched
    for request in env.hub.patches:
        assert set(__import__("json").loads(request.content)["properties"]) <= changed


def test_a_second_pass_writes_nothing(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", S.CLOSED, last_contacted_at=T0, follow_up_count=2)
    env.syncer.run_once()
    sent = len(env.hub.patches)
    env.clock.advance(hours=3)
    again = env.syncer.run_once()
    assert again.planned == 0 and again.applied == 0
    assert len(env.hub.patches) == sent


def test_a_contactable_status_is_never_written(hubs):
    env = Env(hubs)
    env.hub.add("1", "BAD_TIMING")  # a person paused it in HubSpot
    env.revansa("1", S.ACTIVE)  # Revansa is in touch: HubSpot is not "loosened" to new
    report = env.syncer.run_once()
    assert report.planned == 0 and env.hub.patches == []
    assert env.hub.value("1", "hs_lead_status") == "BAD_TIMING"


def test_a_status_is_never_loosened_and_never_replaced_by_an_equal_or_stricter_one(hubs):
    env = Env(hubs)
    env.hub.add("1", "UNQUALIFIED")  # closed in the CRM
    env.hub.add("2", "UNQUALIFIED")
    env.hub.add("3", "DO_NOT_CONTACT")  # opted out in the CRM
    env.revansa("1", S.PAUSED)  # Revansa only paused: that would loosen "closed"
    env.revansa("2", S.CLOSED)  # equal
    env.revansa("3", S.CLOSED)  # looser than an opt-out
    env.syncer.run_once()
    assert env.hub.patches == []
    assert [env.hub.value(i, "hs_lead_status") for i in "123"] == [
        "UNQUALIFIED",
        "UNQUALIFIED",
        "DO_NOT_CONTACT",
    ]


def test_a_contact_that_refuses_contact_is_never_touched_even_if_its_status_looks_open(hubs):
    env = Env(hubs)
    env.hub.add("1", "NEW", hs_email_optout="true")
    env.revansa("1", S.CLOSED, follow_up_count=2)
    env.syncer.run_once()
    assert env.hub.value("1", "hs_lead_status") == "NEW"  # not "loosened" nor rewritten


def test_a_contact_with_an_unmapped_or_blank_status_is_left_alone(hubs):
    env = Env(hubs)
    env.hub.add("1", "SOMETHING_ELSE")
    env.hub.add("2", None)
    env.revansa("1", S.CLOSED, follow_up_count=2)
    env.revansa("2", S.CLOSED, follow_up_count=2)
    env.syncer.run_once()
    assert env.hub.patches == []


def test_an_opt_out_is_propagated_only_if_the_operator_asked(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", S.OPTED_OUT)
    env.syncer.run_once()
    assert env.hub.patches == []  # not asked: it is not replaced by a lesser word either
    asked = Env(
        hubs,
        FakeHubSpot(),
        raw=settings(writeback=writeback_block(propagate_opt_out=True)),
    )
    asked.hub.add("1")
    asked.revansa("1", S.OPTED_OUT)
    asked.syncer.run_once()
    assert asked.hub.value("1", "hs_lead_status") == "DO_NOT_CONTACT"


def test_what_a_person_put_there_is_kept(hubs):
    env = Env(hubs)
    env.hub.add(
        "1",
        revansa_last_contact="2026-03-05T10:00:00.000Z",  # a later moment
        revansa_followups="9",  # a larger count
        revansa_next_action="call me",  # their own next action
        revansa_next_due="2026-03-09T09:00:00.000Z",
    )
    env.revansa(
        "1",
        None,
        last_contacted_at=T0,
        follow_up_count=2,
        next_action_type="follow_up",
        next_action_due_at=T0 + timedelta(days=1),
    )
    report = env.syncer.run_once()
    assert env.hub.patches == []
    assert report.skipped["not_advancing"] == 2 and report.skipped["occupied"] == 1
    assert env.hub.value("1", "revansa_followups") == "9"
    assert env.hub.value("1", "revansa_next_action") == "call me"


def test_an_earlier_value_is_advanced_and_an_empty_one_is_filled(hubs):
    env = Env(hubs)
    env.hub.add("1", revansa_last_contact="2026-02-20T08:30:00.000Z", revansa_followups="1")
    env.revansa("1", None, last_contacted_at=T0, follow_up_count=2, last_inbound_at=T0)
    env.syncer.run_once()
    assert env.hub.value("1", "revansa_last_contact") == "2026-03-01T09:00:00Z"
    assert env.hub.value("1", "revansa_followups") == "2"
    assert env.hub.value("1", "revansa_last_reply") == "2026-03-01T09:00:00Z"


def test_dates_are_read_whether_hubspot_gives_iso_text_or_milliseconds(hubs):
    env = Env(hubs)
    millis = str(int(T0.timestamp() * 1000))
    env.hub.add("1", revansa_last_contact=millis)
    env.hub.add("2", revansa_last_contact="2026-03-01T10:00:00+01:00")
    env.revansa("1", None, last_contacted_at=T0)
    env.revansa("2", None, last_contacted_at=T0)
    report = env.syncer.run_once()
    assert env.hub.patches == [] and report.skipped["already_applied"] == 2


def test_a_value_hubspot_holds_that_cannot_be_read_is_never_overwritten(hubs):
    env = Env(hubs)
    env.hub.add("1", revansa_last_contact="yesterday-ish", revansa_followups="a few")
    env.revansa("1", None, last_contacted_at=T0, follow_up_count=2)
    report = env.syncer.run_once()
    assert env.hub.patches == [] and report.skipped["unreadable_value"] >= 1


def test_a_next_action_revansa_wrote_itself_is_cleared_when_it_has_none_any_more(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", None, next_action_type="follow_up", next_action_due_at=T0 + timedelta(days=1))
    env.syncer.run_once()
    assert env.hub.value("1", "revansa_next_action") == "follow_up"
    env.revansa("1", None)  # nothing due any more
    env.clock.advance(hours=3)
    env.syncer.run_once()
    assert env.hub.value("1", "revansa_next_action") is None
    assert env.hub.value("1", "revansa_next_due") is None


# ---- a person edits while Revansa writes -----------------------------------------------------------------


def test_a_person_changing_the_status_after_it_was_read_is_never_overwritten(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", S.PAUSED)
    calls = []

    def person_closes(hub):
        calls.append(1)
        hub.contacts["1"]["hs_lead_status"] = "UNQUALIFIED"

    # the person's edit lands after the pass read the list, before the re-read of the contact
    original = env.source.list_prospects

    def list_then_edit():
        found = original()
        person_closes(env.hub)
        return found

    env.source.list_prospects = list_then_edit
    report = env.syncer.run_once()
    assert report.conflicts == 1 and env.hub.patches == []
    assert env.hub.value("1", "hs_lead_status") == "UNQUALIFIED"
    assert env.ledger.entries()[0].outcome is Outcome.CONFLICT


def test_one_stale_value_stops_the_whole_call_before_anything_is_sent(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.hub.add("2", revansa_followups="5")
    changes = [
        ProspectChange(prospect_id="1", new={F.STATUS: S.CLOSED}, expected={F.STATUS: S.NEW}),
        ProspectChange(
            prospect_id="2", new={F.FOLLOW_UP_COUNT: 6}, expected={F.FOLLOW_UP_COUNT: 3}
        ),
    ]
    with pytest.raises(SourceConflictError) as caught:
        env.source.update_prospects(changes)
    assert caught.value.prospect_id == "2" and caught.value.fields == ("follow_up_count",)
    assert env.hub.patches == []


def test_a_field_that_was_not_allowed_is_refused_before_any_request(hubs):
    hub = FakeHubSpot()
    hubs[hub.token] = hub
    source = build(
        hub, raw=settings(writeback={"properties": {"follow_up_count": "revansa_followups"}})
    )
    change = ProspectChange(prospect_id="1", new={F.STATUS: S.CLOSED}, expected={F.STATUS: S.NEW})
    with pytest.raises(SourceReadOnlyError):
        source.update_prospects([change])
    identity = ProspectChange(prospect_id="1", new={F.EMAIL: "a@b.c"}, expected={F.EMAIL: "x@y.z"})
    with pytest.raises(SourceReadOnlyError):
        source.update_prospects([identity])
    assert hub.requests == []


def test_a_contact_deleted_meanwhile_is_an_unknown_prospect_and_nothing_is_written(hubs):
    env = Env(hubs)
    env.hub.add("1")
    change = ProspectChange(prospect_id="99", new={F.STATUS: S.CLOSED}, expected={F.STATUS: S.NEW})
    with pytest.raises(KeyError):
        env.source.update_prospects([change])
    assert env.hub.patches == []


# ---- failures ------------------------------------------------------------------------------------------------


def two_pending(hubs, **kwargs):
    env = Env(hubs, **kwargs)
    env.hub.add("1")
    env.hub.add("2")
    env.revansa("1", S.CLOSED)
    env.revansa("2", S.CLOSED)
    return env


def test_a_rate_limit_stops_the_pass_waits_as_asked_and_then_writes(hubs):
    env = two_pending(hubs, batch_size=1)
    env.hub.fail("PATCH", 429, **{"Retry-After": "600"})
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.RATE_LIMITED and report.not_attempted == 1
    assert len(env.hub.patches) == 1 and env.hub.value("1", "hs_lead_status") == "NEW"
    env.clock.advance(minutes=5)
    held = env.syncer.run_once()
    assert held.held_back == 1  # contact 1 waits as HubSpot asked; it is not hammered
    assert [r.url.path[-1] for r in env.hub.patches] == ["1", "2"]
    assert env.hub.value("1", "hs_lead_status") == "NEW"
    env.clock.advance(minutes=6)
    env.syncer.run_once()
    assert [r.url.path[-1] for r in env.hub.patches] == ["1", "2", "1"]
    assert env.hub.value("1", "hs_lead_status") == "UNQUALIFIED"


@pytest.mark.parametrize("status", [401, 403])
def test_refused_scopes_stop_the_pass_and_leak_nothing(hubs, status):
    env = two_pending(hubs, batch_size=1)
    env.hub.fail("PATCH", status)
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.AUTH and report.failed == 1
    assert env.hub.value("1", "hs_lead_status") == "NEW"
    assert TOKEN_A not in repr(env.ledger.entries()) and "secret-detail" not in repr(report)
    assert TOKEN_A not in repr(env.source) and TOKEN_A not in repr(env.syncer)


def test_a_hubspot_server_error_is_deferred_and_retried_later(hubs):
    env = two_pending(hubs)
    env.hub.fail("PATCH", 503)
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.UNAVAILABLE and report.deferred == 2
    env.clock.advance(hours=1)
    env.syncer.run_once()
    assert env.hub.value("2", "hs_lead_status") == "UNQUALIFIED"


def test_an_unreachable_hubspot_on_the_read_writes_nothing_and_says_why(hubs):
    env = two_pending(hubs)
    env.hub.fail("GET", 500)
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.UNAVAILABLE and env.hub.patches == []
    assert env.ledger.entries() == ()


def test_a_value_hubspot_refuses_is_a_failure_not_a_retry_storm(hubs):
    hub = FakeHubSpot()
    hub.definitions["hs_lead_status"]["options"] = [{"value": "NEW"}]  # the word was removed
    env = Env(hubs, hub)
    env.hub.add("1")
    env.revansa("1", S.CLOSED)
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.UNEXPECTED and report.failed == 1


# ---- restarts ------------------------------------------------------------------------------------------------------


@pytest.fixture
def database(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


def durable(database, client=A):
    return sql_ledgers(database)(client)


def test_a_restart_does_not_write_twice(hubs, database):
    first = Env(hubs, ledger=durable(database))
    first.hub.add("1")
    first.revansa("1", S.CLOSED, last_contacted_at=T0, follow_up_count=2)
    first.syncer.run_once()
    sent = len(first.hub.patches)
    assert sent == 1
    # the process restarts: a new source, syncer and ledger over the same database and account
    second = Env(hubs, first.hub, ledger=durable(database))
    second.store = first.store
    second.syncer = WriteBackSyncer(
        second.source,
        second.store,
        second.world.registry,
        second.ledger,
        WriteBackPolicy(fields=second.source.capabilities.writable),
        clock=second.clock,
    )
    report = second.syncer.run_once()
    assert report.applied == 0 and len(first.hub.patches) == sent


def test_an_answer_lost_after_the_write_is_recovered_after_a_restart_without_writing_again(
    hubs, database
):
    first = Env(hubs, ledger=durable(database))
    first.hub.add("1")
    first.revansa("1", S.CLOSED)
    first.hub.lose_answer_of_next_patch = True  # applied in HubSpot, the answer is a 502
    report = first.syncer.run_once()
    assert report.deferred == 1 and first.hub.value("1", "hs_lead_status") == "UNQUALIFIED"
    second = Env(hubs, first.hub, ledger=durable(database))
    second.store = first.store
    second.syncer = WriteBackSyncer(
        second.source,
        second.store,
        second.world.registry,
        second.ledger,
        WriteBackPolicy(fields=second.source.capabilities.writable),
        clock=second.clock,
    )
    again = second.syncer.run_once()
    assert again.recovered == 1 and len(first.hub.patches) == 1
    assert [e.outcome for e in second.ledger.entries()][-1] is Outcome.APPLIED


# ---- verification: ready only when it really is ---------------------------------------------------------------


def test_a_source_is_ready_only_when_hubspot_answers_and_every_property_fits(hubs):
    env = Env(hubs)
    assert env.source.verify() == []


def test_a_refused_token_is_not_ready(hubs):
    hub = FakeHubSpot()
    env = Env(hubs, hub)
    hub.token = TOKEN_B  # the account no longer accepts the token the client holds
    assert env.source.verify() == ["HubSpot refused the access token, or it may not read contacts"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda d: d.pop("revansa_followups"), "does not exist"),
        (lambda d: d["revansa_followups"].update(type="string"), "must be number"),
        (lambda d: d["revansa_last_reply"].update(type="string"), "must be datetime"),
        (
            lambda d: d["revansa_next_action"].update(modificationMetadata={"readOnlyValue": True}),
            "cannot be set",
        ),
        (lambda d: d["revansa_next_due"].update(calculated=True), "cannot be set"),
        (lambda d: d["hs_lead_status"].update(options=[{"value": "NEW"}]), "does not offer"),
        (lambda d: d["hs_lead_status"].update(type="number"), "not a choice list"),
    ],
)
def test_a_property_that_does_not_fit_makes_the_source_not_ready_and_nothing_is_written(
    hubs, change, expected
):
    hub = FakeHubSpot()
    change(hub.definitions)
    env = Env(hubs, hub)
    problems = env.source.verify()
    assert problems and all(p.startswith("write-back:") for p in problems)
    assert any(expected in p for p in problems)
    hub.add("1")
    env.revansa("1", S.CLOSED)
    support = WritebackSupport(
        enabled=True, open_ledger=lambda cid: env.ledger, policies=default_policies()
    )
    runner, issues = writeback_for(env, support)
    summary = runner.run()
    assert not issues and summary.not_ready == 1 and hub.patches == []


def test_missing_property_scopes_are_named_in_the_problem(hubs):
    hub = FakeHubSpot()
    env = Env(hubs, hub)
    original = hub.__call__

    def deny_schema(request):
        if request.url.path.startswith("/crm/v3/properties"):
            return httpx.Response(403, json={})
        return original(request)

    transport = httpx.MockTransport(deny_schema)
    env.source._reader._client._http = lambda: httpx.Client(transport=transport)
    (problem,) = env.source.verify()
    assert "crm.schemas.contacts.read" in problem


def test_a_missing_property_is_never_created(hubs):
    hub = FakeHubSpot()
    hub.definitions.pop("revansa_followups")
    env = Env(hubs, hub)
    env.source.verify()
    hub.add("1")
    env.revansa("1", None, follow_up_count=2)
    assert "POST" not in hub.methods()
    assert all(
        r.url.path.startswith(("/crm/v3/objects", "/crm/v3/properties/contacts/"))
        for r in hub.requests
    )


# ---- the deployment and the client ----------------------------------------------------------------------------------


def writeback_for(
    env, support, *, mode=ApprovalMode.APPROVAL, extra_sources=None, extra_policies=None
):
    sources = {env.ns: env.source, **(extra_sources or {})}
    entries = [SimpleNamespace(id=env.ns, provider="hubspot", settings=settings())]
    entries += [SimpleNamespace(id=i, provider="fake", settings={}) for i in (extra_sources or {})]
    config = SimpleNamespace(
        client_id=env.client, effective_mode=mode, spec=SimpleNamespace(sources=entries)
    )
    if extra_policies:
        support = WritebackSupport(
            enabled=support.enabled,
            open_ledger=support.open_ledger,
            policies={**support.policies, **extra_policies},
        )
    return _build(config, sources, env, support)


def _build(config, sources, env, support):
    from sales_app.wiring.writeback import build_client_writeback

    return build_client_writeback(
        config, sources, env.store, env.world.registry, support, SimpleNamespace(now=env.clock)
    )


def ledgers_in_memory(env):
    return lambda client: env.ledger


def test_with_the_deployment_flag_off_nothing_is_built_read_or_written(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", S.CLOSED)
    support = WritebackSupport(
        enabled=False, open_ledger=ledgers_in_memory(env), policies=default_policies()
    )
    runner, problems = writeback_for(env, support)
    assert runner is None and problems == []
    assert env.hub.requests == []


def test_with_the_deployment_flag_on_the_source_that_asks_is_written_and_the_summary_has_counts_only(
    hubs,
):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", S.CLOSED, follow_up_count=2)
    support = WritebackSupport(
        enabled=True, open_ledger=ledgers_in_memory(env), policies=default_policies()
    )
    runner, problems = writeback_for(env, support)
    assert problems == [] and runner.source_ids == ("hs_a",)
    summary = runner.run()
    assert summary.applied == 2 and summary.errors == ()
    assert env.hub.value("1", "hs_lead_status") == "UNQUALIFIED"
    assert set(summary.counters()) >= {"sources", "not_ready", "stopped", "applied", "failed"}


def test_a_client_in_dry_run_is_never_written(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", S.CLOSED)
    support = WritebackSupport(
        enabled=True, open_ledger=ledgers_in_memory(env), policies=default_policies()
    )
    runner, problems = writeback_for(env, support, mode=ApprovalMode.DRY_RUN)
    assert runner is None and problems == [] and env.hub.requests == []


def test_a_source_without_a_block_asks_for_nothing(hubs):
    env = Env(hubs, raw={"status_mapping": settings()["status_mapping"]})
    support = WritebackSupport(
        enabled=True, open_ledger=ledgers_in_memory(env), policies=default_policies()
    )
    config = SimpleNamespace(
        client_id=A,
        effective_mode=ApprovalMode.APPROVAL,
        spec=SimpleNamespace(
            sources=[
                SimpleNamespace(
                    id="hs_a",
                    provider="hubspot",
                    settings={"status_mapping": settings()["status_mapping"]},
                )
            ]
        ),
    )
    runner, problems = _build(config, {"hs_a": env.source}, env, support)
    assert runner is None and problems == [] and env.hub.requests == []


def test_an_ambiguous_mapping_is_a_configuration_problem_not_a_silent_skip(hubs):
    env = Env(hubs)
    support = WritebackSupport(
        enabled=True, open_ledger=ledgers_in_memory(env), policies=default_policies()
    )
    bad = settings(status_mapping={**settings()["status_mapping"], "LOST": "closed"})
    config = SimpleNamespace(
        client_id=A,
        effective_mode=ApprovalMode.APPROVAL,
        spec=SimpleNamespace(
            sources=[SimpleNamespace(id="hs_a", provider="hubspot", settings=bad)]
        ),
    )
    runner, problems = _build(config, {"hs_a": env.source}, env, support)
    assert runner is None and problems and "closed" in problems[0]
    assert env.hub.requests == []


# ---- isolation ------------------------------------------------------------------------------------------------------------


def test_two_clients_with_the_same_ids_never_touch_each_others_account(hubs, database):
    one = Env(hubs, FakeHubSpot(TOKEN_A), client=A, ledger=durable(database, A))
    two = Env(hubs, FakeHubSpot(TOKEN_B), client=B, ledger=durable(database, B))
    for env in (one, two):
        env.hub.add("1")
    one.revansa("1", S.CLOSED)
    # client B's Revansa holds nothing for this contact
    one.syncer.run_once()
    two.syncer.run_once()
    assert one.hub.value("1", "hs_lead_status") == "UNQUALIFIED"
    assert two.hub.value("1", "hs_lead_status") == "NEW" and two.hub.patches == []
    assert {r.headers["Authorization"] for r in one.hub.requests} == {f"Bearer {TOKEN_A}"}
    assert {r.headers["Authorization"] for r in two.hub.requests} == {f"Bearer {TOKEN_B}"}
    assert len(durable(database, A).entries()) == 1 and durable(database, B).entries() == ()


def test_a_hubspot_source_and_another_source_of_the_same_client_do_not_interfere(hubs):
    env = Env(hubs)
    env.hub.add("1")
    env.revansa("1", S.CLOSED)
    sheet = FakeProspectSource(
        ClientId(A), capabilities=default_capabilities(id_namespace="sheet_a"), allow_writes=True
    )
    sheet.verify_issues.append("a chosen column is gone")  # the other source is not ready
    support = WritebackSupport(
        enabled=True, open_ledger=ledgers_in_memory(env), policies=default_policies()
    )
    runner, problems = writeback_for(
        env,
        support,
        extra_sources={"sheet_a": sheet},
        extra_policies={"fake": lambda s: ({F.STATUS}, False)},
    )
    assert problems == [] and set(runner.source_ids) == {"hs_a", "sheet_a"}
    summary = runner.run()
    assert summary.not_ready == 1 and summary.applied == 1
    assert env.hub.value("1", "hs_lead_status") == "UNQUALIFIED"
    assert {str(e.prospect_id) for e in env.ledger.entries()} == {"hs_a:1"}


# ---- the commercial recap ------------------------------------------------------------------------


def recap_env(hubs, **overrides):
    from hubspot_writeback_support import DEFINITIONS

    hub = FakeHubSpot(definitions={**DEFINITIONS, "revansa_summary": {"type": "string"}})
    raw = settings(
        writeback={"status": True, "properties": {**PROPERTIES, "summary": "revansa_summary"}},
        **overrides,
    )
    return Env(hubs, hub, raw=raw)


def test_a_commercial_recap_is_written_in_its_own_property_and_holds_no_personal_data(hubs):
    env = recap_env(hubs)
    env.hub.add("1", firstname="Ada", email="ada@example.org")
    env.revansa(
        "1",
        S.ACTIVE,
        last_contacted_at=T0,
        last_inbound_at=T0 - timedelta(hours=3),
        follow_up_count=2,
        next_action_type="follow_up",
        next_action_due_at=T0 + timedelta(days=2),
    )
    env.syncer.run_once()
    text = env.hub.value("1", "revansa_summary")
    assert text == (
        "Revansa - status: active; follow-ups: 2; last message: 2026-03-01; "
        "last reply: 2026-03-01; next: follow_up on 2026-03-03"
    )
    assert "Ada" not in text and "@" not in text and len(text) <= 240


def test_the_recap_follows_revansa_but_never_replaces_a_persons_own_text(hubs):
    env = recap_env(hubs)
    env.hub.add("1")
    env.hub.add("2", revansa_summary="Call back after the holidays (Sam)")
    env.revansa("1", S.ACTIVE, follow_up_count=1)
    env.revansa("2", S.ACTIVE, follow_up_count=1)
    env.syncer.run_once()
    first = env.hub.value("1", "revansa_summary")
    assert first.endswith("follow-ups: 1")
    assert env.hub.value("2", "revansa_summary") == "Call back after the holidays (Sam)"
    env.revansa("1", S.ACTIVE, follow_up_count=2)  # Revansa moves on: its own text is renewed
    env.clock.advance(hours=3)
    env.syncer.run_once()
    assert env.hub.value("1", "revansa_summary").endswith("follow-ups: 2")
    assert env.hub.value("2", "revansa_summary") == "Call back after the holidays (Sam)"


def test_a_recap_with_nothing_to_say_is_not_written(hubs):
    env = recap_env(hubs)
    env.hub.add("1")
    env.revansa("1", None)
    env.syncer.run_once()
    assert env.hub.value("1", "revansa_summary") in (None, "")
