"""The pure rules of `sales_app.sourcing`: contacts, identifiers, fields, statuses, merging.

No provider, no Engine, no store: only functions and data. They are the rules every source, the
store and the code that composes them must agree on.
"""

import dataclasses
import itertools
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

import pytest
from factories import CLIENT, NAIVE_NOW, NOW, make_prospect

from sales_app.sourcing import (
    CONTACTABLE,
    LOCAL_ID_PREFIX,
    MAPPABLE,
    OVERLAY_FIELDS,
    SOURCE_ONLY_FIELDS,
    InvalidNativeIdError,
    ProblemCode,
    ProspectChange,
    ProspectField,
    ProspectState,
    SourceAuthError,
    SourceCapabilities,
    SourceConflictError,
    SourceDataError,
    SourceError,
    SourceProblem,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
    StateConflictError,
    StatusMapping,
    StatusMappingError,
    StoreError,
    StoreUnavailableError,
    changed_fields,
    contact_keys,
    effective_status,
    field_value,
    is_local_id,
    is_restrictive,
    local_id_problem,
    lookup_keys,
    lowers_restriction,
    merge_prospect,
    native_id_problem,
    normalize_email,
    normalize_phone,
    resolve_source_status,
    same_contact,
    severity,
    to_native_id,
    to_public_id,
)
from sales_app.sourcing.identifiers import check_namespace
from sales_core.contracts import (
    Channel,
    ContactInfo,
    ConversationId,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)
from sales_integrations.google_sheets import codecs

S = ProspectStatus
ALL_STATUSES = list(ProspectStatus)
OPTIONAL_STATUSES = [None, *ALL_STATUSES]
PAIRS = list(itertools.product(OPTIONAL_STATUSES, OPTIONAL_STATUSES))
STATUS_PAIRS = list(itertools.product(ALL_STATUSES, repeat=2))
FREE_PAIRS = [
    (a, b)
    for a, b in PAIRS
    if not any(s is not None and s not in (S.NEW, S.ACTIVE) for s in (a, b))
]


# ---- contacts ------------------------------------------------------------------------------


PHONES = [
    "+216 22 768 307",
    "21622768307",
    "(216) 22-768-307",
    "+1 (555) 010-9999 ext. 4",
    " 0033 6 12 34 56 78 ",
    "",
    "abc",
    "٢١٦٢٢",  # Arabic-Indic digits: `str.isdigit` accepts them, so the adapter does too
    "²³",  # superscripts: `isdigit` is true, `isdecimal` is not
]


class TestContacts:
    @pytest.mark.parametrize("value", PHONES)
    def test_phone_normalisation_is_the_sheets_adapters_own(self, value):
        assert normalize_phone(value) == codecs.digits(value)

    def test_no_phone_has_no_digits(self):
        assert normalize_phone(None) == "" and normalize_phone("") == ""

    def test_phones_that_differ_only_in_formatting_are_equal(self):
        assert normalize_phone("+216 22 768 307") == normalize_phone("21622768307")

    def test_an_email_is_compared_ignoring_case_and_blanks(self):
        assert normalize_email("  Alex@Example.COM ") == "alex@example.com"
        assert normalize_email(None) == "" and normalize_email("   ") == ""

    def test_the_keys_of_a_contact(self):
        keys = contact_keys(ContactInfo(phone="+216 22", email=" A@B.c "))
        assert keys == {"phone:21622", "email:a@b.c"}

    def test_a_contact_with_nothing_usable_has_no_key(self):
        assert contact_keys(ContactInfo()) == frozenset()
        assert contact_keys(ContactInfo(phone="abc", email=" ")) == frozenset()

    def test_a_lookup_asks_for_the_same_keys_a_contact_carries(self):
        assert lookup_keys(phone="21622", email="A@B.C") == contact_keys(
            ContactInfo(phone="+216 22", email="a@b.c")
        )
        assert lookup_keys() == frozenset()

    def test_two_contacts_are_the_same_if_any_key_meets(self):
        first = ContactInfo(phone="+216 22 000 001", email="one@example.com")
        assert same_contact(first, ContactInfo(phone="21622000001"))
        assert same_contact(first, ContactInfo(email="ONE@example.com"))
        assert not same_contact(first, ContactInfo(phone="21622000002"))

    def test_two_contacts_with_no_key_are_never_the_same(self):
        assert not same_contact(ContactInfo(), ContactInfo())


