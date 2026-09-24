"""What the conversation so far says about how to continue: who spoke last, how much was asked.

Read from message directions and punctuation only. It reads no meaning and no language.
"""

import re
from dataclasses import dataclass

from sales_core.contracts import Conversation, Direction, Message

# Question mark code points (U+003F, U+FF1F, U+061F). A run counts once ("???" is one).
_QUESTION = re.compile(r"[?\uff1f\u061f]+")


def count_questions(text: str) -> int:
    return len(_QUESTION.findall(text))


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryView:
    messages: tuple[Message, ...]
    first_direction: Direction | None  # who spoke first in the history we can see
    last_inbound: Message | None
    last_outbound: Message | None
    unanswered: int  # our messages since the prospect last wrote
    outbound_count: int
    questions_asked: int  # our messages that contained a question
    consecutive_questions: int  # our latest messages in a row that contained a question

    @property
    def has_prior_outbound(self) -> bool:
        return self.outbound_count > 0

    @property
    def awaiting_reply(self) -> bool:
        """We spoke last: whatever comes next is not an answer to the prospect."""
        return self.unanswered > 0


def view_history(conversation: Conversation) -> HistoryView:
    messages = conversation.messages
    outbound = [m for m in messages if m.direction is Direction.OUTBOUND]
    inbound = [m for m in messages if m.direction is Direction.INBOUND]

    unanswered = 0
    for message in reversed(messages):
        if message.direction is Direction.INBOUND:
            break
        unanswered += 1

    consecutive = 0
    for message in reversed(outbound):
        if count_questions(message.text) == 0:
            break
        consecutive += 1

    return HistoryView(
        messages=messages,
        first_direction=messages[0].direction if messages else None,
        last_inbound=inbound[-1] if inbound else None,
        last_outbound=outbound[-1] if outbound else None,
        unanswered=unanswered,
        outbound_count=len(outbound),
        questions_asked=sum(1 for m in outbound if count_questions(m.text) > 0),
        consecutive_questions=consecutive,
    )
