"""An anonymous trace of each decision the agent takes, so a result can be tied to what led to it.

A trace says *how* the agent acted, never *what* it said or to whom: no name, contact, message text
or identifier of a person. People and actions are replaced by an opaque reference
(`anonymous_ref`), a message by its length, its number of questions and the time since the
previous message. Of what the Brain reports about its decision, only a short list of labels is
kept (`TRACE_KEYS`), and only when it is a plain label: free text never gets in.

The sink is told best effort, like the activity reporter: a sink that fails never changes what the
Engine does.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sales_core.contracts import BrainDecision, DecisionType, Direction, Message, SalesActionType

# What a Brain may report about a decision for the trace. Anything else is dropped.
TRACE_KEYS = (
    "objective",
    "selected_product",
    "objection",
    "intent",
    "refusal",
    "stop",
    "follow_up_situation",
    "workflow_step",
    "learned_notes",
)
_LABEL = re.compile(r"^[a-z0-9][a-z0-9_.:\-]{0,59}$")  # `:` joins a rule to its product
_MAX_VALUE = 120
_QUESTION_MARKS = ("?", "؟", "？")  # latin, arabic, full-width


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionTrace:
    client_id: str
    case_ref: str  # the same for every decision about one person; reveals nothing about them
    action_ref: str  # likewise for the action; joins the trace to the message that was sent
    at: datetime
    decision: str
    action_type: str
    channel: str | None = None
    length: int | None = None  # characters of the message, when there is one
    questions: int | None = None
    gap_hours: float | None = None  # since the previous message of either side
    unanswered: int = 0  # messages from the agent since the person last wrote
    answers_reply: bool = False  # this decision answers a message from the person
    labels: Mapping[str, str] = field(default_factory=dict)


class TraceSink(Protocol):
    def record(self, trace: DecisionTrace) -> None: ...


class NoTrace:
    """The sink of a deployment that keeps no trace."""

    def record(self, trace: DecisionTrace) -> None:
        return None


NO_TRACE = NoTrace()


def anonymous_ref(client_id: str, identifier: str) -> str:
    """An opaque, stable reference: the same inputs always give the same value, and the value
    cannot be turned back into the identifier."""
    digest = hashlib.sha256(f"trace:{client_id}:{identifier}".encode()).hexdigest()
    return digest[:24]


def count_questions(text: str) -> int:
    return sum(text.count(mark) for mark in _QUESTION_MARKS)


def clean_labels(metadata: Mapping[str, str]) -> dict[str, str]:
    """The reportable labels of a decision, each a plain lowercase token (or a list of them)."""
    kept: dict[str, str] = {}
    for key in TRACE_KEYS:
        value = metadata.get(key)
        if not value or len(value) > _MAX_VALUE:
            continue
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if parts and all(_LABEL.match(part) for part in parts):
            kept[key] = ",".join(parts)
    return kept


def build_trace(
    *,
    client_id: str,
    prospect_id: str,
    action_id: str,
    action_type: SalesActionType,
    decision: BrainDecision,
    channel: str | None,
    messages: tuple[Message, ...],
    now: datetime,
) -> DecisionTrace:
    text = decision.message.text if decision.message is not None else None
    last = messages[-1] if messages else None
    unanswered = 0
    for message in reversed(messages):
        if message.direction is Direction.INBOUND:
            break
        unanswered += 1
    return DecisionTrace(
        client_id=client_id,
        case_ref=anonymous_ref(client_id, prospect_id),
        action_ref=anonymous_ref(client_id, action_id),
        at=now,
        decision=str(decision.decision_type),
        action_type=str(action_type),
        channel=channel,
        length=len(text) if text is not None else None,
        questions=count_questions(text) if text is not None else None,
        gap_hours=round((now - last.timestamp).total_seconds() / 3600, 1) if last else None,
        unanswered=unanswered,
        answers_reply=action_type is SalesActionType.REPLY,
        labels=clean_labels(decision.metadata),
    )


def sends_a_message(trace: DecisionTrace) -> bool:
    return trace.decision == str(DecisionType.SEND_MESSAGE)