# ---- identifiers -------------------------------------------------------------------------------


class TestIdentifiers:
    @pytest.mark.parametrize("value", ["123", "rec_AbC.9-x", "a", "a" * 128, "0"])
    def test_native_ids_that_are_accepted(self, value):
        assert native_id_problem(value) is None

    @pytest.mark.parametrize(
        "value",
        ["", "a:b", "a b", "é", "a/b", "a" * 129, " a", "a\t", "a;b", 12, None],
    )
    def test_native_ids_that_are_refused(self, value):
        assert native_id_problem(value) is not None

    @pytest.mark.parametrize(
        "value",
        ["x\n", "x\r\n", "x\n\n", "\nx", "x\ny", "x\r", "x\x00", "x ", "\n"],
    )
    def test_a_line_break_or_control_character_is_never_part_of_an_id(self, value):
        assert native_id_problem(value) is not None

    @pytest.mark.parametrize("value", ["123", "rec_AbC.9-x", "a", "a" * 128, "0"])
    @pytest.mark.parametrize("extra", ["\n", "\r\n", " ", "\t", "\x00"])
    def test_a_valid_native_id_stops_being_valid_with_anything_around_it(self, value, extra):
        assert native_id_problem(value) is None
        assert native_id_problem(value + extra) is not None
        assert native_id_problem(extra + value) is not None

    def test_a_namespaced_id_round_trips(self):
        public = to_public_id("fake_crm", "1001")
        assert public == "fake_crm:1001"
        assert to_native_id("fake_crm", public) == "1001"

    def test_a_namespaced_id_has_exactly_one_separator_and_a_local_id_none(self):
        assert to_public_id("fake_crm", "a.b-c_9").count(":") == 1
        local_id = "inbound-2026-01-15-21622768307"
        assert is_local_id(local_id) and ":" not in local_id

    def test_an_id_without_a_namespace_is_returned_untouched_and_unchecked(self):
        # What a spreadsheet already holds must keep working, whatever it looks like.
        for existing in ["p-1", "a:b", "has space", "inbound-2026-01-15-216", "é", "x" * 400]:
            assert to_public_id(None, existing) == existing
            assert to_native_id(None, existing) == existing

    def test_an_invalid_native_id_is_refused_without_repeating_it(self):
        with pytest.raises(InvalidNativeIdError) as raised:
            to_public_id("fake_crm", "secret id!")
        assert "secret id!" not in str(raised.value)
        assert isinstance(raised.value, SourceDataError)

    @pytest.mark.parametrize("native", ["1001\n", "1001\r\n", "\n1001"])
    def test_a_native_id_with_a_line_break_is_refused_in_both_directions(self, native):
        with pytest.raises(InvalidNativeIdError):
            to_public_id("fake_crm", native)
        with pytest.raises(ValueError):
            to_native_id("fake_crm", f"fake_crm:{native}")

    def test_an_id_of_another_source_or_a_local_one_has_no_native_form_here(self):
        for public in [
            "other_crm:1001",
            "inbound-2026-01-15-216",
            "1001",
            "fake_crm:",
            "fake_crm:a:b",
        ]:
            with pytest.raises(ValueError):
                to_native_id("fake_crm", public)

    @pytest.mark.parametrize(
        "namespace",
        ["Bad", "1abc", "a", "has space", "x" * 33, "a-b", ""],
    )
    def test_a_namespace_must_look_like_a_provider_key(self, namespace):
        with pytest.raises(ValueError):
            check_namespace(namespace)
        with pytest.raises(ValueError):
            to_public_id(namespace, "1")

    @pytest.mark.parametrize("namespace", ["fake_crm\n", "\nfake_crm", "ab\r\n", "fake_crm\x00"])
    def test_a_namespace_with_a_line_break_is_refused(self, namespace):
        with pytest.raises(ValueError):
            check_namespace(namespace)
        with pytest.raises(ValueError):
            to_public_id(namespace, "1")

    def test_a_namespace_may_be_absent(self):
        assert check_namespace(None) is None

    def test_what_a_local_id_looks_like(self):
        assert LOCAL_ID_PREFIX == "inbound-"
        assert is_local_id("inbound-2026-01-15-21622768307")
        assert not is_local_id("fake_crm:1001") and not is_local_id("p-1")

    @pytest.mark.parametrize(
        "value",
        [
            "inbound-2026-01-15-21622768307",
            "inbound-2026-01-15-a@b.example",
            "inbound-2026-01-15-+1",
        ],
    )
    def test_local_ids_the_store_can_hold(self, value):
        assert local_id_problem(value) is None

    @pytest.mark.parametrize(
        "value",
        [
            "p-1",
            "inbound-2026-01-15-has space",
            "inbound-2026-01-15-a:b",
            "inbound-2026-01-15-x/y",
            "inbound-" + "x" * 161,
            "inbound-",
            "inbound-2026-01-15-x\n",
            "inbound-2026-01-15-x\r\n",
            "inbound-2026-01-15-x\n\n",
            "inbound-2026-01-15-\nx",
            "\ninbound-2026-01-15-x",
            "inbound-2026-01-15-x\x00",
        ],
    )
    def test_local_ids_the_store_refuses(self, value):
        problem = local_id_problem(value)
        assert problem is not None and value not in problem

    @pytest.mark.parametrize("extra", ["\n", "\r\n", " ", "\t"])
    def test_a_valid_local_id_stops_being_valid_with_anything_around_it(self, extra):
        valid = "inbound-2026-01-15-21622768307"
        assert local_id_problem(valid) is None
        assert local_id_problem(valid + extra) is not None
        assert local_id_problem(extra + valid) is not None


