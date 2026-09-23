import random
from datetime import UTC, datetime, timedelta

import pytest
from factories import CLIENT, NAIVE_NOW, NOW, make_action, make_client_context, make_prospect

from sales_core.contracts import (
    ActionId,
    ActionPriority,
    ActionStatus,
    ClientId,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)
from sales_core.planner import DEFAULT_DAILY_ACTION_LIMIT, plan_daily_actions
from sales_integrations.fakes import FakeSalesRepository

FU = SalesActionType.FOLLOW_UP
REM = SalesActionType.REMINDER
CB = SalesActionType.CALLBACK
FIRST = SalesActionType.FIRST_CONTACT
REPLY = SalesActionType.REPLY

CLIENT_CTX = make_client_context()  # timezone UTC


def scheduled(pid: str, kind: SalesActionType, due: datetime = NOW - timedelta(hours=1), **kw):
    """A prospect with `kind` scheduled at `due`."""
    return make_prospect(
        prospect_id=ProspectId(pid),
        status=kw.pop("status", ProspectStatus.ACTIVE),
        schedule=Scheduling(next_action_type=kind, next_action_due_at=due),
        **kw,
    )


def new(pid: str, **kw):
    return make_prospect(prospect_id=ProspectId(pid), **kw)


def existing(pid: str, *, status=ActionStatus.SENT, kind=FU, due=NOW, key=None):
    return make_action(
        action_id=ActionId(f"a-{pid}-{kind}-{status}"),
        prospect_id=ProspectId(pid),
        action_type=kind,
        status=status,
        due_at=due,
        idempotency_key=key or f"existing:{pid}:{kind}:{status}:{due.isoformat()}",
    )


def plan(prospects, existing_actions=(), *, client=CLIENT_CTX, now=NOW, **kw):
    return plan_daily_actions(
        client=client,
        prospects=list(prospects),
        existing_actions=list(existing_actions),
        now=now,
        **kw,
    )


def ids(result) -> list[str]:
    return [a.prospect_id for a in result.actions]


class TestPriorityOrdering:
    def test_strict_tier_order_regardless_of_input_order(self):
        prospects = [
            new("p-new"),
            scheduled("p-cb", CB),
            scheduled("p-rem", REM),
            scheduled("p-fu", FU),
        ]
        result = plan(prospects)
        assert ids(result) == ["p-fu", "p-rem", "p-cb", "p-new"]
        assert [a.action_type for a in result.actions] == [FU, REM, CB, FIRST]

    def test_priority_field_mirrors_tier_order(self):
        result = plan([new("a"), scheduled("b", CB), scheduled("c", REM), scheduled("d", FU)])
        assert [a.priority for a in result.actions] == [
            ActionPriority.URGENT,
            ActionPriority.HIGH,
            ActionPriority.NORMAL,
            ActionPriority.LOW,
        ]

    def test_a_less_overdue_follow_up_still_beats_a_more_overdue_callback(self):
        prospects = [
            scheduled("cb", CB, NOW - timedelta(days=30)),
            scheduled("fu", FU, NOW - timedelta(minutes=1)),
        ]
        assert ids(plan(prospects)) == ["fu", "cb"]

    def test_planned_actions_are_ready_to_process(self):
        (action,) = plan([scheduled("p1", FU)]).actions
        assert action.status is ActionStatus.PLANNED
        assert action.due_at == NOW
        assert action.draft is None
        assert action.client_id == CLIENT
        assert action.action_id == action.idempotency_key


class TestDueness:
    def test_not_yet_due_items_are_not_selected(self):
        prospects = [scheduled("later", FU, NOW + timedelta(seconds=1))]
        assert plan(prospects).actions == ()

    def test_exactly_due_now_is_selected(self):
        assert ids(plan([scheduled("p1", FU, NOW)])) == ["p1"]

    def test_no_overdue_cutoff(self):
        assert ids(plan([scheduled("old", CB, NOW - timedelta(days=400))])) == ["old"]

    def test_active_prospect_with_nothing_scheduled_is_not_selected(self):
        assert plan([make_prospect(status=ProspectStatus.ACTIVE)]).actions == ()

    def test_scheduled_first_contact_is_a_new_prospect_that_must_be_due(self):
        prospects = [
            scheduled("due", FIRST, NOW - timedelta(hours=2), status=ProspectStatus.NEW),
            scheduled("future", FIRST, NOW + timedelta(hours=2), status=ProspectStatus.NEW),
        ]
        result = plan(prospects)
        assert ids(result) == ["due"]
        assert result.actions[0].action_type is FIRST


