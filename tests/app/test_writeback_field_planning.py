"""The rules that decide whether Revansa may write a date, a count or a next action into a source,
and when it must leave a cell alone. Pure: no source, no store, no ledger."""

from datetime import UTC, datetime, timedelta

import pytest
from factories import CLIENT, make_prospect
from sourcing_fakes import default_capabilities

from sales_app.sourcing import ProspectField, ProspectState
from sales_app.sourcing.writeback import (
    SkipReason,
    WriteBackPolicy,
    WriteBackPolicyError,
    plan_field_writes,
    value_text,
)
from sales_core.contracts import (
    ContactInfo,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)

F = ProspectField
S = ProspectStatus
MIRRORED = {F.LAST_CONTACTED_AT, F.LAST_INBOUND_AT, F.FOLLOW_UP_COUNT, F.NEXT_ACTION}
EVERYTHING = WriteBackPolicy.of(F.STATUS, *MIRRORED)
CAPS = default_capabilities(writable=frozenset({F.STATUS, *MIRRORED}), mirrored=frozenset(MIRRORED))
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=2)
EARLIER = T0 - timedelta(days=2)
NONE = (None, None)
FOLLOW_UP = SalesActionType.FOLLOW_UP
CALLBACK = SalesActionType.CALLBACK


def record(native="1"):
    return make_prospect(
        prospect_id=ProspectId(native), status=S.NEW, contact=ContactInfo(phone="+216 99 888 777")
    )


def state(native="1", **schedule):
    return {
        ProspectId(f"fake_crm:{native}"): ProspectState(
            client_id=CLIENT,
            prospect_id=ProspectId(f"fake_crm:{native}"),
            schedule=Scheduling(**schedule),
        )
    }


def sheet(**held):
    """What the source holds in the mirrored cells; every cell empty unless said otherwise."""
    base = {
        F.LAST_CONTACTED_AT: None,
        F.LAST_INBOUND_AT: None,
        F.FOLLOW_UP_COUNT: 0,
        F.NEXT_ACTION: NONE,
    }
    return {"1": {**base, **{F(k): v for k, v in held.items()}}}


def plan(states, values, *, policy=EVERYTHING, written=None, records=None, asked=None):
    def last_written(public, field):
        if asked is not None:
            asked.append((public, field))
        return (written or {}).get(field)

    return plan_field_writes(
        records if records is not None else [record()],
        states,
        values,
        capabilities=CAPS,
        policy=policy,
        is_blocked=lambda contact: False,
        registry_healthy=True,
        last_written=last_written,
    )


def only(result):
    (write,) = result.writes
    return write


# ---- the two moments -----------------------------------------------------------------------------


@pytest.mark.parametrize("field", [F.LAST_CONTACTED_AT, F.LAST_INBOUND_AT])
class TestAMoment:
    def schedule(self, field, value):
        return {field.value: value}

    def test_an_empty_cell_is_filled(self, field):
        result = plan(state(**self.schedule(field, T0)), sheet())
        write = only(result)
        assert (write.field, write.new, write.expected) == (field, T0, None)

    def test_an_earlier_moment_is_replaced(self, field):
        write = only(plan(state(**self.schedule(field, T0)), sheet(**{field.value: EARLIER})))
        assert (write.new, write.expected) == (T0, EARLIER)

    @pytest.mark.parametrize(
        ("held", "reason"), [(T0, SkipReason.ALREADY_APPLIED), (LATER, SkipReason.NOT_ADVANCING)]
    )
    def test_an_equal_or_later_moment_is_left_alone(self, field, held, reason):
        result = plan(state(**self.schedule(field, T0)), sheet(**{field.value: held}))
        assert result.writes == () and result.skipped[reason] == 1

    def test_nothing_is_written_when_revansa_has_no_moment(self, field):
        result = plan(state(), sheet(**{field.value: LATER}))
        assert result.writes == () and result.skipped[SkipReason.NOTHING_TO_WRITE] >= 1

    def test_a_moment_is_compared_to_the_second_so_a_write_is_never_earlier_than_itself(
        self, field
    ):
        exact = T0 + timedelta(microseconds=123456)
        write = only(plan(state(**self.schedule(field, exact)), sheet()))
        assert write.new == T0
        again = plan(state(**self.schedule(field, exact)), sheet(**{field.value: write.new}))
        assert again.writes == ()


# ---- the count ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mine", "held", "written"),
    [
        (3, 0, True),
        (3, 2, True),
        (1, 0, True),
        (3, 3, False),
        (3, 5, False),
        (0, 0, False),
        (0, 4, False),
    ],
)
def test_a_count_is_written_only_when_it_is_larger_than_the_cell(mine, held, written):
    result = plan(state(follow_up_count=mine), sheet(follow_up_count=held))
    assert bool(result.writes) is written
    if written:
        assert (only(result).new, only(result).expected) == (mine, held)