# ---- fields ----------------------------------------------------------------------------------


class TestFields:
    def test_every_field_has_exactly_one_home(self):
        assert SOURCE_ONLY_FIELDS | OVERLAY_FIELDS == set(ProspectField)
        assert not SOURCE_ONLY_FIELDS & OVERLAY_FIELDS
        assert ProspectField.STATUS in OVERLAY_FIELDS

    def test_identical_prospects_have_no_changed_field(self):
        p = make_prospect()
        assert changed_fields(p, replace(p)) == frozenset()

    @pytest.mark.parametrize(
        ("field", "change"),
        [
            (ProspectField.DISPLAY_NAME, {"display_name": "Someone Else"}),
            (ProspectField.LOCALE, {"locale": "fr"}),
            (ProspectField.STATUS, {"status": S.PAUSED}),
            (ProspectField.STAGE, {"stage": "qualified"}),
            (ProspectField.CONVERSATION_ID, {"conversation_id": ConversationId("c-9")}),
        ],
    )
    def test_one_changed_field_is_reported_alone(self, field, change):
        before = make_prospect()
        assert changed_fields(before, replace(before, **change)) == {field}

    def test_contact_fields_are_reported_one_by_one(self):
        before = make_prospect()
        contact = before.contact
        phone = replace(before, contact=replace(contact, phone="+19999999999"))
        email = replace(before, contact=replace(contact, email="new@example.com"))
        channel = replace(before, contact=replace(contact, preferred_channel=Channel.SMS))
        assert changed_fields(before, phone) == {ProspectField.PHONE}
        assert changed_fields(before, email) == {ProspectField.EMAIL}
        assert changed_fields(before, channel) == {ProspectField.PREFERRED_CHANNEL}

    def test_the_next_action_is_one_field_whatever_part_of_it_changes(self):
        before = make_prospect()
        after = replace(
            before,
            schedule=Scheduling(next_action_type=SalesActionType.FOLLOW_UP, next_action_due_at=NOW),
        )
        assert changed_fields(before, after) == {ProspectField.NEXT_ACTION}
        later = replace(
            after,
            schedule=replace(after.schedule, next_action_due_at=NOW + timedelta(days=1)),
        )
        assert changed_fields(after, later) == {ProspectField.NEXT_ACTION}

    def test_each_schedule_field_is_reported_alone(self):
        before = make_prospect()
        schedule = before.schedule
        for field, change in [
            (ProspectField.LAST_CONTACTED_AT, {"last_contacted_at": NOW}),
            (ProspectField.LAST_INBOUND_AT, {"last_inbound_at": NOW}),
            (ProspectField.FOLLOW_UP_COUNT, {"follow_up_count": 2}),
        ]:
            after = replace(before, schedule=replace(schedule, **change))
            assert changed_fields(before, after) == {field}

    def test_attributes_are_never_a_changed_field(self):
        before = make_prospect()
        assert changed_fields(before, replace(before, attributes={"plan": "gold"})) == frozenset()

    def test_several_changes_are_all_reported(self):
        before = make_prospect()
        after = replace(before, status=S.ACTIVE, stage="s2", locale="ar-TN")
        assert changed_fields(before, after) == {
            ProspectField.STATUS,
            ProspectField.STAGE,
            ProspectField.LOCALE,
        }

    def test_field_values_are_comparable(self):
        p = make_prospect(status=S.ACTIVE, stage="s1")
        assert field_value(p, ProspectField.STATUS) is S.ACTIVE
        assert field_value(p, ProspectField.NEXT_ACTION) == (None, None)
        assert field_value(p, ProspectField.PHONE) == p.contact.phone