class TestOrderingWithinATier:
    def test_most_overdue_first(self):
        prospects = [
            scheduled("a", FU, NOW - timedelta(hours=1)),
            scheduled("b", FU, NOW - timedelta(days=3)),
            scheduled("c", FU, NOW - timedelta(days=1)),
        ]
        assert ids(plan(prospects)) == ["b", "c", "a"]

    def test_prospect_id_breaks_ties(self):
        due = NOW - timedelta(hours=1)
        prospects = [scheduled(pid, FU, due) for pid in ("p-3", "p-1", "p-2")]
        assert ids(plan(prospects)) == ["p-1", "p-2", "p-3"]

    def test_unscheduled_new_prospects_are_ordered_by_prospect_id(self):
        assert ids(plan([new("n-2"), new("n-3"), new("n-1")])) == ["n-1", "n-2", "n-3"]

    def test_overdue_scheduled_first_contact_precedes_unscheduled_new_prospects(self):
        prospects = [
            new("a-unscheduled"),
            scheduled("z-scheduled", FIRST, NOW - timedelta(days=1), status=ProspectStatus.NEW),
        ]
        assert ids(plan(prospects)) == ["z-scheduled", "a-unscheduled"]


class TestMaximumAndLimit:
    def test_default_limit_is_twenty(self):
        assert DEFAULT_DAILY_ACTION_LIMIT == 20

    def test_never_more_than_twenty(self):
        result = plan([new(f"n-{i:02d}") for i in range(35)])
        assert len(result.actions) == 20
        assert ids(result) == [f"n-{i:02d}" for i in range(20)]

    def test_higher_tiers_use_the_quota_first(self):
        prospects = [scheduled(f"f-{i:02d}", FU) for i in range(25)] + [new("n-1")]
        result = plan(prospects)
        assert len(result.actions) == 20
        assert all(a.action_type is FU for a in result.actions)

    def test_new_prospects_fill_what_is_left(self):
        prospects = [scheduled(f"f-{i:02d}", FU) for i in range(15)]
        prospects += [new(f"n-{i:02d}") for i in range(10)]
        result = plan(prospects)
        assert [a.action_type for a in result.actions].count(FU) == 15
        assert [a.action_type for a in result.actions].count(FIRST) == 5

    def test_custom_limit(self):
        assert len(plan([new(f"n-{i}") for i in range(10)], daily_limit=3).actions) == 3

    def test_zero_limit_plans_nothing(self):
        assert plan([new("n-1")], daily_limit=0).actions == ()

    def test_negative_limit_is_rejected(self):
        with pytest.raises(ValueError, match="daily_limit"):
            plan([], daily_limit=-1)

    def test_no_prospects_no_actions(self):
        result = plan([])
        assert result.actions == () and result.already_counted == 0


class TestNewProspects:
    def test_new_prospect_gets_a_first_contact_action(self):
        (action,) = plan([new("n-1")]).actions
        assert action.action_type is FIRST
        assert action.prospect_id == "n-1"

    def test_non_new_prospect_without_schedule_is_not_a_new_prospect(self):
        assert plan([make_prospect(status=ProspectStatus.ACTIVE)]).actions == ()


