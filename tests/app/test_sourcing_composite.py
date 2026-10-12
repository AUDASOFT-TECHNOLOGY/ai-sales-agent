"""The composite repository: a source, a store and the consent registry presented as one repository.

Deterministic: the fakes of `sourcing_fakes`, an in-memory registry with a clock that only moves when
told to, no network, no real time.
"""

import logging
from dataclasses import replace

import pytest
from consent_support import EMAIL, LEAKY, PHONE, make_world
from consent_support import contact as consent_contact
from factories import CLIENT, NOW, make_action, make_message, make_prospect
from sourcing_fakes import (
    FakeOperationalStore,
    FakeProspectSource,
    default_capabilities,
)

from sales_app.sourcing import (
    ProblemCode,
    SourceReadOnlyError,
    SourceUnavailableError,
    StateConflictError,
    StatusMapping,
    StoreUnavailableError,
)
from sales_app.sourcing.composite import (
    KEYWORD_ACTOR,
    KEYWORD_OPT_OUT_EVENT,
    CompositeRepository,
)
from sales_app.sourcing.consent import ConsentUnavailableError, Origin, ReasonCode
from sales_core.contracts import (
    AuditEvent,
    Channel,
    ContactInfo,
    Fact,
    FactStatus,
    ProspectId,
    ProspectStatus,
    Scheduling,
)
from sales_core.ports import FactsReader, FactsWriter, SalesRepository

S = ProspectStatus
MAPPING = StatusMapping.from_config({"lead": "new", "hold": "paused", "dnc": "opted_out"})
OTHER_PHONE = "+216 99 555 666"


def person(pid, phone=None, email=None, **overrides):
    contact = ContactInfo(phone=phone, email=email, preferred_channel=Channel.WHATSAPP)
    return make_prospect(prospect_id=ProspectId(pid), contact=contact, **overrides)


class SpyConsent:
    """Wraps a real registry: counts snapshots, can refuse to record."""

    def __init__(self, registry, *, fail_record=False):
        self._registry = registry
        self._fail_record = fail_record
        self.snapshots = 0
        self.recorded = []

    def snapshot(self):
        self.snapshots += 1
        return self._registry.snapshot()

    def record_opt_out(self, contact, **kwargs):
        if self._fail_record:
            raise ConsentUnavailableError("unavailable", "internal_error")
        self.recorded.append(kwargs)
        return self._registry.record_opt_out(contact, **kwargs)


class Setup:
    def __init__(
        self, *, namespace="fake_crm", initialise=True, fail_record=False, store=None, **source_kw
    ):
        capabilities = default_capabilities(id_namespace=namespace)
        self.source = FakeProspectSource(
            CLIENT, capabilities=capabilities, status_mapping=MAPPING, **source_kw
        )
        self.store = FakeOperationalStore(CLIENT)
        self.world = make_world(client_id=CLIENT, initialise=initialise)
        self.consent = SpyConsent(self.world.registry, fail_record=fail_record)
        self.repo = CompositeRepository(self.source, self.store, self.consent)

    def crm(self, pid, phone=None, email=None, raw="lead", **kw):
        self.source.add_record(person(pid, phone, email), raw_status=raw, **kw)


@pytest.fixture
def env():
    return Setup()


def public(pid):
    return ProspectId(f"fake_crm:{pid}")


# ---- reading ---------------------------------------------------------------------------------


def test_it_is_a_sales_repository_and_a_facts_port(env):
    assert isinstance(env.repo, SalesRepository)
    assert isinstance(env.repo, FactsReader) and isinstance(env.repo, FactsWriter)


def test_a_source_and_a_store_of_two_clients_are_refused():
    source = FakeProspectSource(CLIENT, status_mapping=MAPPING)
    with pytest.raises(ValueError):
        CompositeRepository(source, FakeOperationalStore("client-b"), make_world().registry)


def test_the_core_sees_public_ids_and_the_statuses_of_both_sides(env):
    env.crm("1", PHONE)
    env.crm("2", OTHER_PHONE, raw="hold")
    listed = {p.prospect_id: p for p in env.repo.list_prospects()}
    assert set(listed) == {public("1"), public("2")}
    assert listed[public("1")].status is S.NEW
    assert listed[public("2")].status is S.PAUSED