# ---- statuses ----------------------------------------------------------------------------------


class TestSeverity:
    def test_the_order_is_opt_out_then_closed_then_paused_then_contactable(self):
        assert severity(S.OPTED_OUT) > severity(S.CLOSED) > severity(S.PAUSED) > 0
        assert severity(S.NEW) == severity(S.ACTIVE) == 0

    def test_only_new_and_active_may_be_contacted(self):
        assert CONTACTABLE == {S.NEW, S.ACTIVE}
        assert {s for s in S if not is_restrictive(s)} == CONTACTABLE

    @pytest.mark.parametrize(("current", "proposed"), STATUS_PAIRS)
    def test_lowering_a_restriction_means_a_lower_severity(self, current, proposed):
        assert lowers_restriction(current, proposed) == (severity(proposed) < severity(current))

    def test_lifting_any_restriction_is_a_lowering_and_tightening_is_not(self):
        for restrictive in (S.PAUSED, S.CLOSED, S.OPTED_OUT):
            assert lowers_restriction(restrictive, S.ACTIVE)
            assert lowers_restriction(restrictive, S.NEW)
            assert not lowers_restriction(S.ACTIVE, restrictive)
        assert lowers_restriction(S.OPTED_OUT, S.CLOSED)
        assert not lowers_restriction(S.NEW, S.ACTIVE)


class TestEffectiveStatus:
    @pytest.mark.parametrize(
        ("source", "overlay", "expected"),
        [
            (None, None, S.NEW),
            (S.NEW, None, S.NEW),
            (S.ACTIVE, None, S.ACTIVE),
            (None, S.ACTIVE, S.ACTIVE),
            (S.NEW, S.ACTIVE, S.ACTIVE),  # Revansa's own lifecycle wins when nothing restricts
            (S.ACTIVE, S.NEW, S.NEW),
            (S.CLOSED, S.PAUSED, S.CLOSED),
            (S.PAUSED, S.CLOSED, S.CLOSED),
            (S.PAUSED, S.OPTED_OUT, S.OPTED_OUT),
            (S.OPTED_OUT, S.ACTIVE, S.OPTED_OUT),  # the source's refusal beats Revansa's activity
            (S.ACTIVE, S.PAUSED, S.PAUSED),  # Revansa's pause beats the source's activity
            (S.PAUSED, S.ACTIVE, S.PAUSED),
            (S.NEW, S.OPTED_OUT, S.OPTED_OUT),
        ],
    )
    def test_the_documented_cases(self, source, overlay, expected):
        assert effective_status(source, overlay) is expected

    @pytest.mark.parametrize(("source", "overlay"), PAIRS)
    def test_a_contact_that_opted_out_is_opted_out_whatever_the_records_say(self, source, overlay):
        assert effective_status(source, overlay, contact_opted_out=True) is S.OPTED_OUT

    @pytest.mark.parametrize(("source", "overlay"), PAIRS)
    def test_the_result_is_never_less_strict_than_either_side(self, source, overlay):
        result = effective_status(source, overlay)
        for side in (source, overlay):
            if side is not None:
                assert severity(result) >= severity(side)

    @pytest.mark.parametrize(("source", "overlay"), PAIRS)
    def test_the_result_is_one_of_the_two_sides_or_new(self, source, overlay):
        assert effective_status(source, overlay) in {source, overlay, S.NEW}

    @pytest.mark.parametrize(("source", "overlay"), FREE_PAIRS)
    def test_without_any_restriction_revansas_status_then_the_sources_then_new(
        self, source, overlay
    ):
        assert effective_status(source, overlay) is (overlay or source or S.NEW)


