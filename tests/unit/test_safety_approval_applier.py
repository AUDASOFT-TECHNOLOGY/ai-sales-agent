"""Pure units used by the Engine: safety checks, approval policy, state transitions."""

from dataclasses import replace
from datetime import timedelta

import pytest
from engine_harness import prospect
from factories import NOW, make_action

from sales_core.approval import ApprovalMode, status_after_proposal
from sales_core.contracts import (
    ActionStatus,
    BrainDecision,
    Channel,
    ContactInfo,
    DecisionType,
    MessageDraft,
    ProspectStatus,
    SalesActionType,
)
from sales_core.engine import InvalidDecisionError
from sales_core.engine.applier import (
    after_send,
    apply_without_message,
    proposal,
    validate_decision,
)
from sales_core.safety import (
    DEFAULT_OPT_OUT_KEYWORDS,
    BlockReason,
    check_contactable,
    check_ready_to_send,
    is_opt_out_message,
    recipient_for,
)


class TestSafety:
    @pytest.mark.parametrize("status", [ProspectStatus.NEW, ProspectStatus.ACTIVE])
    def test_new_and_active_prospects_with_an_address_are_contactable(self, status):
        assert check_contactable(prospect("p1", status=status), Channel.WHATSAPP).allowed

    @pytest.mark.parametrize(
        ("status", "reason"),
        [
            (ProspectStatus.OPTED_OUT, BlockReason.OPTED_OUT),
            (ProspectStatus.PAUSED, BlockReason.PAUSED),
            (ProspectStatus.CLOSED, BlockReason.CLOSED),
        ],
    )
    def test_blocked_statuses(self, status, reason):
        assert check_contactable(prospect("p1", status=status), Channel.WHATSAPP).reason is reason

    def test_unknown_prospect_fails_closed(self):
        assert check_contactable(None, Channel.WHATSAPP).reason is BlockReason.UNKNOWN_PROSPECT

    def test_address_must_exist_for_the_channel(self):
        phone_only = prospect("p1", contact=ContactInfo(phone="+15550000001"))
        assert check_contactable(phone_only, Channel.EMAIL).reason is BlockReason.NO_CONTACT
        assert recipient_for(phone_only, Channel.WHATSAPP) == "+15550000001"
        assert recipient_for(phone_only, Channel.EMAIL) is None
        blank = prospect("p1", contact=ContactInfo(phone="  "))
        assert check_contactable(blank, Channel.SMS).reason is BlockReason.NO_CONTACT

    def test_ready_to_send_requires_a_draft_and_a_matching_client(self):
        p = prospect("p1")
        assert (
            check_ready_to_send(p, make_action(), Channel.WHATSAPP).reason is BlockReason.NO_DRAFT
        )
        with_draft = make_action(draft=MessageDraft(text="Hi"))
        assert check_ready_to_send(p, with_draft, Channel.WHATSAPP).allowed
        assert (
            check_ready_to_send(p, replace(with_draft, client_id="other"), Channel.WHATSAPP).reason
            is BlockReason.CLIENT_MISMATCH
        )

    @pytest.mark.parametrize("text", ["stop", "STOP", " Stop ", "stop!", "...stop..."])
    def test_opt_out_messages(self, text):
        assert is_opt_out_message(text, DEFAULT_OPT_OUT_KEYWORDS)

    @pytest.mark.parametrize(
        "text", ["", "   ", "stop by tomorrow", "nonstop", "yes", "STOP ME NOT"]
    )
    def test_non_opt_out_messages(self, text):
        assert not is_opt_out_message(text, DEFAULT_OPT_OUT_KEYWORDS)

    def test_custom_keywords_are_case_insensitive(self):
        assert is_opt_out_message("Désabonner", {"désabonner"})


class TestApprovalPolicy:
    def test_modes_map_to_the_status_of_a_new_proposal(self):
        assert status_after_proposal(ApprovalMode.APPROVAL) is ActionStatus.PROPOSED
        assert status_after_proposal(ApprovalMode.AUTONOMOUS) is ActionStatus.APPROVED

    def test_mode_values_match_client_config(self):
        assert {m.value for m in ApprovalMode} == {"approval", "autonomous", "dry_run"}


class TestApplier:
    def test_proposal_copies_the_decision_onto_the_action(self):
        decision = BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE,
            message=MessageDraft(text="Hi"),
            next_follow_up_at=NOW + timedelta(days=2),
            next_action_type=SalesActionType.CALLBACK,
            new_stage="s2",
        )
        action = proposal(make_action(), decision)
        assert action.draft == MessageDraft(text="Hi")
        assert action.next_stage == "s2"
        assert action.next_action_type is SalesActionType.CALLBACK
        assert action.next_action_due_at == NOW + timedelta(days=2)

    def test_only_send_message_makes_a_proposal(self):
        with pytest.raises(ValueError):
            proposal(make_action(), BrainDecision(decision_type=DecisionType.NO_ACTION))

    def test_send_message_is_not_a_message_less_decision(self):
        decision = BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE, message=MessageDraft(text="Hi")
        )
        with pytest.raises(ValueError):
            apply_without_message(prospect("p1"), decision)

    def test_stage_is_kept_when_the_decision_gives_none(self):
        p = replace(prospect("p1"), stage="existing")
        out = apply_without_message(p, BrainDecision(decision_type=DecisionType.CLOSE))
        assert out.stage == "existing" and out.status is ProspectStatus.CLOSED

    def test_only_follow_ups_increment_the_follow_up_counter(self):
        p = prospect("p1")
        follow_up = after_send(p, make_action(action_type=SalesActionType.FOLLOW_UP), NOW)
        reminder = after_send(p, make_action(action_type=SalesActionType.REMINDER), NOW)
        first = after_send(p, make_action(action_type=SalesActionType.FIRST_CONTACT), NOW)
        assert [x.schedule.follow_up_count for x in (follow_up, reminder, first)] == [1, 0, 0]

    def test_after_send_keeps_the_stage_when_the_proposal_has_none(self):
        p = replace(prospect("p1"), stage="kept")
        assert after_send(p, make_action(), NOW).stage == "kept"

    def test_validate_decision_rejects_past_dates(self):
        past = BrainDecision(
            decision_type=DecisionType.SCHEDULE_FOLLOW_UP, next_follow_up_at=NOW - timedelta(days=1)
        )
        with pytest.raises(InvalidDecisionError):
            validate_decision(past, NOW)
        validate_decision(replace(past, next_follow_up_at=NOW + timedelta(seconds=1)), NOW)