# ---- the next action ---------------------------------------------------------------------------


def mine(action=FOLLOW_UP, due=LATER):
    return {"next_action_type": action, "next_action_due_at": due}


def test_an_empty_next_action_is_filled():
    write = only(plan(state(**mine()), sheet()))
    assert (write.field, write.new, write.expected) == (F.NEXT_ACTION, (FOLLOW_UP, LATER), NONE)


def test_a_next_action_a_person_put_there_is_never_overwritten_whatever_revansa_wants():
    result = plan(state(**mine()), sheet(next_action=(CALLBACK, EARLIER)))
    assert result.writes == () and result.skipped[SkipReason.OCCUPIED] == 1


def test_what_revansa_wrote_itself_is_replaced_by_its_next_value():
    theirs = (FOLLOW_UP, T0)
    written = {F.NEXT_ACTION: value_text(F.NEXT_ACTION, theirs)}
    write = only(plan(state(**mine(CALLBACK, LATER)), sheet(next_action=theirs), written=written))
    assert (write.new, write.expected) == ((CALLBACK, LATER), theirs)


def test_what_revansa_wrote_is_cleared_when_it_has_no_next_action_any_more():
    theirs = (FOLLOW_UP, T0)
    written = {F.NEXT_ACTION: value_text(F.NEXT_ACTION, theirs)}
    write = only(plan(state(), sheet(next_action=theirs), written=written))
    assert (write.new, write.expected) == (NONE, theirs)


def test_a_next_action_that_is_not_revansas_is_not_cleared_either():
    result = plan(state(), sheet(next_action=(CALLBACK, LATER)), written={F.NEXT_ACTION: "other"})
    assert result.writes == () and result.skipped[SkipReason.OCCUPIED] == 1


def test_a_cell_a_person_changed_after_revansa_wrote_it_is_theirs_now():
    written = {F.NEXT_ACTION: value_text(F.NEXT_ACTION, (FOLLOW_UP, T0))}
    result = plan(
        state(**mine(CALLBACK, LATER)), sheet(next_action=(FOLLOW_UP, EARLIER)), written=written
    )
    assert result.writes == ()


def test_the_same_next_action_is_not_written_again_and_none_is_nothing_to_do():
    same = plan(state(**mine()), sheet(next_action=(FOLLOW_UP, LATER)))
    assert same.writes == () and same.skipped[SkipReason.ALREADY_APPLIED] == 1
    nothing = plan(state(), sheet())
    assert nothing.writes == () and nothing.skipped[SkipReason.NOTHING_TO_WRITE] >= 1


# ---- what is never planned ---------------------------------------------------------------------


def test_only_the_fields_the_policy_allows_are_planned():
    everything_due = state(last_contacted_at=T0, last_inbound_at=T0, follow_up_count=2, **mine())
    narrow = plan(everything_due, sheet(), policy=WriteBackPolicy.of(F.FOLLOW_UP_COUNT))
    assert [w.field for w in narrow.writes] == [F.FOLLOW_UP_COUNT]
    full = plan(everything_due, sheet())
    assert [w.field for w in full.writes] == [
        F.LAST_CONTACTED_AT,
        F.LAST_INBOUND_AT,
        F.FOLLOW_UP_COUNT,
        F.NEXT_ACTION,
    ]


def test_a_prospect_revansa_knows_nothing_of_is_left_alone():
    result = plan({}, sheet())
    # the four mirrored fields and the status, each counted once
    assert result.writes == () and result.skipped[SkipReason.NOTHING_TO_WRITE] == 5


def test_a_row_the_source_could_not_describe_is_left_alone_and_counted():
    result = plan(state(follow_up_count=2), {})
    assert result.writes == () and result.skipped[SkipReason.UNREADABLE_VALUE] == 4


def test_another_prospects_state_is_never_used():
    result = plan(state("2", follow_up_count=9), sheet())
    assert result.writes == ()


def test_the_policy_must_fit_the_source():
    narrow = default_capabilities(writable=frozenset({F.STATUS}))
    with pytest.raises(WriteBackPolicyError):
        plan_field_writes(
            [record()], {}, {}, capabilities=narrow, policy=EVERYTHING,
            is_blocked=lambda c: False, registry_healthy=True, last_written=lambda p, f: None,
        )  # fmt: skip


def test_the_status_is_still_planned_by_its_own_rules_in_the_same_pass():
    states = {
        ProspectId("fake_crm:1"): ProspectState(
            client_id=CLIENT,
            prospect_id=ProspectId("fake_crm:1"),
            status=S.PAUSED,
            schedule=Scheduling(follow_up_count=2),
        )
    }
    result = plan(states, sheet())
    assert [(w.field, w.new) for w in result.writes] == [
        (F.STATUS, S.PAUSED),
        (F.FOLLOW_UP_COUNT, 2),
    ]