class TestStatusMapping:
    def mapping(self, **extra):
        raw = {"Lead": "new", " Qualified ": "new", "On hold": "paused", "DNC": "opted_out"}
        return StatusMapping.from_config(raw | extra)

    def test_values_are_compared_ignoring_case_and_blanks(self):
        mapping = self.mapping()
        assert mapping.lookup("lead") is S.NEW and mapping.lookup("  LEAD ") is S.NEW
        assert mapping.lookup("qualified") is S.NEW
        assert mapping.lookup("on hold") is S.PAUSED and mapping.lookup("dnc") is S.OPTED_OUT

    def test_there_is_no_default_meaning_for_any_word(self):
        mapping = StatusMapping.from_config({"lead": "new"})
        for word in ["closed", "won", "lost", "customer", "client", "unsubscribed", "dead"]:
            assert mapping.lookup(word) is None

    def test_closed_is_whatever_the_operator_says(self):
        mapping = self.mapping(Won="closed", Lost="closed", Archived="closed")
        assert {mapping.lookup(w) for w in ("won", "lost", "archived")} == {S.CLOSED}

    def test_active_cannot_be_imposed_by_a_mapping(self):
        for target in ("active", "ACTIVE", " Active "):
            with pytest.raises(StatusMappingError, match="only Revansa"):
                self.mapping(Engaged=target)
        assert S.ACTIVE not in MAPPABLE and MAPPABLE == {S.NEW, S.PAUSED, S.CLOSED, S.OPTED_OUT}

    @pytest.mark.parametrize("target", ["won", "", "contacted", "none"])
    def test_a_target_must_be_a_status(self, target):
        with pytest.raises(StatusMappingError, match="is not a status"):
            self.mapping(Odd=target)

    def test_a_blank_crm_value_is_refused(self):
        with pytest.raises(StatusMappingError, match="blank"):
            StatusMapping.from_config({"lead": "new", "  ": "paused"})

    def test_one_value_cannot_lead_to_two_statuses(self):
        with pytest.raises(StatusMappingError, match="two different"):
            StatusMapping.from_config({"Lead": "new", "lead": "paused"})

    def test_the_same_value_twice_with_the_same_target_is_harmless(self):
        assert StatusMapping.from_config({"Lead": "new", "lead": "new"}).lookup("LEAD") is S.NEW

    def test_a_mapping_that_never_leads_to_new_is_refused(self):
        with pytest.raises(StatusMappingError, match="nothing could be contacted"):
            StatusMapping.from_config({"dead": "closed"})
        with pytest.raises(StatusMappingError):
            StatusMapping.from_config({})

    def test_a_mapping_cannot_be_changed_afterwards(self):
        with pytest.raises(TypeError):
            self.mapping().values["x"] = S.NEW  # type: ignore[index]
        assert isinstance(self.mapping().values, MappingProxyType)


