"""The result of a conversation, derived from what the Core already knows.

    achieved     the goal was reached: a payment a person confirmed, or the stage the client marked as
                 the goal. Final as soon as it is seen.
    declined     the person said no, asked to stop, or the conversation was closed. Final.
    uneasy       the person was bothered: they showed impatience, or stopped us after we wrote
                 several times without an answer. Final.
    handed_over  a person took the conversation (or it waits for one). Kept apart: what a human did
                 is not what the agent did, so it never counts for or against the agent.
    quiet        nothing since `quiet_after`. Not a refusal and never read as one: it only means
                 there is no result to learn from.
    open         still going.

A rehearsal (a test person the client asked the agent to write to) has no result: it never teaches.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sales_core.contracts import Conversation, Direction, ProspectStatus

DEFAULT_QUIET_AFTER = timedelta(days=14)
# Unanswered messages in a row after which a stop request reads as discomfort, not as a plain no.
INSISTENCE = 2
TEST_ID_PREFIX = "inbound-test-"  # same prefix as the Engine's test people


class Result(StrEnum):
    ACHIEVED = "achieved"
    DECLINED = "declined"
    UNEASY = "uneasy"
    HANDED_OVER = "handed_over"
    QUIET = "quiet"
    OPEN = "open"

    @property
    def is_final(self) -> bool:
        return self in {Result.ACHIEVED, Result.DECLINED, Result.UNEASY}

    @property
    def is_unfavourable(self) -> bool:
        return self in {Result.DECLINED, Result.UNEASY}


def is_rehearsal(prospect_id: str) -> bool:
    return prospect_id.startswith(TEST_ID_PREFIX)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationSignals:
    """What is known about one conversation besides its messages. Each field comes from existing
    data; none is guessed."""

    status: ProspectStatus
    stage: str | None = None
    paid: bool = False  # a person authorised by the client confirmed a payment
    with_a_person: bool = False  # a person took it, or it waits for one
    impatient: bool = False  # the prospect's latest message showed impatience or annoyance
    refused: bool = False  # the prospect's latest message was an explicit refusal


def resolve_result(
    prospect_id: str,
    conversation: Conversation,
    signals: ConversationSignals,
    *,
    achieved_stages: frozenset[str],
    now: datetime,
    quiet_after: timedelta = DEFAULT_QUIET_AFTER,
) -> Result | None:
    """The result now, or None when the conversation must not teach (a rehearsal, or no message
    from the agent yet)."""
    if is_rehearsal(prospect_id):
        return None
    messages = conversation.messages
    if not any(m.direction is Direction.OUTBOUND for m in messages):
        return None
    if signals.paid or (signals.stage is not None and signals.stage in achieved_stages):
        return Result.ACHIEVED
    if signals.with_a_person or signals.status is ProspectStatus.PAUSED:
        return Result.HANDED_OVER
    if signals.status is ProspectStatus.OPTED_OUT:
        silent_run = _unanswered_run(conversation)
        # A stop request that follows several unanswered messages, or came with annoyance, is
        # discomfort. Anything else is a plain no.
        if signals.impatient or silent_run >= INSISTENCE:
            return Result.UNEASY
        return Result.DECLINED
    if signals.status is ProspectStatus.CLOSED or signals.refused:
        return Result.UNEASY if signals.impatient else Result.DECLINED
    if signals.impatient:
        return Result.UNEASY
    last = messages[-1]
    if now - last.timestamp >= quiet_after:
        return Result.QUIET
    return Result.OPEN


def _unanswered_run(conversation: Conversation) -> int:
    """Messages from the agent since the prospect last wrote, not counting a final inbound stop."""
    run = 0
    for message in reversed(conversation.messages):
        if message.direction is Direction.OUTBOUND:
            run += 1
        elif run:
            break
    return run
