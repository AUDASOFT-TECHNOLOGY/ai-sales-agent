"""The contracts of `ProspectSource` and `OperationalStore`, checked against their fakes.

These are the tests a real adapter will have to pass too: when a connector arrives, its wrapper is
added to the fixtures below and every test applies to it unchanged. Whatever the ports say must be
true of the fake, so the code that composes them can be tested on it.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from factories import CLIENT, NOW, make_action, make_message, make_prospect
from sourcing_fakes import (
    FakeOperationalStore,
    FakeProspectSource,
    default_capabilities,
    spreadsheet_capabilities,
)

from sales_app.sourcing import (
    SOURCE_ONLY_FIELDS,
    OperationalStore,
    ProblemCode,
    ProspectChange,
    ProspectField,
    ProspectSource,
    ProspectState,
    SourceAuthError,
    SourceConflictError,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
    StateConflictError,
    StatusMapping,
    StoreUnavailableError,
    contact_keys,
    merge_prospect,
)
from sales_core.contracts import (
    ActionId,
    ActionStatus,
    AuditEvent,
    Channel,
    ContactInfo,
    ConversationId,
    Fact,
    FactStatus,
    MessageId,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)
from sales_core.ports import FactsReader, FactsWriter

S = ProspectStatus
OTHER = "client-b"
MAPPING = StatusMapping.from_config(
    {"lead": "new", "hold": "paused", "dead": "closed", "dnc": "opted_out"}
)


def person(pid, name="Someone", phone=None, email=None, **overrides):
    contact = ContactInfo(phone=phone, email=email, preferred_channel=Channel.WHATSAPP)
    return make_prospect(
        prospect_id=ProspectId(pid), display_name=name, contact=contact, **overrides
    )


def new_source(kind, **kw):
    capabilities = default_capabilities() if kind == "crm" else spreadsheet_capabilities()
    return FakeProspectSource(CLIENT, capabilities=capabilities, status_mapping=MAPPING, **kw)


def seed(source):
    source.add_record(person("1001", "Ana Test", "+216 22 000 001", "ana@example.com"))
    source.add_record(person("1002", "Ben Test", "+216 22 000 002"))
    source.add_record(person("1003", "Chloe Test", None, "chloe@example.com"))


def status_change(pid="1001", new=S.PAUSED, expected=S.NEW):
    return ProspectChange(
        prospect_id=pid,
        new={ProspectField.STATUS: new},
        expected={ProspectField.STATUS: expected},
    )


# ---- ProspectSource ------------------------------------------------------------------------------


@pytest.fixture(params=["crm", "spreadsheet"])
def kind(request):
    return request.param


@pytest.fixture
def source(kind):
    made = new_source(kind)
    seed(made)
    return made


@pytest.fixture
def writable(kind):
    made = new_source(kind, allow_writes=True)
    seed(made)
    return made


@pytest.fixture
def crm():
    made = new_source("crm")
    return made


class TestWhatASourceDeclares:
    def test_it_satisfies_the_port_and_is_bound_to_one_client(self, source):
        assert isinstance(source, ProspectSource)
        assert source.client_id == CLIENT

    def test_it_always_owns_what_a_person_is_and_writes_only_what_it_owns(self, source):
        caps = source.capabilities
        assert SOURCE_ONLY_FIELDS <= caps.owned
        assert caps.writable <= caps.owned

    def test_a_crm_owns_little_and_a_spreadsheet_everything(self):
        crm_caps, sheet_caps = default_capabilities(), spreadsheet_capabilities()
        assert crm_caps.owned == SOURCE_ONLY_FIELDS | {ProspectField.STATUS}
        assert crm_caps.writable == {ProspectField.STATUS} and not crm_caps.can_create
        assert crm_caps.id_namespace == "fake_crm"
        assert sheet_caps.owned == set(ProspectField) and sheet_caps.can_create
        assert sheet_caps.id_namespace is None


class TestReading:
    def test_prospects_come_in_a_stable_order_with_their_native_ids(self, source):
        first = [p.prospect_id for p in source.list_prospects()]
        assert first == ["1001", "1002", "1003"]
        assert [p.prospect_id for p in source.list_prospects()] == first

    def test_one_prospect_by_native_id(self, source):
        found = source.get_prospect("1002")
        assert found is not None and found.display_name == "Ben Test"
        assert source.get_prospect("9999") is None

    def test_a_prospect_is_found_by_contact_ignoring_formatting_and_case(self, source):
        assert source.find_prospect_by_contact(phone="21622000001").prospect_id == "1001"
        assert source.find_prospect_by_contact(phone=" (216) 22-000-002").prospect_id == "1002"
        assert source.find_prospect_by_contact(email="CHLOE@Example.COM").prospect_id == "1003"

    def test_no_match_and_no_usable_criterion_find_nothing(self, source):
        assert source.find_prospect_by_contact(phone="21699999999") is None
        assert source.find_prospect_by_contact(email="nobody@example.com") is None
        assert source.find_prospect_by_contact() is None
        assert source.find_prospect_by_contact(phone="", email="  ") is None
        assert source.find_prospect_by_contact(phone="abc") is None

    def test_a_record_cannot_belong_to_another_client(self, source):
        with pytest.raises(ValueError):
            source.add_record(make_prospect(client_id=OTHER, prospect_id=ProspectId("1004")))

    def test_fields_the_source_does_not_own_come_back_empty(self, kind):
        busy = person(
            "1",
            "Ana Test",
            "+216 22 000 001",
            status=S.ACTIVE,
            stage="qualified",
            conversation_id=ConversationId("c-1"),
            schedule=Scheduling(
                next_action_type=SalesActionType.FOLLOW_UP,
                next_action_due_at=NOW,
                follow_up_count=3,
            ),
        )
        made = new_source(kind)
        made.add_record(busy)
        (got,) = made.list_prospects()
        if kind == "spreadsheet":
            assert got == busy
        else:
            assert got.stage is None and got.conversation_id is None
            assert got.schedule == Scheduling()
            assert got.status is S.ACTIVE  # the status is the one thing a CRM does own here
            assert got.display_name == "Ana Test" and got.contact == busy.contact

    def test_verify_reports_what_the_source_says_without_writing(self, source):
        assert source.verify() == []
        source.verify_issues.append("warning: a column is missing")
        assert source.verify() == ["warning: a column is missing"]
        assert not [r for r in source.requests if r.startswith("write:")]


class TestRecordsThatCannotBeUsed:
    def test_a_crm_leaves_out_what_it_cannot_interpret_and_says_so(self, crm):
        crm.add_record(person("1001", "Ana Test", "+216 22 000 001"), raw_status="lead")
        crm.add_record(person("1002", "Ben Test", "+216 22 000 002"), raw_status=None)
        crm.add_record(person("1003", "Cyd Test", "+216 22 000 003"), raw_status="mystery")
        crm.add_record(person("bad id", "Dan Test", "+216 22 000 004"), raw_status="lead")
        crm.add_record(person("1005", "Eve Test", "+216 22 000 005"), raw_status="")
        crm.add_unreadable()
        assert [p.prospect_id for p in crm.list_prospects()] == ["1001"]
        codes = sorted(p.code for p in crm.problems())
        assert codes == sorted(
            [
                ProblemCode.BLANK_STATUS,
                ProblemCode.UNMAPPED_STATUS,
                ProblemCode.INVALID_NATIVE_ID,
                ProblemCode.BLANK_STATUS,
                ProblemCode.UNREADABLE_RECORD,
            ]
        )

    def test_a_problem_never_repeats_a_name_a_number_or_an_unsafe_id(self, crm):
        crm.add_record(
            person("1002", "Ben Test", "+216 22 000 002", "ben@example.com"), raw_status=""
        )
        crm.add_record(person("bad id", "Dan Test", "+216 22 000 004"), raw_status="lead")
        crm.add_unreadable()
        crm.list_prospects()
        text = " ".join(f"{p.detail} {p.native_id}" for p in crm.problems())
        for private in ("Ben", "Dan", "Test", "216", "@", "bad id", "example"):
            assert private not in text
        unsafe = [p for p in crm.problems() if p.code is ProblemCode.INVALID_NATIVE_ID]
        assert unsafe and all(p.native_id is None for p in unsafe)

    def test_the_problems_are_the_same_on_every_read(self, crm):
        crm.add_record(person("1002", "Ben Test", "+216 22 000 002"), raw_status="mystery")
        assert crm.problems() == ()  # nothing has been read yet
        crm.list_prospects()
        first = crm.problems()
        crm.list_prospects()
        assert crm.problems() == first and len(first) == 1

    def test_a_refusal_keeps_a_usable_record_and_marks_it_opted_out(self, crm):
        crm.add_record(
            person("1001", "Ana Test", "+216 22 000 001"), raw_status="lead", do_not_contact=True
        )
        (kept,) = crm.list_prospects()
        assert kept.status is S.OPTED_OUT and crm.problems() == ()

    def test_a_refusal_does_not_bring_an_unusable_record_into_the_cycle(self, crm):
        crm.add_record(
            person("1001", "Ana Test", "+216 22 000 001"), raw_status="mystery", do_not_contact=True
        )
        crm.add_record(
            person("1002", "Ben Test", "+216 22 000 002"), raw_status=None, do_not_contact=True
        )
        assert crm.list_prospects() == []
        assert {p.code for p in crm.problems()} == {
            ProblemCode.UNMAPPED_STATUS,
            ProblemCode.BLANK_STATUS,
        }

    def test_a_spreadsheet_keeps_ids_as_they_are_whatever_they_look_like(self):
        sheet = new_source("spreadsheet")
        for pid in ("bad id", "a:b", "inbound-2026-01-15-21622000001"):
            sheet.add_record(person(pid, "Ana Test", "+216 22 000 001" if pid == "a:b" else None))
        assert [p.prospect_id for p in sheet.list_prospects()] == [
            "bad id",
            "a:b",
            "inbound-2026-01-15-21622000001",
        ]
        assert sheet.problems() == ()


class TestRefusedContacts:
    """A refusal carried by a record that is left out must not be lost."""

    def add(self, made, pid, name, phone, status, *, refuses, email=None):
        made.add_record(person(pid, name, phone, email), raw_status=status, do_not_contact=refuses)

    def test_the_port_declares_it(self, source):
        assert source.refused_contacts() == ()  # nothing has been read yet
        incomplete = type("NoRefusals", (type(source),), {"refused_contacts": None})
        assert isinstance(source, ProspectSource)
        assert not isinstance(incomplete(CLIENT), ProspectSource)

    @pytest.mark.parametrize("kind", ["crm", "spreadsheet"])
    @pytest.mark.parametrize(("status", "code"), [
        (None, ProblemCode.BLANK_STATUS),
        ("", ProblemCode.BLANK_STATUS),
        ("   ", ProblemCode.BLANK_STATUS),
        ("mystery", ProblemCode.UNMAPPED_STATUS),
    ])  # fmt: skip
    def test_an_unusable_record_that_refuses_is_left_out_but_its_contact_is_kept(
        self, kind, status, code
    ):
        made = new_source(kind)
        self.add(made, "1002", "Ben Test", "+216 22 000 002", status, refuses=True,
                 email="ben@example.com")  # fmt: skip
        assert made.list_prospects() == []  # still out of the commercial cycle
        assert [p.code for p in made.problems()] == [code]  # and the problem is still reported
        assert made.refused_contacts() == (
            ContactInfo(phone="+216 22 000 002", email="ben@example.com"),
        )

    def test_a_record_without_a_refusal_is_never_listed(self, crm):
        self.add(crm, "1001", "Ana Test", "+216 22 000 001", None, refuses=False)
        self.add(crm, "1002", "Ben Test", "+216 22 000 002", "mystery", refuses=False)
        crm.list_prospects()
        assert len(crm.problems()) == 2
        assert crm.refused_contacts() == ()

    def test_only_the_records_that_refuse_are_listed_among_those_left_out(self, crm):
        self.add(crm, "1001", "Ana Test", "+216 22 000 001", None, refuses=False)
        self.add(crm, "1002", "Ben Test", "+216 22 000 002", None, refuses=True)
        self.add(crm, "1003", "Cyd Test", "+216 22 000 003", "mystery", refuses=True)
        crm.list_prospects()
        assert crm.refused_contacts() == (
            ContactInfo(phone="+216 22 000 002"),
            ContactInfo(phone="+216 22 000 003"),
        )

    def test_a_record_that_is_kept_is_listed_as_opted_out_and_not_repeated_here(self, crm):
        self.add(crm, "1001", "Ana Test", "+216 22 000 001", "lead", refuses=True)
        (kept,) = crm.list_prospects()
        assert kept.status is S.OPTED_OUT and crm.problems() == ()
        assert crm.refused_contacts() == ()

    def test_the_result_holds_a_phone_and_an_address_and_nothing_else(self, crm):
        made = person(
            "1002", "Ben Test", "+216 22 000 002", "ben@example.com", locale="fr",
            attributes={"plan": "gold"},
        )  # fmt: skip
        crm.add_record(made, raw_status=None, do_not_contact=True)
        crm.list_prospects()
        (contact,) = crm.refused_contacts()
        assert contact == ContactInfo(phone="+216 22 000 002", email="ben@example.com")
        assert contact.preferred_channel is None  # even the channel is not carried over
        text = repr(crm.refused_contacts())
        for private in ("Ben", "Test", "gold", "fr", "1002"):
            assert private not in text.replace("preferred_channel", "")

    def test_a_refusal_never_shows_up_in_the_problems(self, crm):
        self.add(crm, "1002", "Ben Test", "+216 22 000 002", None, refuses=True,
                 email="ben@example.com")  # fmt: skip
        crm.list_prospects()
        text = " ".join(f"{p.code} {p.detail} {p.native_id}" for p in crm.problems())
        for contact in ("216", "ben@example.com", "example", "Ben"):
            assert contact not in text

    def test_a_refusal_without_a_usable_contact_leaves_nothing_to_record(self, crm):
        self.add(crm, "1002", "Ben Test", None, None, refuses=True)
        self.add(crm, "1003", "Cyd Test", "abc", "mystery", refuses=True)
        crm.list_prospects()
        assert len(crm.problems()) == 2 and crm.refused_contacts() == ()

    def test_a_record_whose_id_is_unusable_still_has_its_refusal_kept(self, crm):
        self.add(crm, "bad id", "Dan Test", "+216 22 000 004", "lead", refuses=True)
        assert crm.list_prospects() == []
        assert [p.code for p in crm.problems()] == [ProblemCode.INVALID_NATIVE_ID]
        assert crm.refused_contacts() == (ContactInfo(phone="+216 22 000 004"),)

    def test_the_same_contact_is_listed_once_in_a_stable_order(self, crm):
        self.add(crm, "1003", "Cyd Test", "+216 22 000 003", None, refuses=True)
        self.add(crm, "1002", "Ben Test", "+216 22 000 002", None, refuses=True)
        self.add(crm, "1004", "Ben Again", "216 22 000 002", "mystery", refuses=True)
        crm.list_prospects()
        first = crm.refused_contacts()
        assert [c.phone for c in first] == ["+216 22 000 003", "+216 22 000 002"]
        crm.list_prospects()
        assert crm.refused_contacts() == first

    def test_it_follows_the_latest_read_and_makes_no_request_of_its_own(self, crm):
        self.add(crm, "1002", "Ben Test", "+216 22 000 002", None, refuses=True)
        crm.list_prospects()
        assert len(crm.refused_contacts()) == 1
        crm.human_edit(replace(crm.records["1002"], contact=ContactInfo(phone="+216 22 000 009")))
        assert len(crm.refused_contacts()) == 1  # nothing was read since: still the old answer
        crm.list_prospects()
        assert crm.refused_contacts() == (ContactInfo(phone="+216 22 000 009"),)
        crm.refused_contacts()
        assert crm.requests == ("read:list_prospects", "read:list_prospects")

    def test_a_read_that_fails_does_not_change_the_answer(self, crm):
        self.add(crm, "1002", "Ben Test", "+216 22 000 002", None, refuses=True)
        crm.list_prospects()
        crm.fail_next("list_prospects")
        with pytest.raises(SourceUnavailableError):
            crm.list_prospects()
        assert crm.refused_contacts() == (ContactInfo(phone="+216 22 000 002"),)


SOURCE_CALLS = {
    "list_prospects": lambda s: s.list_prospects(),
    "get_prospect": lambda s: s.get_prospect("1001"),
    "find_prospect_by_contact": lambda s: s.find_prospect_by_contact(phone="21622000001"),
    "verify": lambda s: s.verify(),
}


class TestSourceErrors:
    @pytest.mark.parametrize("call", sorted(SOURCE_CALLS))
    @pytest.mark.parametrize(
        "error",
        [
            SourceUnavailableError("down"),
            SourceRateLimitedError(retry_after=3),
            SourceAuthError("no"),
        ],
        ids=["unavailable", "rate-limited", "auth"],
    )
    def test_an_error_reaches_the_caller_unchanged(self, source, call, error):
        source.fail_next(call, error)
        with pytest.raises(type(error)) as raised:
            SOURCE_CALLS[call](source)
        assert raised.value is error

    def test_a_failed_read_is_an_error_never_a_partial_list(self, source):
        source.fail_next("list_prospects")
        with pytest.raises(SourceUnavailableError):
            source.list_prospects()
        assert [p.prospect_id for p in source.list_prospects()] == ["1001", "1002", "1003"]

    def test_a_rate_limit_may_say_when_to_come_back(self, source):
        source.fail_next("list_prospects", SourceRateLimitedError(retry_after=30))
        with pytest.raises(SourceRateLimitedError) as raised:
            source.list_prospects()
        assert raised.value.retry_after == 30


class TestReadOnlyByDefault:
    def test_a_source_refuses_every_write_without_sending_any_request(self, source):
        before = dict(source.records)
        with pytest.raises(SourceReadOnlyError):
            source.update_prospects([status_change()])
        with pytest.raises(SourceReadOnlyError):
            source.create_prospects([person("2001", "New Person", "+216 22 000 009")])
        assert source.requests == ()
        assert source.records == before

    def test_reads_are_requests_and_still_no_write_is_ever_made(self, source):
        source.list_prospects()
        source.get_prospect("1001")
        source.find_prospect_by_contact(phone="21622000001")
        with pytest.raises(SourceReadOnlyError):
            source.update_prospects([status_change()])
        assert source.requests == (
            "read:list_prospects",
            "read:get_prospect",
            "read:find_prospect_by_contact",
        )


class TestWriting:
    def test_an_allowed_change_is_written(self, writable):
        writable.update_prospects([status_change()])
        assert writable.records["1001"].status is S.PAUSED
        assert writable.get_prospect("1001").status is S.PAUSED
        assert writable.requests == ("write:update_prospects", "read:get_prospect")

    def test_only_the_fields_named_are_touched(self, writable):
        before = writable.records["1001"]
        writable.update_prospects([status_change()])
        assert writable.records["1001"] == replace(before, status=S.PAUSED)

    def test_a_field_the_source_cannot_write_is_refused_and_nothing_changes(self):
        crm = new_source("crm", allow_writes=True)
        crm.add_record(person("1001", "Ana Test", "+216 22 000 001"))
        change = ProspectChange(
            prospect_id="1001",
            new={ProspectField.STAGE: "qualified"},
            expected={ProspectField.STAGE: None},
        )
        with pytest.raises(SourceReadOnlyError):
            crm.update_prospects([change])
        assert crm.records["1001"].stage is None

    def test_a_value_that_changed_in_the_meantime_is_a_conflict_and_nothing_is_written(
        self, writable
    ):
        stale = status_change(expected=S.CLOSED)  # the source holds NEW, not CLOSED
        with pytest.raises(SourceConflictError) as raised:
            writable.update_prospects([stale])
        assert raised.value.prospect_id == "1001" and raised.value.fields == ("status",)
        assert "closed" not in str(raised.value).lower()
        assert writable.records["1001"].status is S.NEW

    def test_a_person_edit_between_the_read_and_the_write_is_never_overwritten(self, writable):
        read_status = writable.get_prospect("1001").status
        writable.human_edit(replace(writable.records["1001"], status=S.OPTED_OUT))
        with pytest.raises(SourceConflictError):
            writable.update_prospects([status_change(expected=read_status, new=S.ACTIVE)])
        assert writable.records["1001"].status is S.OPTED_OUT

    def test_an_unknown_prospect_is_a_key_error(self, writable):
        with pytest.raises(KeyError):
            writable.update_prospects([status_change(pid="9999")])

    def test_a_batch_is_all_or_nothing(self, writable):
        batch = [status_change("1001"), status_change("1002", expected=S.CLOSED)]
        with pytest.raises(SourceConflictError):
            writable.update_prospects(batch)
        assert writable.records["1001"].status is S.NEW

    def test_the_same_prospect_twice_in_one_call_is_refused(self, writable):
        with pytest.raises(ValueError):
            writable.update_prospects([status_change("1001"), status_change("1001")])
        assert writable.records["1001"].status is S.NEW

    def test_a_source_that_cannot_create_refuses_even_when_writes_are_allowed(self):
        crm = new_source("crm", allow_writes=True)
        with pytest.raises(SourceReadOnlyError):
            crm.create_prospects([person("2001", "New Person", "+216 22 000 009")])
        assert crm.records == {}

    def test_a_source_that_can_create_does(self):
        sheet = new_source("spreadsheet", allow_writes=True)
        sheet.create_prospects([person("2001", "New Person", "+216 22 000 009")])
        assert sheet.get_prospect("2001").display_name == "New Person"

    def test_creating_is_all_or_nothing_and_refuses_duplicates_and_other_clients(self):
        sheet = new_source("spreadsheet", allow_writes=True)
        seed(sheet)
        with pytest.raises(ValueError):
            sheet.create_prospects([person("2001", "Fine"), person("1001", "Duplicate")])
        with pytest.raises(ValueError):
            sheet.create_prospects([make_prospect(client_id=OTHER, prospect_id=ProspectId("2002"))])
        assert sorted(sheet.records) == ["1001", "1002", "1003"]


# ---- OperationalStore ----------------------------------------------------------------------------


@pytest.fixture
def store():
    return FakeOperationalStore(CLIENT)


def act(aid, key=None, **kw):
    return make_action(action_id=ActionId(aid), idempotency_key=key or f"{CLIENT}:p-1:{aid}", **kw)


def msg(mid, **kw):
    return make_message(message_id=MessageId(mid), **kw)


def fact(key="note", value="v", **kw):
    fields = {"status": FactStatus.DISCOVERED, "recorded_at": NOW} | kw
    return Fact(key=key, value=value, **fields)


def local(pid="inbound-2026-01-15-21622000001", phone="+216 22 000 001", **kw):
    return person(pid, phone, phone, **({"status": S.ACTIVE} | kw))


class TestStoreDeclaration:
    def test_it_satisfies_the_port_and_the_facts_ports(self, store):
        assert isinstance(store, OperationalStore)
        assert isinstance(store, FactsReader) and isinstance(store, FactsWriter)
        assert store.client_id == CLIENT

    def test_it_has_no_prospect_listing_of_a_source(self, store):
        assert not hasattr(store, "list_prospects") and not hasattr(store, "update_prospects")


class TestStoreActions:
    def test_actions_are_saved_listed_and_found_by_key(self, store):
        store.save_actions([act("a1"), act("a2", status=ActionStatus.APPROVED)])
        assert [a.action_id for a in store.list_actions()] == ["a1", "a2"]
        assert [a.action_id for a in store.list_actions({ActionStatus.APPROVED})] == ["a2"]
        assert store.find_action_by_idempotency_key(f"{CLIENT}:p-1:a2").action_id == "a2"
        assert store.find_action_by_idempotency_key("missing") is None

    def test_saving_again_updates_by_action_id(self, store):
        store.save_actions([act("a1")])
        store.save_actions([act("a1", status=ActionStatus.APPROVED)])
        (only,) = store.list_actions()
        assert only.status is ActionStatus.APPROVED

    def test_a_shared_idempotency_key_is_refused_and_nothing_is_written(self, store):
        store.save_actions([act("a1", key="same")])
        with pytest.raises(ValueError):
            store.save_actions([act("a2", key="other"), act("a3", key="same")])
        assert [a.action_id for a in store.list_actions()] == ["a1"]

    def test_an_action_of_another_client_is_refused_and_nothing_is_written(self, store):
        with pytest.raises(ValueError):
            store.save_actions([act("a1"), act("a2", client_id=OTHER)])
        assert store.list_actions() == []


class TestStoreHistory:
    def test_messages_are_appended_and_read_back_per_prospect(self, store):
        store.append_messages([msg("m1"), msg("m2", text="Again")])
        conversation = store.get_conversation(ProspectId("p-1"))
        assert [m.message_id for m in conversation.messages] == ["m1", "m2"]
        assert conversation.conversation_id == "c-1"

    def test_a_prospect_with_no_history_has_an_empty_conversation(self, store):
        conversation = store.get_conversation(ProspectId("p-9"))
        assert conversation.messages == () and conversation.conversation_id == "conv-p-9"

    def test_a_message_id_is_never_stored_twice_and_nothing_is_written_on_refusal(self, store):
        store.append_messages([msg("m1")])
        with pytest.raises(ValueError):
            store.append_messages([msg("m2"), msg("m1")])
        assert [m.message_id for m in store.messages] == ["m1"]

    def test_a_prospect_keeps_one_conversation(self, store):
        store.append_messages([msg("m1")])
        with pytest.raises(ValueError):
            store.append_messages([msg("m2", conversation_id=ConversationId("c-2"))])

    def test_a_message_of_another_client_is_refused(self, store):
        with pytest.raises(ValueError):
            store.append_messages([msg("m1", client_id=OTHER)])
        assert store.messages == ()

    def test_the_audit_trail_is_appended_and_isolated(self, store):
        event = AuditEvent(timestamp=NOW, client_id=CLIENT, event_type="action.sent", actor="core")
        store.append_audit_events([event])
        assert store.audit_events == (event,)
        with pytest.raises(ValueError):
            store.append_audit_events([replace(event, client_id=OTHER)])
        assert store.audit_events == (event,)


class TestStoreFacts:
    P1, P2 = ProspectId("p1"), ProspectId("p2")

    def test_a_fact_is_recorded_once_however_often_it_is_sent(self, store):
        store.append_facts(self.P1, [fact("k", "v")])
        store.append_facts(self.P1, [fact("k", "v"), fact("k", "v")])
        assert [f.key for f in store.get_facts(self.P1)] == ["k"]

    def test_facts_are_per_prospect_and_oldest_first(self, store):
        store.append_facts(self.P1, [fact("late", recorded_at=NOW + timedelta(hours=1))])
        store.append_facts(self.P1, [fact("early")])
        store.append_facts(self.P2, [fact("other")])
        assert [f.key for f in store.get_facts(self.P1)] == ["early", "late"]
        assert [f.key for f in store.get_facts(self.P2)] == ["other"]
        assert store.get_facts(ProspectId("p3")) == []

    def test_a_fact_without_a_time_is_refused_and_the_call_writes_nothing(self, store):
        with pytest.raises(ValueError, match="recorded_at"):
            store.append_facts(self.P1, [fact("ok"), replace(fact("bad"), recorded_at=None)])
        assert store.get_facts(self.P1) == []

    def test_facts_are_accepted_for_a_prospect_the_store_knows_nothing_else_about(self, store):
        # The `OperationalStore` port says so explicitly: the store records facts for any id and
        # never claims to have checked the prospect exists. The code that composes a source and a
        # store must check existence (source or local prospects) and raise `KeyError` itself
        # BEFORE writing; this test must be matched by one on the composite when it exists.
        store.append_facts(ProspectId("fake_crm:1001"), [fact("k")])
        assert len(store.get_facts(ProspectId("fake_crm:1001"))) == 1
        assert store.get_states([ProspectId("fake_crm:1001")]) == {}  # nothing else is created


class TestStates:
    ID = ProspectId("fake_crm:1001")

    def st(self, **kw):
        return ProspectState(client_id=CLIENT, prospect_id=kw.pop("prospect_id", self.ID), **kw)

    def test_a_prospect_with_no_state_is_absent(self, store):
        assert store.get_states([self.ID]) == {} and store.list_states() == {}

    def test_a_new_state_is_saved_at_revision_zero_and_stored_one_ahead(self, store):
        store.save_states([self.st(status=S.ACTIVE, stage="s1")])
        saved = store.get_states([self.ID])[self.ID]
        assert saved.revision == 1 and saved.status is S.ACTIVE and saved.stage == "s1"

    def test_a_state_is_updated_from_the_revision_that_was_read(self, store):
        store.save_states([self.st(status=S.ACTIVE)])
        read = store.get_states([self.ID])[self.ID]
        store.save_states([replace(read, stage="s2")])
        again = store.get_states([self.ID])[self.ID]
        assert again.revision == 2 and again.stage == "s2" and again.status is S.ACTIVE

    def test_a_stale_revision_is_a_conflict_and_nothing_is_written(self, store):
        store.save_states([self.st(status=S.ACTIVE)])
        stale = self.st(status=S.PAUSED)  # revision 0, but one is already stored
        fresh = self.st(prospect_id=ProspectId("fake_crm:1002"), status=S.ACTIVE)
        with pytest.raises(StateConflictError) as raised:
            store.save_states([fresh, stale])
        assert raised.value.prospect_id == self.ID
        assert set(store.list_states()) == {self.ID}
        assert store.get_states([self.ID])[self.ID].status is S.ACTIVE

    def test_a_new_state_cannot_claim_a_later_revision(self, store):
        with pytest.raises(StateConflictError):
            store.save_states([self.st(revision=3)])
        assert store.list_states() == {}

    def test_two_writers_with_the_same_read_cannot_both_win(self, store):
        store.save_states([self.st(status=S.ACTIVE)])
        read = store.get_states([self.ID])[self.ID]
        store.save_states([replace(read, stage="mine")])
        with pytest.raises(StateConflictError):
            store.save_states([replace(read, stage="theirs")])
        assert store.get_states([self.ID])[self.ID].stage == "mine"

    def test_the_same_prospect_twice_in_one_call_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save_states([self.st(), self.st()])
        assert store.list_states() == {}

    def test_a_state_of_another_client_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save_states([replace(self.st(), client_id=OTHER)])
        assert store.list_states() == {}

    def test_only_the_asked_states_are_returned(self, store):
        other = ProspectId("fake_crm:1002")
        store.save_states([self.st(), self.st(prospect_id=other)])
        assert set(store.get_states([other, ProspectId("nope")])) == {other}
        assert set(store.list_states()) == {self.ID, other}


class TestLocalProspects:
    def test_a_local_prospect_is_created_listed_and_read_back(self, store):
        created = local()
        store.create_local_prospects([created])
        assert store.get_local_prospect(created.prospect_id) == created
        assert store.list_local_prospects() == [created]
        assert store.get_local_prospect(ProspectId("inbound-2026-01-15-nope")) is None

    def test_it_is_found_by_contact_ignoring_formatting(self, store):
        store.create_local_prospects([local(email="x@example.com")])
        assert store.find_local_by_contact(phone="21622000001") is not None
        assert store.find_local_by_contact(email="X@EXAMPLE.COM") is not None
        assert store.find_local_by_contact(phone="21699999999") is None
        assert store.find_local_by_contact() is None

    def test_an_id_or_a_contact_already_in_use_is_refused_and_nothing_is_written(self, store):
        store.create_local_prospects([local()])
        with pytest.raises(ValueError):
            store.create_local_prospects(
                [local("inbound-2026-01-15-2", "+216 22 000 002"), local()]
            )
        with pytest.raises(ValueError):  # same contact, another id
            store.create_local_prospects([local("inbound-2026-01-16-21622000001")])
        assert len(store.list_local_prospects()) == 1

    def test_the_same_id_twice_in_one_call_is_refused(self, store):
        with pytest.raises(ValueError):
            store.create_local_prospects([local(), local(phone="+216 22 000 002")])
        assert store.list_local_prospects() == []

    @pytest.mark.parametrize(
        "bad_id",
        ["p-1", "inbound-2026-01-15-has space", "inbound-2026-01-15-a:b", "inbound-"],
    )
    def test_an_id_the_store_cannot_hold_safely_is_refused(self, store, bad_id):
        with pytest.raises(ValueError):
            store.create_local_prospects([local(bad_id)])
        assert store.list_local_prospects() == []

    def test_a_prospect_of_another_client_is_refused(self, store):
        with pytest.raises(ValueError):
            store.create_local_prospects([replace(local(), client_id=OTHER)])

    def test_a_local_prospect_has_a_state_from_the_start(self, store):
        created = local(stage="opening")
        store.create_local_prospects([created])
        state = store.get_states([created.prospect_id])[created.prospect_id]
        assert state.revision == 0 and state.status is S.ACTIVE and state.stage == "opening"

    def test_the_initial_state_is_taken_from_the_prospect_itself_at_revision_zero(self, store):
        schedule = Scheduling(
            next_action_type=SalesActionType.FOLLOW_UP,
            next_action_due_at=NOW + timedelta(days=1),
            last_inbound_at=NOW,
            follow_up_count=1,
        )
        first = local(
            "inbound-2026-01-15-21622000001",
            "+216 22 000 001",
            stage="opening",
            schedule=schedule,
            conversation_id=ConversationId("c-1"),
        )
        second = local("inbound-2026-01-15-21622000002", "+216 22 000 002", status=S.PAUSED)
        store.create_local_prospects([first, second])
        states = store.get_states([first.prospect_id, second.prospect_id])
        assert set(states) == {first.prospect_id, second.prospect_id}
        one, two = states[first.prospect_id], states[second.prospect_id]
        assert (one.client_id, one.status, one.stage) == (CLIENT, S.ACTIVE, "opening")
        assert one.schedule == schedule and one.conversation_id == "c-1"
        assert two.status is S.PAUSED and two.schedule == Scheduling()
        assert one.revision == two.revision == 0
        assert set(store.list_states()) == set(states)

    def test_the_first_save_of_that_state_carries_revision_zero_and_stores_one(self, store):
        created = local()
        store.create_local_prospects([created])
        initial = store.get_states([created.prospect_id])[created.prospect_id]
        store.save_states([replace(initial, stage="next")])
        saved = store.get_states([created.prospect_id])[created.prospect_id]
        assert saved.revision == 1 and saved.stage == "next"
        with pytest.raises(StateConflictError):  # the revision-0 read is now stale
            store.save_states([replace(initial, stage="late")])

    def test_a_save_cannot_claim_a_revision_the_initial_state_never_had(self, store):
        created = local()
        store.create_local_prospects([created])
        initial = store.get_states([created.prospect_id])[created.prospect_id]
        with pytest.raises(StateConflictError):
            store.save_states([replace(initial, revision=1)])
        assert store.get_states([created.prospect_id])[created.prospect_id].revision == 0

    def test_a_refused_creation_leaves_neither_prospects_nor_states(self, store):
        good = local()
        bad = local("p-1", "+216 22 000 009")  # not an id the store can hold
        with pytest.raises(ValueError):
            store.create_local_prospects([good, bad])
        assert store.list_local_prospects() == [] and store.list_states() == {}
        store.create_local_prospects([good])
        with pytest.raises(ValueError):  # the second one repeats an id
            store.create_local_prospects([local("inbound-2026-01-15-9", "+216 22 000 009"), good])
        assert set(store.list_states()) == {good.prospect_id}

    def test_a_source_prospect_has_no_state_until_one_is_saved(self, store):
        assert store.get_states([ProspectId("fake_crm:1001")]) == {}

    def test_its_state_moves_on_through_save_states_and_its_identity_does_not(self, store):
        created = local()
        store.create_local_prospects([created])
        read = store.get_states([created.prospect_id])[created.prospect_id]
        store.save_states([replace(read, status=S.PAUSED, stage="waiting")])
        seen = store.get_local_prospect(created.prospect_id)
        assert seen.status is S.PAUSED and seen.stage == "waiting"
        assert seen.display_name == created.display_name and seen.contact == created.contact
        assert contact_keys(seen.contact) == contact_keys(created.contact)

    def test_its_conversation_follows_its_state(self, store):
        created = local(conversation_id=ConversationId("c-local"))
        store.create_local_prospects([created])
        assert store.get_conversation(created.prospect_id).conversation_id == "c-local"


class TestStoreErrorsAndIsolation:
    def test_an_injected_failure_reaches_the_caller_once(self, store):
        store.fail_next("save_actions")
        with pytest.raises(StoreUnavailableError):
            store.save_actions([act("a1")])
        store.save_actions([act("a1")])
        assert len(store.list_actions()) == 1

    def test_a_failed_state_save_writes_nothing(self, store):
        store.fail_next("save_states")
        with pytest.raises(StoreUnavailableError):
            store.save_states([ProspectState(client_id=CLIENT, prospect_id=ProspectId("x"))])
        assert store.list_states() == {}

    def test_two_clients_never_see_each_other(self):
        mine, theirs = FakeOperationalStore(CLIENT), FakeOperationalStore(OTHER)
        mine.save_actions([act("a1")])
        mine.save_states([ProspectState(client_id=CLIENT, prospect_id=ProspectId("x"))])
        mine.create_local_prospects([local()])
        assert theirs.list_actions() == [] and theirs.list_states() == {}
        assert theirs.list_local_prospects() == []
        with pytest.raises(ValueError):
            theirs.save_actions([act("a1")])  # an action of client-a is refused by client-b
        assert theirs.verify() == [] and theirs.problems() == ()


# ---- the two together -----------------------------------------------------------------------------


class TestASourceAndAStoreMeet:
    def test_a_source_record_and_its_stored_state_merge_into_one_prospect(self):
        crm = new_source("crm")
        crm.add_record(person("1001", "Ana Test", "+216 22 000 001"), raw_status="lead")
        store = FakeOperationalStore(CLIENT)
        public = ProspectId("fake_crm:1001")
        store.save_states(
            [
                ProspectState(
                    client_id=CLIENT,
                    prospect_id=public,
                    status=S.ACTIVE,
                    stage="qualified",
                    conversation_id=ConversationId("c-5"),
                )
            ]
        )
        (record,) = crm.list_prospects()
        merged = merge_prospect(
            record,
            store.get_states([public]).get(public),
            owned=crm.capabilities.owned,
            public_id=public,
        )
        assert merged.prospect_id == public and merged.display_name == "Ana Test"
        assert merged.status is S.ACTIVE and merged.stage == "qualified"
        assert merged.conversation_id == "c-5"
        assert crm.requests == ("read:list_prospects",)  # the merge wrote nothing to the source

    def test_the_sources_refusal_wins_over_what_revansa_stored(self):
        crm = new_source("crm")
        crm.add_record(person("1001", "Ana Test", "+216 22 000 001"), raw_status="dnc")
        store = FakeOperationalStore(CLIENT)
        public = ProspectId("fake_crm:1001")
        store.save_states([ProspectState(client_id=CLIENT, prospect_id=public, status=S.ACTIVE)])
        (record,) = crm.list_prospects()
        merged = merge_prospect(
            record,
            store.get_states([public])[public],
            owned=crm.capabilities.owned,
            public_id=public,
        )
        assert merged.status is S.OPTED_OUT
