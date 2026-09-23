"""Pure state transitions: how a Brain decision and a completed send change actions and prospects.

No I/O and no decisions of its own: every value written here comes from the decision, the action
(which carries the Brain's proposal), or the clock.
"""

from dataclasses import replace
from datetime import datetime

from sales_core.contracts import (
    BrainDecision,
    DecisionType,
    Prospect,
    ProspectStatus,
    SalesAction,
    SalesActionType,
)
from sales_core.engine.errors import InvalidDecisionError


def validate_decision(decision: BrainDecision, now: datetime) -> None:
    """Reject decisions that are well-formed but unusable."""
    if decision.next_follow_up_at is not None and decision.next_follow_up_at <= now:
        raise InvalidDecisionError("next_follow_up_at must be in the future")


def clear_next_action(prospect: Prospect) -> Prospect:
    schedule = replace(prospect.schedule, next_action_type=None, next_action_due_at=None)
    return replace(prospect, schedule=schedule)


def with_next_action(
    prospect: Prospect, action_type: SalesActionType | None, due_at: datetime | None
) -> Prospect:
    schedule = replace(prospect.schedule, next_action_type=action_type, next_action_due_at=due_at)
    return replace(prospect, schedule=schedule)


def proposal(action: SalesAction, decision: BrainDecision) -> SalesAction:
    """Store a SEND_MESSAGE decision on the action so it survives approval and restarts."""
    if decision.decision_type is not DecisionType.SEND_MESSAGE or decision.message is None:
        raise ValueError("only SEND_MESSAGE decisions produce a proposal")
    return replace(
        action,
        draft=decision.message,
        next_stage=decision.new_stage,
        next_action_type=decision.next_action_type,
        next_action_due_at=decision.next_follow_up_at,
    )


def apply_without_message(prospect: Prospect, decision: BrainDecision) -> Prospect:
    """Prospect state after a decision that sends nothing (the action then becomes COMPLETED)."""
    stage = decision.new_stage if decision.new_stage is not None else prospect.stage
    match decision.decision_type:
        case DecisionType.SCHEDULE_FOLLOW_UP | DecisionType.NO_ACTION:
            updated = with_next_action(
                prospect, decision.next_action_type, decision.next_follow_up_at
            )
            return replace(updated, stage=stage)
        case DecisionType.HANDOFF_TO_HUMAN:
            return replace(clear_next_action(prospect), status=ProspectStatus.PAUSED, stage=stage)
        case DecisionType.CLOSE:
            return replace(clear_next_action(prospect), status=ProspectStatus.CLOSED, stage=stage)
        case _:
            raise ValueError(f"{decision.decision_type} sends a message")


def after_send(prospect: Prospect, action: SalesAction, sent_at: datetime) -> Prospect:
    """Prospect state once the action's message has been accepted by the channel."""
    schedule = replace(
        prospect.schedule,
        next_action_type=action.next_action_type,
        next_action_due_at=action.next_action_due_at,
        last_contacted_at=sent_at,
        follow_up_count=prospect.schedule.follow_up_count
        + (1 if action.action_type is SalesActionType.FOLLOW_UP else 0),
    )
    status = ProspectStatus.ACTIVE if prospect.status is ProspectStatus.NEW else prospect.status
    stage = action.next_stage if action.next_stage is not None else prospect.stage
    return replace(prospect, status=status, stage=stage, schedule=schedule)