class TestExcludedStatuses:
    @pytest.mark.parametrize(
        "status", [ProspectStatus.OPTED_OUT, ProspectStatus.PAUSED, ProspectStatus.CLOSED]
    )
    def test_never_selected_in_any_tier(self, status):
        prospects = [
            new("new", status=status),
            scheduled("fu", FU, status=status),
            scheduled("rem", REM, status=status),
            scheduled("cb", CB, status=status),
        ]
        assert plan(prospects).actions == ()

    def test_eligible_neighbours_are_still_selected(self):
        prospects = [scheduled("bad", FU, status=ProspectStatus.OPTED_OUT), scheduled("ok", FU)]
        assert ids(plan(prospects)) == ["ok"]

    def test_excluded_prospects_do_not_consume_quota(self):
        prospects = [new(f"x-{i}", status=ProspectStatus.CLOSED) for i in range(30)] + [new("ok")]
        assert ids(plan(prospects)) == ["ok"]


class TestDailyQuotaAccounting:
    @pytest.mark.parametrize(
        "status",
        [
            ActionStatus.PLANNED,
            ActionStatus.PROPOSED,
            ActionStatus.APPROVED,
            ActionStatus.SENDING,
            ActionStatus.SENT,
            ActionStatus.COMPLETED,
        ],
    )
    def test_planned_approved_and_sent_actions_count(self, status):
        prior = [existing(f"old-{i}", status=status) for i in range(5)]
        result = plan([new(f"n-{i:02d}") for i in range(30)], prior)
        assert result.already_counted == 5
        assert len(result.actions) == 15

    @pytest.mark.parametrize(
        "status", [ActionStatus.FAILED, ActionStatus.REJECTED, ActionStatus.CANCELLED]
    )
    def test_failed_rejected_and_cancelled_actions_do_not_count(self, status):
        prior = [existing(f"old-{i}", status=status) for i in range(5)]
        result = plan([new(f"n-{i:02d}") for i in range(30)], prior)
        assert result.already_counted == 0
        assert len(result.actions) == 20

    def test_exhausted_quota_plans_nothing(self):
        prior = [existing(f"old-{i}") for i in range(20)]
        result = plan([scheduled("fu", FU), new("n-1")], prior)
        assert result.actions == () and result.already_counted == 20

    def test_over_quota_history_never_goes_negative(self):
        prior = [existing(f"old-{i}") for i in range(25)]
        assert plan([new("n-1")], prior).actions == ()

    def test_actions_of_other_days_do_not_count(self):
        prior = [existing(f"old-{i}", due=NOW - timedelta(days=1)) for i in range(20)]
        prior += [existing(f"fut-{i}", due=NOW + timedelta(days=1)) for i in range(20)]
        result = plan([new("n-1")], prior)
        assert result.already_counted == 0
        assert ids(result) == ["n-1"]

    def test_quota_is_per_client_limit_parameter(self):
        prior = [existing(f"old-{i}") for i in range(2)]
        assert len(plan([new(f"n-{i}") for i in range(9)], prior, daily_limit=5).actions) == 3

    def test_higher_priority_work_still_wins_the_remaining_slots(self):
        prior = [existing(f"old-{i}") for i in range(18)]
        result = plan([new("n-1"), scheduled("fu", FU), scheduled("cb", CB)], prior)
        assert ids(result) == ["fu", "cb"]


class TestInboundRepliesAreNotPlannedNorCounted:
    def test_reply_actions_do_not_consume_quota(self):
        replies = [existing(f"r-{i}", kind=REPLY, status=ActionStatus.SENT) for i in range(20)]
        result = plan([new(f"n-{i:02d}") for i in range(30)], replies)
        assert result.already_counted == 0
        assert len(result.actions) == 20

    def test_reply_in_any_counted_status_is_ignored_for_quota(self):
        replies = [
            existing(f"r-{i}", kind=REPLY, status=status)
            for i, status in enumerate(
                [ActionStatus.PLANNED, ActionStatus.APPROVED, ActionStatus.SENT]
            )
        ]
        assert plan([new("n-1")], replies, daily_limit=1).already_counted == 0

    def test_a_prospect_scheduled_with_a_reply_is_never_selected(self):
        assert plan([scheduled("p1", REPLY)]).actions == ()

    def test_the_planner_never_emits_reply_actions(self):
        prospects = [scheduled("a", FU), scheduled("b", REM), scheduled("c", CB), new("d")]
        assert REPLY not in {a.action_type for a in plan(prospects).actions}

    def test_an_open_reply_holds_back_outreach_to_the_same_prospect(self):
        pending_reply = existing("p1", kind=REPLY, status=ActionStatus.PROPOSED)
        result = plan([scheduled("p1", FU), scheduled("p2", FU)], [pending_reply])
        assert ids(result) == ["p2"]