def test_a_restriction_held_by_revansa_beats_a_contactable_source(env):
    env.crm("1", PHONE)
    closed = replace(env.repo.get_prospect(public("1")), status=S.CLOSED)
    env.repo.update_prospects([closed])
    assert env.repo.get_prospect(public("1")).status is S.CLOSED
    assert env.repo.list_prospects()[0].status is S.CLOSED


def test_a_contact_blocked_in_the_registry_is_opted_out_everywhere(env):
    env.crm("1", PHONE)
    env.crm("2", email=EMAIL)
    env.world.block(consent_contact(PHONE))
    env.world.block(consent_contact(None, EMAIL))
    assert {p.status for p in env.repo.list_prospects()} == {S.OPTED_OUT}
    assert env.repo.get_prospect(public("1")).status is S.OPTED_OUT
    assert env.repo.find_prospect_by_contact(phone="21699888777").status is S.OPTED_OUT
    assert env.repo.find_prospect_by_contact(email=EMAIL.upper()).status is S.OPTED_OUT


def test_the_registry_is_read_once_per_list(env):
    for n in range(5):
        env.crm(str(n), f"+216 99 000 00{n}")
    env.repo.list_prospects()
    assert env.consent.snapshots == 1


def test_an_unhealthy_registry_blocks_everyone_without_failing_the_read():
    env = Setup(initialise=False)
    env.crm("1", PHONE)
    assert [p.status for p in env.repo.list_prospects()] == [S.OPTED_OUT]
    assert env.repo.get_prospect(public("1")).status is S.OPTED_OUT


def test_a_refusal_on_a_left_out_record_is_recorded_and_binds_other_records_at_once(env):
    env.source.add_record(person("9", PHONE), raw_status="unknown value", do_not_contact=True)
    env.crm("1", "+216 99 888 777")  # the same number, on a record that is kept
    listed = env.repo.list_prospects()
    assert [p.prospect_id for p in listed] == [public("1")]
    assert listed[0].status is S.OPTED_OUT  # in the very read that learned of the refusal
    assert env.world.registry.snapshot().blocks(consent_contact(PHONE))
    env.source.clear_refusals()  # a person clears the flag in the CRM: the refusal stays
    assert env.repo.list_prospects()[0].status is S.OPTED_OUT


def test_refusals_already_recorded_are_not_recorded_again(env):
    env.source.add_record(person("9", PHONE), raw_status="unknown value", do_not_contact=True)
    env.repo.list_prospects()
    events = len(env.world.registry.events())
    env.repo.list_prospects()
    assert len(env.world.registry.events()) == events


def test_a_registry_that_cannot_record_still_blocks_the_contacts_of_the_read():
    env = Setup(fail_record=True)
    env.source.add_record(person("9", PHONE), raw_status="unknown value", do_not_contact=True)
    env.crm("1", PHONE)
    assert [p.status for p in env.repo.list_prospects()] == [S.OPTED_OUT]


def test_a_failing_store_fails_the_read_instead_of_answering_from_the_source_alone(env):
    env.crm("1", PHONE)
    env.store.fail_next("list_states")
    with pytest.raises(StoreUnavailableError):
        env.repo.list_prospects()


def test_a_failing_source_fails_the_read(env):
    env.source.fail_next("list_prospects")
    with pytest.raises(SourceUnavailableError):
        env.repo.list_prospects()
    env.source.fail_next("get_prospect")
    with pytest.raises(SourceUnavailableError):
        env.repo.get_prospect(public("1"))


def test_unknown_ids_are_none(env):
    assert env.repo.get_prospect(public("nope")) is None
    assert env.repo.get_prospect(ProspectId("inbound-2026-01-15-1")) is None
    assert env.repo.find_prospect_by_contact(phone="999") is None