class TestResolveSourceStatus:
    MAPPING = StatusMapping.from_config(
        {"lead": "new", "hold": "paused", "dead": "closed", "dnc": "opted_out"}
    )

    def resolve(self, raw, **kw):
        return resolve_source_status(raw, self.MAPPING, **kw)

    @pytest.mark.parametrize("raw", [None, "", "   ", "\t"])
    def test_a_blank_status_excludes_the_record(self, raw):
        result = self.resolve(raw)
        assert result.excluded and result.status is None
        assert result.problem is ProblemCode.BLANK_STATUS and not result.opt_out_signal

    @pytest.mark.parametrize("raw", ["mystery", "closed", "Contacted", "lead2"])
    def test_a_status_nobody_mapped_excludes_the_record(self, raw):
        result = self.resolve(raw)
        assert result.excluded and result.problem is ProblemCode.UNMAPPED_STATUS
        assert not result.opt_out_signal

    @pytest.mark.parametrize(
        ("raw", "status"),
        [("lead", S.NEW), ("HOLD", S.PAUSED), (" dead ", S.CLOSED), ("dnc", S.OPTED_OUT)],
    )
    def test_a_mapped_status_keeps_the_record(self, raw, status):
        result = self.resolve(raw)
        assert result.status is status and result.problem is None and not result.excluded
        assert not result.opt_out_signal

    @pytest.mark.parametrize("raw", ["lead", "hold", "dead", "dnc"])
    def test_a_refusal_on_a_kept_record_makes_it_opted_out(self, raw):
        result = self.resolve(raw, do_not_contact=True)
        assert result.status is S.OPTED_OUT and result.opt_out_signal and result.problem is None

    @pytest.mark.parametrize(
        ("raw", "problem"),
        [
            (None, ProblemCode.BLANK_STATUS),
            ("", ProblemCode.BLANK_STATUS),
            ("mystery", ProblemCode.UNMAPPED_STATUS),
        ],
    )
    def test_a_refusal_never_brings_an_unusable_record_into_the_cycle(self, raw, problem):
        result = self.resolve(raw, do_not_contact=True)
        assert result.excluded and result.status is None
        assert result.problem is problem
        assert result.opt_out_signal  # the refusal is still reported, next to the problem

    @pytest.mark.parametrize(
        "raw", [None, "", "  ", "lead", "hold", "dead", "dnc", "mystery", "closed"]
    )
    def test_a_refusal_never_makes_anything_contactable(self, raw):
        result = self.resolve(raw, do_not_contact=True)
        assert result.status is None or result.status not in CONTACTABLE
        assert result.opt_out_signal


# ---- merging -------------------------------------------------------------------------------------

CRM_OWNED = SOURCE_ONLY_FIELDS | {ProspectField.STATUS}
ID = ProspectId("fake_crm:1001")
NEXT = SalesActionType.FOLLOW_UP


def source_record(**overrides):
    return make_prospect(
        prospect_id=ProspectId("1001"),
        display_name="Ana Test",
        contact=ContactInfo(phone="+216 22 000 001", email="ana@example.com"),
        locale="fr",
        attributes={"plan": "gold"},
        **overrides,
    )


def state(**overrides):
    values = {
        "client_id": CLIENT,
        "prospect_id": ID,
        "stage": "qualified",
        "conversation_id": ConversationId("c-77"),
        "schedule": Scheduling(
            next_action_type=NEXT,
            next_action_due_at=NOW + timedelta(days=2),
            last_contacted_at=NOW,
            last_inbound_at=NOW - timedelta(hours=3),
            follow_up_count=2,
        ),
    }
    return ProspectState(**(values | overrides))