class TestDeterminism:
    def _prospects(self):
        return (
            [scheduled(f"f-{i}", FU, NOW - timedelta(hours=i % 3)) for i in range(12)]
            + [scheduled(f"r-{i}", REM, NOW - timedelta(hours=i % 2)) for i in range(8)]
            + [scheduled(f"c-{i}", CB, NOW - timedelta(minutes=i)) for i in range(6)]
            + [new(f"n-{i}") for i in range(10)]
        )

    def test_input_order_does_not_change_the_result(self):
        prospects = self._prospects()
        baseline = plan(prospects)
        for seed in range(5):
            shuffled = prospects[:]
            random.Random(seed).shuffle(shuffled)
            assert plan(shuffled) == baseline

    def test_same_inputs_same_output(self):
        assert plan(self._prospects()) == plan(self._prospects())

    def test_existing_action_order_does_not_change_the_result(self):
        prior = [existing(f"old-{i}") for i in range(4)]
        assert plan(self._prospects(), prior) == plan(self._prospects(), prior[::-1])


class TestTimezoneAndDateHandling:
    TUNIS = make_client_context(timezone="Africa/Tunis")  # UTC+1, no DST
    NEW_YORK = make_client_context(timezone="America/New_York")  # UTC-5 in January

    def test_local_date_follows_the_client_timezone(self):
        late_utc = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
        assert plan([], now=late_utc).local_date.isoformat() == "2026-01-15"
        assert plan([], client=self.TUNIS, now=late_utc).local_date.isoformat() == "2026-01-16"
        assert plan([], client=self.NEW_YORK, now=late_utc).local_date.isoformat() == "2026-01-15"

    def test_the_local_date_is_part_of_the_idempotency_key(self):
        late_utc = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
        (utc_action,) = plan([new("n-1")], now=late_utc).actions
        (tunis_action,) = plan([new("n-1")], client=self.TUNIS, now=late_utc).actions
        assert utc_action.idempotency_key == "client-a:n-1:first_contact:2026-01-15"
        assert tunis_action.idempotency_key == "client-a:n-1:first_contact:2026-01-16"

    def test_the_same_instant_belongs_to_different_days_in_different_zones(self):
        # 2026-01-15T23:30Z is already the 16th in Tunis, still the 15th in UTC.
        action_time = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
        now = datetime(2026, 1, 16, 8, 0, tzinfo=UTC)
        prior = [existing(f"old-{i}", due=action_time) for i in range(20)]
        assert plan([new("n-1")], prior, now=now).already_counted == 0
        tunis = plan([new("n-1")], prior, client=self.TUNIS, now=now)
        assert tunis.already_counted == 20 and tunis.actions == ()

    def test_local_midnight_resets_the_quota(self):
        prior = [
            existing(f"old-{i}", due=datetime(2026, 1, 15, 22, 59, tzinfo=UTC)) for i in range(20)
        ]
        before_midnight = datetime(2026, 1, 15, 22, 59, 30, tzinfo=UTC)  # 23:59:30 in Tunis
        after_midnight = datetime(2026, 1, 15, 23, 0, 0, tzinfo=UTC)  # 00:00:00 in Tunis
        assert plan([new("n-1")], prior, client=self.TUNIS, now=before_midnight).actions == ()
        assert ids(plan([new("n-1")], prior, client=self.TUNIS, now=after_midnight)) == ["n-1"]

    def test_now_in_a_non_utc_offset_is_converted_correctly(self):
        from datetime import timezone

        plus_one = timezone(timedelta(hours=1))
        now = datetime(2026, 1, 16, 0, 30, tzinfo=plus_one)  # 2026-01-15T23:30Z
        assert plan([], client=self.TUNIS, now=now).local_date.isoformat() == "2026-01-16"
        assert plan([], now=now).local_date.isoformat() == "2026-01-15"

    def test_dst_zone_uses_the_offset_in_force_on_that_date(self):
        # New York is UTC-4 in July: 03:30Z on July 1st is still June 30th locally.
        summer = datetime(2026, 7, 1, 3, 30, tzinfo=UTC)
        assert plan([], client=self.NEW_YORK, now=summer).local_date.isoformat() == "2026-06-30"

    def test_unknown_timezone_is_rejected(self):
        with pytest.raises(ValueError, match="unknown timezone"):
            plan([], client=make_client_context(timezone="Mars/Olympus"))

    def test_naive_now_is_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            plan([], now=NAIVE_NOW)