def test_the_source_wins_a_collision_of_ids_and_the_conflict_is_reported():
    env = Setup(namespace=None)
    local = person("inbound-2026-01-15-5", "+216 99 123 456")
    env.repo.create_prospects([local])
    env.source.add_record(person("inbound-2026-01-15-5", "+216 99 654 321"), raw_status="lead")
    listed = env.repo.list_prospects()
    assert [p.contact.phone for p in listed] == ["+216 99 654 321"]
    assert [p.code for p in env.repo.problems()] == [ProblemCode.DUPLICATE_CONTACT]
    assert "216" not in repr(env.repo.problems())


# ---- creating --------------------------------------------------------------------------------


def test_an_unknown_sender_becomes_a_local_prospect_and_never_reaches_the_source(env):
    local = person("inbound-2026-01-15-1", "+216 99 123 456")
    env.repo.create_prospects([local])
    assert env.repo.get_prospect(local.prospect_id).contact.phone == "+216 99 123 456"
    assert env.repo.find_prospect_by_contact(phone="21699123456").prospect_id == local.prospect_id
    assert not [r for r in env.source.requests if r.startswith("write:")]


def test_a_person_who_refused_is_not_recreated_as_contactable(env):
    env.world.block(consent_contact(PHONE))
    env.repo.create_prospects([person("inbound-2026-01-15-1", PHONE, status=S.NEW)])
    assert env.repo.get_prospect(ProspectId("inbound-2026-01-15-1")).status is S.OPTED_OUT
    assert env.store.get_local_prospect(ProspectId("inbound-2026-01-15-1")).status is S.OPTED_OUT


def test_creation_refuses_a_taken_contact_a_taken_id_and_another_client(env):
    env.crm("1", PHONE)
    with pytest.raises(ValueError):
        env.repo.create_prospects([person("inbound-2026-01-15-1", PHONE)])
    env.repo.create_prospects([person("inbound-2026-01-15-2", OTHER_PHONE)])
    with pytest.raises(ValueError):
        env.repo.create_prospects([person("inbound-2026-01-15-2", "+216 99 777 000")])
    with pytest.raises(ValueError):
        env.repo.create_prospects([person("inbound-2026-01-15-3", client_id="client-b")])
    with pytest.raises(ValueError):
        env.repo.create_prospects([person("not-a-local-id", "+216 99 777 111")])


def test_a_refused_batch_creates_nothing(env):
    with pytest.raises(ValueError):
        env.repo.create_prospects(
            [person("inbound-2026-01-15-1", "+216 99 100 100"), person("bad id")]
        )
    assert env.store.list_local_prospects() == []


# ---- updating --------------------------------------------------------------------------------


def test_revansas_fields_go_to_the_store_and_the_source_is_never_written(env):
    env.crm("1", PHONE)
    before = dict(env.source.records)
    prospect = env.repo.get_prospect(public("1"))
    env.repo.update_prospects(
        [
            replace(
                prospect,
                status=S.ACTIVE,
                stage="qualified",
                conversation_id=None,
                schedule=Scheduling(last_contacted_at=NOW, follow_up_count=2),
            )
        ]
    )
    again = env.repo.get_prospect(public("1"))
    assert again.status is S.ACTIVE and again.stage == "qualified"
    assert again.schedule.last_contacted_at == NOW and again.schedule.follow_up_count == 2
    assert env.source.records == before
    assert not [r for r in env.source.requests if r.startswith("write:")]


def test_a_next_action_is_stored_as_a_pair(env):
    from sales_core.contracts import SalesActionType

    env.crm("1", PHONE)
    prospect = env.repo.get_prospect(public("1"))
    schedule = Scheduling(next_action_type=SalesActionType.FOLLOW_UP, next_action_due_at=NOW)
    env.repo.update_prospects([replace(prospect, schedule=schedule)])
    assert env.repo.get_prospect(public("1")).schedule == schedule
    env.repo.update_prospects([replace(prospect, schedule=Scheduling())])
    assert env.repo.get_prospect(public("1")).schedule == Scheduling()


def test_a_local_prospect_is_updated_through_its_state(env):
    local = person("inbound-2026-01-15-1", PHONE)
    env.repo.create_prospects([local])
    env.repo.update_prospects([replace(env.repo.get_prospect(local.prospect_id), status=S.ACTIVE)])
    assert env.repo.get_prospect(local.prospect_id).status is S.ACTIVE
    assert env.repo.list_prospects()[0].status is S.ACTIVE