class TestMerge:
    def merge(self, source=None, st=None, **kw):
        kw.setdefault("owned", CRM_OWNED)
        kw.setdefault("public_id", ID)
        return merge_prospect(source or source_record(), st, **kw)

    def test_who_the_person_is_always_comes_from_the_source(self):
        merged = self.merge(st=state())
        source = source_record()
        assert merged.display_name == source.display_name and merged.contact == source.contact
        assert merged.locale == "fr" and dict(merged.attributes) == {"plan": "gold"}
        assert merged.client_id == CLIENT and merged.prospect_id == ID

    def test_what_revansa_did_comes_from_its_state_when_the_source_does_not_own_it(self):
        merged = self.merge(st=state())
        assert merged.stage == "qualified" and merged.conversation_id == "c-77"
        assert merged.schedule == state().schedule

    def test_a_prospect_revansa_never_touched_has_no_state_of_its_own(self):
        merged = self.merge(source_record(stage="junk", status=S.NEW), None)
        assert merged.stage is None and merged.conversation_id is None
        assert merged.schedule == Scheduling()
        assert merged.status is S.NEW

    def test_values_the_source_does_not_own_are_ignored_even_if_it_returns_some(self):
        junk = source_record(
            stage="junk",
            conversation_id=ConversationId("c-junk"),
            schedule=Scheduling(follow_up_count=9, last_contacted_at=NOW),
        )
        merged = self.merge(junk, state())
        assert merged.stage == "qualified" and merged.conversation_id == "c-77"
        assert merged.schedule.follow_up_count == 2

    def test_a_source_that_owns_everything_is_returned_as_it_is(self):
        full = source_record(
            status=S.ACTIVE,
            stage="s1",
            conversation_id=ConversationId("c-1"),
            schedule=Scheduling(next_action_type=NEXT, next_action_due_at=NOW, follow_up_count=1),
        )
        merged = self.merge(full, None, owned=set(ProspectField), public_id=None)
        assert merged == full

    def test_a_source_that_owns_a_field_is_read_for_it_even_if_a_state_exists(self):
        full = source_record(status=S.ACTIVE, stage="from-source")
        merged = self.merge(full, state(status=S.NEW), owned=set(ProspectField))
        assert merged.stage == "from-source" and merged.status is S.NEW

    def test_the_public_id_replaces_the_native_one(self):
        assert self.merge().prospect_id == ID
        assert self.merge(public_id=None, st=None).prospect_id == "1001"

    @pytest.mark.parametrize(
        ("source_status", "state_status", "expected"),
        [
            (S.NEW, None, S.NEW),
            (S.NEW, S.ACTIVE, S.ACTIVE),
            (S.PAUSED, S.ACTIVE, S.PAUSED),
            (S.NEW, S.PAUSED, S.PAUSED),
            (S.CLOSED, S.PAUSED, S.CLOSED),
            (S.OPTED_OUT, S.ACTIVE, S.OPTED_OUT),
        ],
    )
    def test_the_status_is_the_effective_one(self, source_status, state_status, expected):
        merged = self.merge(source_record(status=source_status), state(status=state_status))
        assert merged.status is expected

    @pytest.mark.parametrize("state_status", [None, S.NEW, S.ACTIVE, S.PAUSED, S.CLOSED])
    def test_a_contact_that_opted_out_is_opted_out_on_every_record(self, state_status):
        merged = self.merge(
            source_record(status=S.NEW), state(status=state_status), contact_opted_out=True
        )
        assert merged.status is S.OPTED_OUT

    def test_a_status_the_source_does_not_own_is_not_read_from_it(self):
        merged = self.merge(source_record(status=S.CLOSED), None, owned=SOURCE_ONLY_FIELDS)
        assert merged.status is S.NEW

    def test_a_state_of_another_prospect_or_client_is_refused(self):
        with pytest.raises(ValueError):
            self.merge(st=state(prospect_id=ProspectId("fake_crm:2002")))
        with pytest.raises(ValueError):
            self.merge(st=state(client_id="client-b"))

    def test_the_inputs_are_not_changed(self):
        source, held = source_record(), state()
        self.merge(source, held)
        assert source == source_record() and held == state()


# ---- contract objects ------------------------------------------------------------------------------


class TestCapabilities:
    def test_a_source_always_owns_what_a_person_is(self):
        with pytest.raises(ValueError, match="always owns"):
            SourceCapabilities(owned={ProspectField.STATUS})
        assert SourceCapabilities(owned=SOURCE_ONLY_FIELDS).writable == frozenset()

    def test_it_can_only_write_what_it_owns(self):
        with pytest.raises(ValueError, match="only write fields it owns"):
            SourceCapabilities(owned=SOURCE_ONLY_FIELDS, writable={ProspectField.STATUS})

    def test_its_namespace_must_look_like_a_provider_key_or_be_absent(self):
        assert SourceCapabilities(owned=SOURCE_ONLY_FIELDS, id_namespace=None).id_namespace is None
        with pytest.raises(ValueError):
            SourceCapabilities(owned=SOURCE_ONLY_FIELDS, id_namespace="Bad Key")

    def test_it_is_read_only_and_cannot_create_by_default(self):
        caps = SourceCapabilities(owned=SOURCE_ONLY_FIELDS)
        assert not caps.writable and not caps.can_create
        assert not caps.atomic_compare_and_set and not caps.consent_signal

    def test_it_cannot_be_changed(self):
        caps = SourceCapabilities(owned=SOURCE_ONLY_FIELDS)
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.can_create = True  # type: ignore[misc]