class TestIdempotencyAndRepeatedRuns:
    def test_second_run_after_persisting_the_first_plans_nothing_new(self):
        prospects = [scheduled("fu", FU), scheduled("cb", CB), new("n-1")]
        first = plan(prospects)
        second = plan(prospects, first.actions)
        assert len(first.actions) == 3
        assert second.actions == ()
        assert second.already_counted == 3

    def test_partial_rerun_only_tops_up_the_remaining_quota(self):
        prospects = [new(f"n-{i:02d}") for i in range(30)]
        first = plan(prospects, daily_limit=5)
        second = plan(prospects, first.actions, daily_limit=8)
        assert len(second.actions) == 3
        assert not {a.prospect_id for a in first.actions} & {a.prospect_id for a in second.actions}

    def test_reruns_through_the_repository_never_violate_key_uniqueness(self):
        repo = FakeSalesRepository(CLIENT)
        prospects = [scheduled("fu", FU), new("n-1"), new("n-2")]
        for _ in range(3):
            result = plan(prospects, repo.list_actions())
            repo.save_actions(result.actions)  # would raise on a duplicate idempotency key
        assert len(repo.list_actions()) == 3

    def test_a_prospect_with_an_open_action_from_an_earlier_day_is_not_planned_again(self):
        stale = existing("p1", status=ActionStatus.APPROVED, due=NOW - timedelta(days=2))
        result = plan([scheduled("p1", FU), scheduled("p2", FU)], [stale])
        assert ids(result) == ["p2"]
        assert result.already_counted == 0  # yesterday's leftovers use no quota today

    def test_terminal_actions_from_earlier_days_do_not_block(self):
        old = existing("p1", status=ActionStatus.SENT, due=NOW - timedelta(days=2))
        assert ids(plan([scheduled("p1", FU)], [old])) == ["p1"]

    def test_a_failed_action_today_is_not_silently_replanned(self):
        key = "client-a:p1:follow_up:2026-01-15"
        failed = existing("p1", status=ActionStatus.FAILED, key=key)
        assert plan([scheduled("p1", FU)], [failed]).actions == ()

    def test_a_prospect_gets_at_most_one_outreach_per_day(self):
        sent_today = existing("p1", kind=FU, status=ActionStatus.SENT)
        assert plan([scheduled("p1", CB)], [sent_today]).actions == ()

    def test_duplicate_prospect_entries_produce_one_action(self):
        result = plan([scheduled("p1", FU), scheduled("p1", FU)])
        assert ids(result) == ["p1"]


class TestClientIsolation:
    def test_prospects_of_another_client_are_rejected(self):
        foreign = make_prospect(client_id=ClientId("client-b"))
        with pytest.raises(ValueError, match="client-b"):
            plan([foreign])

    def test_existing_actions_of_another_client_are_rejected(self):
        foreign = make_action(client_id=ClientId("client-b"))
        with pytest.raises(ValueError, match="client-b"):
            plan([], [foreign])


class TestPlanMetadata:
    def test_reports_limit_date_and_prior_usage(self):
        prior = [existing("old-1")]
        result = plan([new("n-1")], prior, daily_limit=7)
        assert result.limit == 7
        assert result.already_counted == 1
        assert result.local_date.isoformat() == "2026-01-15"