def test_an_unknown_prospect_raises_key_error_before_anything_is_written(env):
    env.crm("1", PHONE)
    known = replace(env.repo.get_prospect(public("1")), status=S.ACTIVE)
    ghost = replace(known, prospect_id=public("ghost"))
    with pytest.raises(KeyError):
        env.repo.update_prospects([known, ghost])
    assert env.store.get_states([public("1")]) == {}
    assert env.repo.get_prospect(public("1")).status is S.NEW


def test_a_change_to_a_field_the_source_owns_is_refused_and_nothing_is_written(env):
    env.crm("1", PHONE)
    env.crm("2", OTHER_PHONE)
    first = replace(env.repo.get_prospect(public("1")), status=S.ACTIVE)
    second = replace(env.repo.get_prospect(public("2")), display_name="Someone Else")
    with pytest.raises(SourceReadOnlyError):
        env.repo.update_prospects([first, second])
    assert env.store.get_states([public("1"), public("2")]) == {}
    with pytest.raises(SourceReadOnlyError):
        env.repo.update_prospects(
            [replace(first, contact=replace(first.contact, phone="+216 99 000 999"))]
        )


def test_the_identity_of_a_local_prospect_does_not_change(env):
    local = person("inbound-2026-01-15-1", PHONE)
    env.repo.create_prospects([local])
    with pytest.raises(SourceReadOnlyError):
        env.repo.update_prospects([replace(local, display_name="Renamed")])


def test_a_prospect_that_did_not_change_writes_nothing(env):
    env.crm("1", PHONE)
    env.repo.update_prospects([env.repo.get_prospect(public("1"))])
    assert env.store.list_states() == {}


def test_a_state_conflict_is_retried_once_then_reported(env):
    env.crm("1", PHONE)
    active = replace(env.repo.get_prospect(public("1")), status=S.ACTIVE)
    env.store.fail_next("save_states", StateConflictError("x"))
    env.repo.update_prospects([active])
    assert env.repo.get_prospect(public("1")).status is S.ACTIVE
    env.crm("2", OTHER_PHONE)
    paused = replace(env.repo.get_prospect(public("2")), status=S.PAUSED)
    env.store.fail_next("save_states", StateConflictError("x"), times=2)
    with pytest.raises(StateConflictError):
        env.repo.update_prospects([paused])
    assert env.repo.get_prospect(public("2")).status is S.NEW


def test_a_retry_keeps_what_another_writer_changed_between_the_plan_and_the_save(env):
    env.crm("1", PHONE)
    prospect = env.repo.get_prospect(public("1"))
    real_save = env.store.save_states
    calls = []

    def save(states):
        calls.append(1)
        if len(calls) == 1:  # another process moves the stage, then our save is found stale
            held = env.store.get_states([public("1")]).get(public("1"))
            from sales_app.sourcing import ProspectState

            other = held or ProspectState(client_id=CLIENT, prospect_id=public("1"))
            real_save([replace(other, stage="a")])
        real_save(states)

    env.store.save_states = save
    env.repo.update_prospects([replace(prospect, status=S.ACTIVE)])
    now = env.repo.get_prospect(public("1"))
    assert len(calls) == 2
    assert now.status is S.ACTIVE and now.stage == "a"


def test_an_opt_out_status_alone_never_writes_the_registry(env):
    env.crm("1", PHONE)
    env.repo.update_prospects([replace(env.repo.get_prospect(public("1")), status=S.OPTED_OUT)])
    assert env.consent.recorded == []
    assert env.world.registry.events() == ()


# ---- facts -----------------------------------------------------------------------------------


def _fact(key="interest"):
    return Fact(key=key, value="yes", status=FactStatus.CONFIRMED, recorded_at=NOW)


def test_facts_are_checked_against_both_homes_before_being_written(env):
    env.crm("1", PHONE)
    local = person("inbound-2026-01-15-1", OTHER_PHONE)
    env.repo.create_prospects([local])
    env.repo.append_facts(public("1"), [_fact()])
    env.repo.append_facts(local.prospect_id, [_fact()])
    assert len(env.repo.get_facts(public("1"))) == 1
    assert len(env.repo.get_facts(local.prospect_id)) == 1