class TestChange:
    def change(self, **kw):
        values = {
            "prospect_id": "1001",
            "new": {ProspectField.STATUS: S.PAUSED},
            "expected": {ProspectField.STATUS: S.NEW},
        }
        return ProspectChange(**(values | kw))

    def test_a_change_names_the_values_it_replaces(self):
        change = self.change()
        assert change.new[ProspectField.STATUS] is S.PAUSED
        assert change.expected[ProspectField.STATUS] is S.NEW

    def test_it_needs_an_id_and_a_field(self):
        with pytest.raises(ValueError):
            self.change(prospect_id="")
        with pytest.raises(ValueError):
            self.change(new={}, expected={})

    def test_it_must_give_the_expected_value_of_exactly_the_fields_it_writes(self):
        with pytest.raises(ValueError, match="expected value"):
            self.change(expected={})
        with pytest.raises(ValueError, match="expected value"):
            self.change(expected={ProspectField.STAGE: None})

    def test_it_is_a_copy_that_cannot_be_changed(self):
        raw = {ProspectField.STATUS: S.PAUSED}
        change = self.change(new=raw)
        raw[ProspectField.STAGE] = "x"
        assert set(change.new) == {ProspectField.STATUS}
        with pytest.raises(TypeError):
            change.new[ProspectField.STAGE] = "y"  # type: ignore[index]


class TestState:
    def test_a_fresh_state_is_revision_zero_with_nothing_set(self):
        fresh = ProspectState(client_id=CLIENT, prospect_id=ID)
        assert fresh.revision == 0 and fresh.status is None and fresh.stage is None
        assert fresh.schedule == Scheduling() and fresh.conversation_id is None

    def test_it_needs_a_client_and_a_prospect(self):
        with pytest.raises(ValueError):
            ProspectState(client_id=CLIENT, prospect_id="")
        with pytest.raises(ValueError):
            ProspectState(client_id="", prospect_id=ID)

    def test_the_revision_cannot_be_negative(self):
        with pytest.raises(ValueError, match="revision"):
            ProspectState(client_id=CLIENT, prospect_id=ID, revision=-1)

    def test_a_timestamp_must_carry_a_time_zone(self):
        with pytest.raises(ValueError, match="timezone"):
            ProspectState(client_id=CLIENT, prospect_id=ID, status_changed_at=NAIVE_NOW)

    def test_it_carries_no_name_and_no_contact(self):
        names = {f.name for f in dataclasses.fields(ProspectState)}
        assert not names & {"display_name", "phone", "email", "contact", "attributes", "locale"}


class TestErrors:
    def test_every_source_failure_is_a_source_error(self):
        for error in (
            SourceUnavailableError,
            SourceRateLimitedError,
            SourceAuthError,
            SourceReadOnlyError,
            SourceDataError,
            InvalidNativeIdError,
        ):
            assert issubclass(error, SourceError)
        assert issubclass(InvalidNativeIdError, SourceDataError)
        assert issubclass(SourceConflictError, SourceError)

    def test_every_store_failure_is_a_store_error(self):
        assert issubclass(StoreUnavailableError, StoreError)
        assert issubclass(StateConflictError, StoreError)
        assert not issubclass(StoreError, SourceError)

    def test_a_rate_limit_may_say_when_to_come_back(self):
        assert SourceRateLimitedError().retry_after is None
        assert SourceRateLimitedError(retry_after=12.5).retry_after == 12.5

    def test_a_conflict_names_the_prospect_and_the_fields_but_never_a_value(self):
        error = SourceConflictError("1001", ("status",))
        assert error.prospect_id == "1001" and error.fields == ("status",)
        assert "1001" not in str(error) and "status" not in str(error)

    def test_a_state_conflict_names_the_prospect_but_does_not_show_it(self):
        error = StateConflictError("fake_crm:1001")
        assert error.prospect_id == "fake_crm:1001" and "1001" not in str(error)


class TestProblems:
    def test_a_problem_is_a_fixed_sentence_and_a_code(self):
        problem = SourceProblem(ProblemCode.BLANK_STATUS, "a record has no status", "1001")
        assert problem.native_id == "1001"
        with pytest.raises(dataclasses.FrozenInstanceError):
            problem.detail = "x"  # type: ignore[misc]

    def test_the_codes_are_stable_text(self):
        assert {c.value for c in ProblemCode} == {
            "invalid_native_id",
            "invalid_local_id",
            "blank_status",
            "unmapped_status",
            "unreadable_record",
            "duplicate_contact",
        }
