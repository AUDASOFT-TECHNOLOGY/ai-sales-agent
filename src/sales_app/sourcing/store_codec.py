"""The Core's records as JSON documents, and back. Pure: no I/O, no SQL.

Used by the SQL operational store (`sql_store`). A document holds exactly what the contract object
holds, with timestamps as ISO 8601 text (always with their offset) and enums by value, so it reads
back equal to what was written. A document that cannot be read back raises `ValueError`, which the
store reports as a fixed sentence without repeating the document.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sales_app.sourcing.ports import ProspectState
from sales_core.contracts import (
    ActionPriority,
    ActionStatus,
    AuditEvent,
    Channel,
    ContactInfo,
    Direction,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    Message,
    MessageDraft,
    Prospect,
    ProspectStatus,
    SalesAction,
    SalesActionType,
    Scheduling,
)


def _when(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _read_when(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _opt[T](value: Any, build) -> T | None:
    return None if value is None else build(value)


# ---- scheduling, contact, prospect -------------------------------------------------------------


def scheduling_to_doc(value: Scheduling) -> dict[str, Any]:
    return {
        "next_action_type": _opt(value.next_action_type, str),
        "next_action_due_at": _when(value.next_action_due_at),
        "last_contacted_at": _when(value.last_contacted_at),
        "last_inbound_at": _when(value.last_inbound_at),
        "follow_up_count": value.follow_up_count,
    }


def scheduling_from_doc(doc: Mapping[str, Any]) -> Scheduling:
    return Scheduling(
        next_action_type=_opt(doc["next_action_type"], SalesActionType),
        next_action_due_at=_read_when(doc["next_action_due_at"]),
        last_contacted_at=_read_when(doc["last_contacted_at"]),
        last_inbound_at=_read_when(doc["last_inbound_at"]),
        follow_up_count=doc["follow_up_count"],
    )


def prospect_to_doc(value: Prospect) -> dict[str, Any]:
    return {
        "client_id": value.client_id,
        "prospect_id": value.prospect_id,
        "display_name": value.display_name,
        "contact": {
            "phone": value.contact.phone,
            "email": value.contact.email,
            "telegram_chat_id": value.contact.telegram_chat_id,
            "preferred_channel": _opt(value.contact.preferred_channel, str),
        },
        "status": str(value.status),
        "stage": value.stage,
        "locale": value.locale,
        "conversation_id": value.conversation_id,
        "schedule": scheduling_to_doc(value.schedule),
        "attributes": dict(value.attributes),
        "lane_id": value.lane_id,
    }


def prospect_from_doc(doc: Mapping[str, Any]) -> Prospect:
    contact = doc["contact"]
    return Prospect(
        client_id=doc["client_id"],
        prospect_id=doc["prospect_id"],
        display_name=doc["display_name"],
        contact=ContactInfo(
            phone=contact["phone"],
            email=contact["email"],
            telegram_chat_id=contact.get("telegram_chat_id"),  # absent from older documents
            preferred_channel=_opt(contact["preferred_channel"], Channel),
        ),
        status=ProspectStatus(doc["status"]),
        stage=doc["stage"],
        locale=doc["locale"],
        conversation_id=doc["conversation_id"],
        schedule=scheduling_from_doc(doc["schedule"]),
        attributes=doc["attributes"],
        lane_id=doc.get("lane_id"),  # absent from older documents
    )


# ---- Revansa's state for a prospect ------------------------------------------------------------


def state_to_doc(value: ProspectState) -> dict[str, Any]:
    """Everything but the revision, which the store keeps in its own column."""
    return {
        "status": _opt(value.status, str),
        "stage": value.stage,
        "schedule": scheduling_to_doc(value.schedule),
        "conversation_id": value.conversation_id,
        "telegram_chat_id": value.telegram_chat_id,
        "lane_id": value.lane_id,
        "status_reason": value.status_reason,
        "status_changed_at": _when(value.status_changed_at),
    }


def state_from_doc(
    client_id: str, prospect_id: str, revision: int, doc: Mapping[str, Any]
) -> ProspectState:
    return ProspectState(
        client_id=client_id,
        prospect_id=prospect_id,
        status=_opt(doc["status"], ProspectStatus),
        stage=doc["stage"],
        schedule=scheduling_from_doc(doc["schedule"]),
        conversation_id=doc["conversation_id"],
        telegram_chat_id=doc.get("telegram_chat_id"),  # absent from older documents
        lane_id=doc.get("lane_id"),  # absent from older documents
        status_reason=doc["status_reason"],
        status_changed_at=_read_when(doc["status_changed_at"]),
        revision=revision,
    )


# ---- actions, messages, audit, facts -----------------------------------------------------------


def action_to_doc(value: SalesAction) -> dict[str, Any]:
    draft = value.draft
    return {
        "action_id": value.action_id,
        "client_id": value.client_id,
        "prospect_id": value.prospect_id,
        "action_type": str(value.action_type),
        "due_at": _when(value.due_at),
        "idempotency_key": value.idempotency_key,
        "priority": int(value.priority),
        "status": str(value.status),
        "draft": None
        if draft is None
        else {
            "text": draft.text,
            "language": draft.language,
            "template_id": draft.template_id,
            "template_params": dict(draft.template_params),
        },
        "attempts": value.attempts,
        "last_error": value.last_error,
        "next_stage": value.next_stage,
        "next_action_type": _opt(value.next_action_type, str),
        "next_action_due_at": _when(value.next_action_due_at),
        "lane_id": value.lane_id,
        "channel": _opt(value.channel, str),
    }


def action_from_doc(doc: Mapping[str, Any]) -> SalesAction:
    draft = doc["draft"]
    return SalesAction(
        action_id=doc["action_id"],
        client_id=doc["client_id"],
        prospect_id=doc["prospect_id"],
        action_type=SalesActionType(doc["action_type"]),
        due_at=datetime.fromisoformat(doc["due_at"]),
        idempotency_key=doc["idempotency_key"],
        priority=ActionPriority(doc["priority"]),
        status=ActionStatus(doc["status"]),
        draft=None
        if draft is None
        else MessageDraft(
            text=draft["text"],
            language=draft["language"],
            template_id=draft["template_id"],
            template_params=draft["template_params"],
        ),
        attempts=doc["attempts"],
        last_error=doc["last_error"],
        next_stage=doc["next_stage"],
        next_action_type=_opt(doc["next_action_type"], SalesActionType),
        next_action_due_at=_read_when(doc["next_action_due_at"]),
        lane_id=doc.get("lane_id"),  # absent from older documents
        channel=_opt(doc.get("channel"), Channel),
    )


def message_to_doc(value: Message) -> dict[str, Any]:
    return {
        "message_id": value.message_id,
        "client_id": value.client_id,
        "prospect_id": value.prospect_id,
        "conversation_id": value.conversation_id,
        "direction": str(value.direction),
        "channel": str(value.channel),
        "text": value.text,
        "timestamp": _when(value.timestamp),
        "external_id": value.external_id,
        "metadata": dict(value.metadata),
    }


def message_from_doc(doc: Mapping[str, Any]) -> Message:
    return Message(
        message_id=doc["message_id"],
        client_id=doc["client_id"],
        prospect_id=doc["prospect_id"],
        conversation_id=doc["conversation_id"],
        direction=Direction(doc["direction"]),
        channel=Channel(doc["channel"]),
        text=doc["text"],
        timestamp=datetime.fromisoformat(doc["timestamp"]),
        external_id=doc["external_id"],
        metadata=doc["metadata"],
    )


def audit_to_doc(value: AuditEvent) -> dict[str, Any]:
    return {
        "timestamp": _when(value.timestamp),
        "client_id": value.client_id,
        "event_type": value.event_type,
        "actor": value.actor,
        "prospect_id": value.prospect_id,
        "action_id": value.action_id,
        "details": dict(value.details),
    }


def audit_from_doc(doc: Mapping[str, Any]) -> AuditEvent:
    return AuditEvent(
        timestamp=datetime.fromisoformat(doc["timestamp"]),
        client_id=doc["client_id"],
        event_type=doc["event_type"],
        actor=doc["actor"],
        prospect_id=doc["prospect_id"],
        action_id=doc["action_id"],
        details=doc["details"],
    )


def fact_to_doc(value: Fact) -> dict[str, Any]:
    evidence = value.evidence
    return {
        "key": value.key,
        "value": value.value,
        "status": str(value.status),
        "confidence": value.confidence,
        "evidence": None
        if evidence is None
        else {
            "source": str(evidence.source),
            "message_id": evidence.message_id,
            "excerpt": evidence.excerpt,
            "observed_at": _when(evidence.observed_at),
            "recorded_by": evidence.recorded_by,
        },
        "recorded_at": _when(value.recorded_at),
        "updated_at": _when(value.updated_at),
    }


def fact_from_doc(doc: Mapping[str, Any]) -> Fact:
    evidence = doc["evidence"]
    return Fact(
        key=doc["key"],
        value=doc["value"],
        status=FactStatus(doc["status"]),
        confidence=doc["confidence"],
        evidence=None
        if evidence is None
        else FactEvidence(
            source=FactSource(evidence["source"]),
            message_id=evidence["message_id"],
            excerpt=evidence["excerpt"],
            observed_at=_read_when(evidence["observed_at"]),
            recorded_by=evidence["recorded_by"],
        ),
        recorded_at=_read_when(doc["recorded_at"]),
        updated_at=_read_when(doc["updated_at"]),
    )