# ---- the text the ledger keeps -----------------------------------------------------------------


def test_the_canonical_text_of_each_field():
    assert value_text(F.STATUS, S.PAUSED) is S.PAUSED
    assert value_text(F.LAST_CONTACTED_AT, None) == ""
    assert (
        value_text(F.LAST_INBOUND_AT, T0 + timedelta(microseconds=5)) == "2026-03-01T09:00:00+00:00"
    )
    assert value_text(F.FOLLOW_UP_COUNT, None) == "" and value_text(F.FOLLOW_UP_COUNT, 3) == "3"
    assert value_text(F.NEXT_ACTION, NONE) == ""
    assert value_text(F.NEXT_ACTION, (FOLLOW_UP, T0)) == "follow_up|2026-03-01T09:00:00+00:00"


# ---- what a source may declare ------------------------------------------------------------------


def test_a_source_can_write_what_it_mirrors_but_never_what_it_neither_owns_nor_mirrors():
    assert default_capabilities().mirrored == frozenset()
    assert CAPS.writable == {F.STATUS, *MIRRORED} and CAPS.mirrored == MIRRORED
    assert not CAPS.mirrored & CAPS.owned  # a mirror is never the truth
    with pytest.raises(ValueError):
        default_capabilities(writable=frozenset({F.STAGE}))  # neither owned nor mirrored
    with pytest.raises(ValueError):
        default_capabilities(mirrored=frozenset({F.PHONE}))  # what a person is is never a copy
    with pytest.raises(ValueError):
        default_capabilities(writable=frozenset({F.FOLLOW_UP_COUNT}))  # writable needs a home


# ---- the ledger is asked only when the answer depends on it ----------------------------------------


def test_the_ledger_is_never_asked_for_a_moment_a_count_or_a_prospect_with_nothing_to_say():
    asked = []
    busy = state(last_contacted_at=T0, last_inbound_at=T0, follow_up_count=2)
    result = plan(busy, sheet(), asked=asked)
    assert len(result.writes) == 3 and asked == []
    plan(busy, sheet(last_contacted_at=LATER, follow_up_count=9), asked=asked)  # nothing written
    plan({}, sheet(), asked=asked)  # no state at all
    only_status = WriteBackPolicy.of(F.STATUS)
    plan(state(), sheet(next_action=(CALLBACK, LATER)), policy=only_status, asked=asked)
    assert asked == []


@pytest.mark.parametrize(
    ("held_by_revansa", "held_in_sheet"),
    [
        (mine(), NONE),  # an empty cell is written without asking whose it was
        (mine(), (FOLLOW_UP, LATER)),  # an equal one is already applied
        ({}, NONE),  # nothing on either side
    ],
)
def test_the_ledger_is_not_asked_when_the_next_action_cell_is_empty_or_already_equal(
    held_by_revansa, held_in_sheet
):
    asked = []
    plan(state(**held_by_revansa), sheet(next_action=held_in_sheet), asked=asked)
    assert asked == []


def test_the_ledger_is_asked_once_and_only_for_a_filled_cell_that_differs():
    asked = []
    result = plan(state(**mine()), sheet(next_action=(CALLBACK, EARLIER)), asked=asked)
    assert asked == [("fake_crm:1", F.NEXT_ACTION)]
    assert result.writes == () and result.skipped[SkipReason.OCCUPIED] == 1


def test_the_ledger_is_asked_for_each_prospect_that_needs_it_and_not_for_the_others():
    asked = []
    states = {**state("1", **mine()), **state("2", **mine()), **state("3", **mine())}
    values = {
        "1": sheet(next_action=(CALLBACK, EARLIER))["1"],  # filled, differs: asked
        "2": sheet()["1"],  # empty: not asked
        "3": sheet(next_action=(FOLLOW_UP, LATER))["1"],  # equal: not asked
    }
    plan(states, values, records=[record("1"), record("2"), record("3")], asked=asked)
    assert asked == [("fake_crm:1", F.NEXT_ACTION)]


def test_asking_late_changes_no_answer_the_rules_are_the_same_as_before():
    theirs = (FOLLOW_UP, T0)
    ours = value_text(F.NEXT_ACTION, theirs)
    for written, expected in ((ours, True), (None, False), ("something else", False)):
        result = plan(
            state(**mine(CALLBACK, LATER)),
            sheet(next_action=theirs),
            written={F.NEXT_ACTION: written} if written is not None else None,
        )
        assert bool(result.writes) is expected
