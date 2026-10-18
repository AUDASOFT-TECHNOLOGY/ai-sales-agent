"""Figures for a client's periodic report, computed from its stored prospects and conversations.

Pure functions in the style of `kpi.py`: no I/O, no client, no vendor. The app layer only passes the
prospects and a way to read their conversations, and gets neutral numbers back. What the platform
does not track (purchase intentions, appointments, conversions) is not computed here at all.
"""

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from sales_core.contracts import (
    Channel,
    Conversation,
    Direction,
    MessageDraft,
    OutboundMessage,
    Prospect,
    ProspectStatus,
    SalesActionType,
)

_ACTIVE = (ProspectStatus.NEW, ProspectStatus.ACTIVE)
_SCHEDULABLE = (SalesActionType.FOLLOW_UP, SalesActionType.REMINDER, SalesActionType.CALLBACK)
REPORT_PROSPECT_ID = "daily-report"


def activity_figures(
    prospects: Iterable[Prospect],
    conversation_of: Callable[[str], Conversation | None],
    *,
    start: datetime,
    end: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Counts over `[start, end)`: our messages (new contact = first message of a conversation,
    answer = right after the prospect's, follow-up = right after our own), replies received, and
    the state of the pipeline now (by status and stage, follow-ups scheduled, people waiting)."""
    by_status = {s.value: 0 for s in ProspectStatus}
    by_stage: dict[str, int] = {}
    contacted: set[str] = set()
    replied: set[str] = set()
    total = sent = new = follow_ups = answers = received = scheduled = soon = 0
    for prospect in prospects:
        total += 1
        by_status[prospect.status.value] += 1
        if prospect.stage:
            by_stage[prospect.stage] = by_stage.get(prospect.stage, 0) + 1
        due = prospect.schedule.next_action_due_at
        if (
            prospect.status in _ACTIVE
            and prospect.schedule.next_action_type in _SCHEDULABLE
            and due is not None
        ):
            scheduled += 1
            if due < now + timedelta(hours=24):
                soon += 1
        conversation = conversation_of(prospect.prospect_id)
        previous = None
        for message in sorted(
            conversation.messages if conversation else (), key=lambda m: m.timestamp
        ):
            if start <= message.timestamp < end:
                if message.direction is Direction.OUTBOUND:
                    sent += 1
                    contacted.add(prospect.prospect_id)
                    if previous is None:
                        new += 1
                    elif previous is Direction.INBOUND:
                        answers += 1
                    else:
                        follow_ups += 1
                else:
                    received += 1
                    replied.add(prospect.prospect_id)
            previous = message.direction
    stages = dict(sorted(by_stage.items(), key=lambda kv: (-kv[1], kv[0]))[:20])
    return {
        "prospects": {"total": total, "by_status": by_status, "by_stage": stages},
        "activity": {
            "messages_sent": sent,
            "prospects_contacted": len(contacted),
            "new_contacts": new,
            "follow_ups_sent": follow_ups,
            "answers_sent": answers,
            "replies_received": received,
            "prospects_replied": len(replied),
        },
        "follow_ups": {"scheduled": scheduled, "due_next_24h": soon},
        "needs_human": by_status[ProspectStatus.PAUSED.value],
    }


def report_message(
    *,
    client_id: str,
    channel: str,
    recipient: str,
    text: str,
    language: str | None,
    idempotency_key: str,
) -> OutboundMessage:
    """The report as a message for a channel. It is addressed to the client, never to a prospect."""
    return OutboundMessage(
        client_id=client_id,
        prospect_id=REPORT_PROSPECT_ID,
        channel=Channel(channel),
        recipient=recipient,
        draft=MessageDraft(text=text, language=language),
        idempotency_key=idempotency_key,
    )
