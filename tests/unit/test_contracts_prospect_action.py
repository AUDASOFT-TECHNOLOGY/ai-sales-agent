from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest
from factories import NAIVE_NOW as NAIVE
from factories import NOW, make_action, make_prospect

from sales_core.contracts import (
    ActionPriority,
    ActionStatus,
    MessageDraft,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)


class TestProspect:
    def test_defaults_describe_a_brand_new_prospect(self):
        prospect = make_prospect()
        assert prospect.status is ProspectStatus.NEW
        assert prospect.stage is None
        assert prospect.conversation_id is None
        assert prospect.schedule == Scheduling()
        assert prospect.attributes == {}

    def test_is_immutable_and_updated_via_replace(self):
        prospect = make_prospect()
        with pytest.raises(FrozenInstanceError):
            prospect.status = ProspectStatus.ACTIVE  # type: ignore[misc]
        updated = replace(prospect, status=ProspectStatus.ACTIVE)
        assert updated.status is ProspectStatus.ACTIVE
        assert prospect.status is ProspectStatus.NEW

    def test_attributes_are_copied_and_read_only(self):
        source = {"budget": "high"}
        prospect = make_prospect(attributes=source)
        source["budget"] = "changed"
        assert prospect.attributes["budget"] == "high"
        with pytest.raises(TypeError):
            prospect.attributes["budget"] = "x"  # type: ignore[index]

    @pytest.mark.parametrize("field", ["client_id", "prospect_id"])
    def test_ids_must_not_be_blank(self, field):
        with pytest.raises(ValueError, match=field):
            make_prospect(**{field: "  "})


class TestScheduling:
    def test_next_action_type_and_due_date_must_come_together(self):
        with pytest.raises(ValueError, match="together"):
            Scheduling(next_action_type=SalesActionType.FOLLOW_UP)
        with pytest.raises(ValueError, match="together"):
            Scheduling(next_action_due_at=NOW)

    def test_complete_schedule_is_valid(self):
        schedule = Scheduling(
            next_action_type=SalesActionType.FOLLOW_UP,
            next_action_due_at=NOW + timedelta(days=2),
            last_contacted_at=NOW,
            follow_up_count=1,
        )
        assert schedule.next_action_type is SalesActionType.FOLLOW_UP

    @pytest.mark.parametrize("field", ["last_contacted_at", "last_inbound_at"])
    def test_naive_datetimes_are_rejected(self, field):
        with pytest.raises(ValueError, match="timezone-aware"):
            Scheduling(**{field: NAIVE})

    def test_naive_due_date_is_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Scheduling(next_action_type=SalesActionType.FOLLOW_UP, next_action_due_at=NAIVE)

    def test_negative_follow_up_count_is_rejected(self):
        with pytest.raises(ValueError, match="follow_up_count"):
            Scheduling(follow_up_count=-1)


class TestSalesAction:
    def test_defaults(self):
        action = make_action()
        assert action.status is ActionStatus.PLANNED
        assert action.priority is ActionPriority.NORMAL
        assert action.draft is None
        assert action.attempts == 0

    def test_priorities_are_ordered(self):
        assert ActionPriority.LOW < ActionPriority.NORMAL < ActionPriority.HIGH
        assert ActionPriority.HIGH < ActionPriority.URGENT

    def test_status_change_creates_a_new_object(self):
        action = make_action()
        proposed = replace(action, status=ActionStatus.PROPOSED, draft=MessageDraft(text="Hi"))
        assert action.status is ActionStatus.PLANNED
        assert proposed.draft == MessageDraft(text="Hi")

    def test_idempotency_key_is_mandatory(self):
        with pytest.raises(ValueError, match="idempotency_key"):
            make_action(idempotency_key="")

    def test_due_at_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="due_at"):
            make_action(due_at=NAIVE)

    def test_attempts_cannot_be_negative(self):
        with pytest.raises(ValueError, match="attempts"):
            make_action(attempts=-1)


def test_enum_values_are_stable_because_they_are_persisted():
    assert {s.value for s in ActionStatus} == {
        "planned", "proposed", "approved", "rejected", "sending", "sent", "completed", "failed",
        "cancelled",
    }  # fmt: skip
    assert {s.value for s in ProspectStatus} == {"new", "active", "paused", "opted_out", "closed"}
    assert {t.value for t in SalesActionType} == {
        "first_contact", "follow_up", "reminder", "callback", "reply",
    }  # fmt: skip