def test_facts_for_an_unknown_prospect_raise_key_error_and_write_nothing(env):
    with pytest.raises(KeyError):
        env.repo.append_facts(public("ghost"), [_fact()])
    assert env.store.get_facts(public("ghost")) == []


# ---- the rest and the audit trail ------------------------------------------------------------


def test_actions_messages_and_conversation_are_the_stores(env):
    env.crm("1", PHONE)
    action = make_action(prospect_id=public("1"))
    env.repo.save_actions([action])
    assert env.repo.find_action_by_idempotency_key(action.idempotency_key) == action
    assert env.repo.list_actions() == [action]
    message = make_message(prospect_id=public("1"))
    env.repo.append_messages([message])
    assert env.repo.get_conversation(public("1")).messages == (message,)


def _audit(event_type, pid, client=CLIENT):
    return AuditEvent(
        timestamp=NOW, client_id=client, event_type=event_type, actor="core", prospect_id=pid
    )


def test_the_event_the_composite_listens_for_is_the_engines():
    from sales_core.engine.events import PROSPECT_OPTED_OUT

    assert KEYWORD_OPT_OUT_EVENT == PROSPECT_OPTED_OUT


def test_a_keyword_opt_out_is_recorded_in_the_registry_with_its_origin(env):
    env.crm("1", PHONE)
    env.repo.append_audit_events([_audit(KEYWORD_OPT_OUT_EVENT, public("1"))])
    assert env.world.registry.snapshot().blocks(consent_contact(PHONE))
    (event,) = env.world.registry.events()
    assert event.origin is Origin.INBOUND_KEYWORD and event.reason is ReasonCode.KEYWORD
    assert event.actor == KEYWORD_ACTOR
    assert len(env.store.audit_events) == 1


def test_other_audit_events_do_not_touch_the_registry(env):
    env.crm("1", PHONE)
    env.repo.append_audit_events([_audit("action.sent", public("1"))])
    assert env.world.registry.events() == ()
    assert len(env.store.audit_events) == 1


def test_if_the_registry_cannot_record_the_trail_is_not_written_so_it_can_be_offered_again():
    env = Setup(fail_record=True)
    env.crm("1", PHONE)
    with pytest.raises(ConsentUnavailableError):
        env.repo.append_audit_events([_audit(KEYWORD_OPT_OUT_EVENT, public("1"))])
    assert env.store.audit_events == ()


def test_an_audit_event_of_another_client_is_refused(env):
    with pytest.raises(ValueError):
        env.repo.append_audit_events([_audit("action.sent", None, client="client-b")])


# ---- no personal data leaks ------------------------------------------------------------------


def test_nothing_personal_appears_in_logs_errors_or_representations(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.source.add_record(
        person("9", PHONE, EMAIL), raw_status="unknown value", do_not_contact=True
    )
    env.crm("1", PHONE, EMAIL)
    local = person("inbound-2026-01-15-21699888777", OTHER_PHONE)
    texts = [repr(env.repo)]
    env.repo.list_prospects()
    env.repo.create_prospects([local])
    errors = []
    for call in (
        lambda: env.repo.append_facts(ProspectId("inbound-2026-01-15-21699888777x"), [_fact()]),
        lambda: env.repo.update_prospects([replace(local, display_name="Renamed")]),
        lambda: env.repo.update_prospects(
            [replace(local, prospect_id=ProspectId("inbound-2026-01-15-21699888777z"))]
        ),
        lambda: env.repo.create_prospects([person("inbound-2026-01-15-77", PHONE)]),
    ):
        with pytest.raises((KeyError, ValueError, SourceReadOnlyError)) as caught:
            call()
        errors.append(str(caught.value) + repr(caught.value))
    texts += errors + [repr(env.repo.problems()), caplog.text]
    blob = " ".join(texts)
    for leaky in (*LEAKY, "21699888777", "2026-01-15-21699888777"):
        assert leaky not in blob
