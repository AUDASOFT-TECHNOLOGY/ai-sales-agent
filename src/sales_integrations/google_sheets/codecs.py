"""Converting between sheet cells and Core contracts. Vendor-free and side-effect free.

Reading is forgiving about how a human typed a value (numbers, dates, case). Writing is strict and
canonical: every value the adapter writes is a plain string, so the API stores it as text.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from sales_core.contracts import (
    ActionPriority,
    ActionStatus,
    AuditEvent,
    Channel,
    ClientId,
    ContactInfo,
    Direction,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    Message,
    MessageDraft,
    Prospect,
    ProspectId,
    ProspectStatus,
    SalesAction,
    SalesActionType,
    Scheduling,
)
from sales_integrations.google_sheets.layout import Row, Table

MAX_CELL_CHARS = 49_000  # Google Sheets rejects cells above 50,000 characters
_EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=UTC)  # how Sheets numbers dates (wall time only)


# ---- cell values ---------------------------------------------------------------------------------


def norm(raw: Any) -> str:
    """A cell value as a comparable string (None -> "", 5.0 -> "5", True -> "TRUE")."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "TRUE" if raw else "FALSE"
    if isinstance(raw, float):
        return str(int(raw)) if raw.is_integer() else format(raw, ".15g")
    return str(raw)


def text_of(raw: Any) -> str:
    return norm(raw).strip()


def digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def parse_datetime(raw: Any, zone: ZoneInfo, what: str) -> datetime | None:
    """ISO text (with or without offset), or a date Sheets stored as a number."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{what}: {raw!r} is not a date/time")  # noqa: TRY004
    if isinstance(raw, int | float):
        naive = _EXCEL_EPOCH + timedelta(days=float(raw), milliseconds=500)
        return naive.replace(microsecond=0, tzinfo=zone)
    text = str(raw).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{what}: {text!r} is not a date/time (use e.g. 2026-01-20 09:00)"
        ) from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=zone)


def format_datetime(value: datetime | None, zone: ZoneInfo) -> str:
    if value is None:
        return ""
    local = value.astimezone(zone)
    return local.isoformat(timespec="seconds" if local.microsecond == 0 else "milliseconds")


def parse_enum[E: Enum](raw: Any, enum: type[E], what: str) -> E | None:
    text = text_of(raw).casefold()
    if not text:
        return None
    for member in enum:
        if text in (str(member.value).casefold(), member.name.casefold()):
            return member
    allowed = ", ".join(str(m.value) for m in enum)
    raise ValueError(f"{what}: {text_of(raw)!r} is not one of: {allowed}")


def parse_int(raw: Any, what: str, default: int = 0) -> int:
    text = text_of(raw)
    if not text:
        return default
    try:
        number = float(text)
    except ValueError:
        raise ValueError(f"{what}: {text!r} is not a whole number") from None
    if not number.is_integer():
        raise ValueError(f"{what}: {text!r} is not a whole number")
    return int(number)


def parse_fraction(raw: Any, what: str) -> float | None:
    text = text_of(raw)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{what}: {text!r} is not a number") from None


def json_out(mapping: Mapping[str, str]) -> str:
    return json.dumps(dict(mapping), ensure_ascii=False, sort_keys=True) if mapping else ""


def json_in(raw: Any, what: str) -> dict[str, str]:
    text = text_of(raw)
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError(f"{what}: not valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError(f"{what}: must be a JSON object")  # noqa: TRY004
    return {str(k): str(v) for k, v in value.items()}


def _cell(name: str, value: str) -> str:
    if len(value) > MAX_CELL_CHARS:
        raise ValueError(
            f"{name} is {len(value)} characters; a cell holds at most {MAX_CELL_CHARS}"
        )
    return value


# ---- prospects -----------------------------------------------------------------------------------


def decode_prospect(table: Table, row: Row, client_id: ClientId, zone: ZoneInfo) -> Prospect:
    def get(name: str) -> Any:
        return table.cell(row, name)

    phone, email = text_of(get("phone")), text_of(get("email"))
    contact = ContactInfo(
        phone=phone or None,
        email=email or None,
        telegram_chat_id=text_of(get("telegram_chat_id")) or None,
        preferred_channel=parse_enum(get("preferred_channel"), Channel, "preferred_channel"),
    )
    schedule = Scheduling(
        next_action_type=parse_enum(get("next_action_type"), SalesActionType, "next_action_type"),
        next_action_due_at=parse_datetime(get("next_action_due_at"), zone, "next_action_due_at"),
        last_contacted_at=parse_datetime(get("last_contacted_at"), zone, "last_contacted_at"),
        last_inbound_at=parse_datetime(get("last_inbound_at"), zone, "last_inbound_at"),
        follow_up_count=parse_int(get("follow_up_count"), "follow_up_count"),
    )
    attributes = {
        header: text_of(row.cells[position])
        for header, position in table.extras.items()
        if position < len(row.cells) and text_of(row.cells[position])
    }
    return Prospect(
        client_id=client_id,
        prospect_id=text_of(get("prospect_id")),  # type: ignore[arg-type]
        display_name=text_of(get("display_name")),
        contact=contact,
        status=parse_enum(get("status"), ProspectStatus, "status") or ProspectStatus.NEW,
        stage=text_of(get("stage")) or None,
        locale=text_of(get("locale")) or None,
        conversation_id=text_of(get("conversation_id")) or None,  # type: ignore[arg-type]
        schedule=schedule,
        attributes=attributes,
    )


def encode_prospect(prospect: Prospect, zone: ZoneInfo) -> dict[str, str]:
    """Every column the adapter may write for a prospect, as canonical strings.

    Keys are canonical field names, or `@<header>` for a client's extra column. `prospect_id` is
    deliberately absent: the adapter never rewrites a prospect's identity.
    """
    schedule = prospect.schedule
    encoded = {
        "display_name": prospect.display_name,
        "phone": prospect.contact.phone or "",
        "email": prospect.contact.email or "",
        "telegram_chat_id": prospect.contact.telegram_chat_id or "",
        "preferred_channel": str(prospect.contact.preferred_channel or ""),
        "locale": prospect.locale or "",
        "status": str(prospect.status),
        "stage": prospect.stage or "",
        "next_action_type": str(schedule.next_action_type or ""),
        "next_action_due_at": format_datetime(schedule.next_action_due_at, zone),
        "conversation_id": prospect.conversation_id or "",
        "last_contacted_at": format_datetime(schedule.last_contacted_at, zone),
        "last_inbound_at": format_datetime(schedule.last_inbound_at, zone),
        "follow_up_count": str(schedule.follow_up_count),
    }
    encoded.update({f"@{header}": value for header, value in prospect.attributes.items()})
    return {key: _cell(key, value) for key, value in encoded.items()}


# ---- actions -------------------------------------------------------------------------------------


def decode_action(table: Table, row: Row, client_id: ClientId, zone: ZoneInfo) -> SalesAction:
    def get(name: str) -> Any:
        return table.cell(row, name)

    def required(name: str) -> str:
        value = text_of(get(name))
        if not value:
            raise ValueError(f"{name}: is empty")
        return value

    priority_text = text_of(get("priority")).casefold()
    priority = ActionPriority.NORMAL
    if priority_text:
        matches = [p for p in ActionPriority if priority_text in (p.name.casefold(), str(p.value))]
        if not matches:
            names = ", ".join(p.name.lower() for p in ActionPriority)
            raise ValueError(f"priority: {priority_text!r} is not one of: {names}")
        priority = matches[0]

    status = parse_enum(get("status"), ActionStatus, "status")
    if status is None:
        raise ValueError("status: is empty")
    action_type = parse_enum(get("action_type"), SalesActionType, "action_type")
    if action_type is None:
        raise ValueError("action_type: is empty")
    due_at = parse_datetime(get("due_at"), zone, "due_at")
    if due_at is None:
        raise ValueError("due_at: is empty")

    draft_text, template_id = norm(get("draft_text")), text_of(get("draft_template_id"))
    draft = None
    if draft_text.strip() or template_id:
        draft = MessageDraft(
            text=draft_text,
            language=text_of(get("draft_language")) or None,
            template_id=template_id or None,
            template_params=json_in(get("draft_template_params"), "draft_template_params"),
        )
    return SalesAction(
        action_id=required("action_id"),  # type: ignore[arg-type]
        client_id=client_id,
        prospect_id=required("prospect_id"),  # type: ignore[arg-type]
        action_type=action_type,
        due_at=due_at,
        idempotency_key=required("idempotency_key"),
        priority=priority,
        status=status,
        draft=draft,
        attempts=parse_int(get("attempts"), "attempts"),
        last_error=norm(get("last_error")).strip() or None,
        next_stage=text_of(get("next_stage")) or None,
        next_action_type=parse_enum(get("next_action_type"), SalesActionType, "next_action_type"),
        next_action_due_at=parse_datetime(get("next_action_due_at"), zone, "next_action_due_at"),
    )


def encode_action(action: SalesAction, zone: ZoneInfo) -> dict[str, str]:
    draft = action.draft
    encoded = {
        "action_id": action.action_id,
        "prospect_id": action.prospect_id,
        "action_type": str(action.action_type),
        "due_at": format_datetime(action.due_at, zone),
        "priority": action.priority.name.lower(),
        "status": str(action.status),
        "idempotency_key": action.idempotency_key,
        "attempts": str(action.attempts),
        "last_error": action.last_error or "",
        "draft_text": draft.text if draft else "",
        "draft_language": (draft.language or "") if draft else "",
        "draft_template_id": (draft.template_id or "") if draft else "",
        "draft_template_params": json_out(draft.template_params) if draft else "",
        "next_stage": action.next_stage or "",
        "next_action_type": str(action.next_action_type or ""),
        "next_action_due_at": format_datetime(action.next_action_due_at, zone),
    }
    return {key: _cell(key, value) for key, value in encoded.items()}


# ---- messages and audit --------------------------------------------------------------------------


def decode_message(table: Table, row: Row, client_id: ClientId, zone: ZoneInfo) -> Message:
    def get(name: str) -> Any:
        return table.cell(row, name)

    def required(name: str) -> str:
        value = text_of(get(name))
        if not value:
            raise ValueError(f"{name}: is empty")
        return value

    direction = parse_enum(get("direction"), Direction, "direction")
    channel = parse_enum(get("channel"), Channel, "channel")
    timestamp = parse_datetime(get("timestamp"), zone, "timestamp")
    if direction is None or channel is None or timestamp is None:
        raise ValueError("direction, channel and timestamp are required")
    return Message(
        message_id=required("message_id"),  # type: ignore[arg-type]
        client_id=client_id,
        prospect_id=required("prospect_id"),  # type: ignore[arg-type]
        conversation_id=required("conversation_id"),  # type: ignore[arg-type]
        direction=direction,
        channel=channel,
        text=norm(get("text")),
        timestamp=timestamp,
        external_id=text_of(get("external_id")) or None,
        metadata=json_in(get("metadata"), "metadata"),
    )


def encode_message(message: Message, zone: ZoneInfo) -> dict[str, str]:
    encoded = {
        "message_id": message.message_id,
        "conversation_id": message.conversation_id,
        "prospect_id": message.prospect_id,
        "direction": str(message.direction),
        "channel": str(message.channel),
        "timestamp": format_datetime(message.timestamp, zone),
        "text": message.text,
        "external_id": message.external_id or "",
        "metadata": json_out(message.metadata),
    }
    return {key: _cell(key, value) for key, value in encoded.items()}


def encode_audit(event: AuditEvent, zone: ZoneInfo) -> dict[str, str]:
    encoded = {
        "timestamp": format_datetime(event.timestamp, zone),
        "event_type": event.event_type,
        "actor": event.actor,
        "prospect_id": event.prospect_id or "",
        "action_id": event.action_id or "",
        "details": json_out(event.details),
    }
    return {key: _cell(key, value) for key, value in encoded.items()}


# ---- facts ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredFact:
    """One row of the Facts tab: the observation and whose it is."""

    fact_id: str
    prospect_id: ProspectId
    fact: Fact


def decode_fact(table: Table, row: Row, zone: ZoneInfo) -> StoredFact:
    """Rebuild an observation; `ValueError` if the row is incomplete or contradicts the contract."""

    def get(name: str) -> Any:
        return table.cell(row, name)

    def required(name: str) -> str:
        value = text_of(get(name))
        if not value:
            raise ValueError(f"{name}: is empty")
        return value

    status = parse_enum(get("fact_status"), FactStatus, "fact_status")
    if status is None:
        raise ValueError("fact_status: is empty")
    recorded_at = parse_datetime(get("recorded_at"), zone, "recorded_at")
    if recorded_at is None:
        raise ValueError("recorded_at: is empty")
    source = parse_enum(get("evidence_source"), FactSource, "evidence_source")
    details = {
        "message_id": text_of(get("evidence_message_id")) or None,
        "excerpt": norm(get("evidence_excerpt")).strip() or None,
        "observed_at": parse_datetime(get("evidence_observed_at"), zone, "evidence_observed_at"),
        "recorded_by": text_of(get("evidence_recorded_by")) or None,
    }
    if source is None and any(v is not None for v in details.values()):
        raise ValueError("evidence_source: is empty but other evidence columns are filled")
    evidence = None if source is None else FactEvidence(source=source, **details)
    fact = Fact(
        key=required("fact_key"),
        value=norm(get("fact_value")),
        status=status,
        confidence=parse_fraction(get("confidence"), "confidence"),
        evidence=evidence,
        recorded_at=recorded_at,
        updated_at=parse_datetime(get("updated_at"), zone, "updated_at"),
    )
    return StoredFact(required("fact_id"), ProspectId(required("prospect_id")), fact)


def encode_fact(
    fact_id: str, client_id: ClientId, prospect_id: ProspectId, fact: Fact, zone: ZoneInfo
) -> dict[str, str]:
    evidence = fact.evidence
    encoded = {
        "fact_id": fact_id,
        "client_id": client_id,
        "prospect_id": prospect_id,
        "fact_key": fact.key,
        "fact_value": fact.value,
        "fact_status": str(fact.status),
        "confidence": "" if fact.confidence is None else norm(float(fact.confidence)),
        "evidence_source": str(evidence.source) if evidence else "",
        "evidence_message_id": (evidence.message_id or "") if evidence else "",
        "evidence_excerpt": (evidence.excerpt or "") if evidence else "",
        "evidence_observed_at": format_datetime(evidence.observed_at, zone) if evidence else "",
        "evidence_recorded_by": (evidence.recorded_by or "") if evidence else "",
        "recorded_at": format_datetime(fact.recorded_at, zone),
        "updated_at": format_datetime(fact.updated_at, zone),
    }
    return {key: _cell(key, value) for key, value in encoded.items()}


# ---- rows ----------------------------------------------------------------------------------------


def row_raw(table: Table, row: Row) -> dict[str, str]:
    """Every known cell of a row as comparable strings, keyed like the encoders' output."""
    raw = {name: norm(table.cell(row, name)) for name in table.index}
    for header, position in table.extras.items():
        raw[f"@{header}"] = norm(row.cells[position]) if position < len(row.cells) else ""
    return raw


def row_values(table: Table, encoded: Mapping[str, str]) -> list[str]:
    """A full row for a new record, laid out by the sheet's own column order."""
    by_position = {position: key for key, position in table.index.items()}
    by_position.update({position: f"@{header}" for header, position in table.extras.items()})
    return [
        encoded.get(by_position.get(position, ""), "") for position in range(len(table.headers))
    ]


# ---- targeted writes ------------------------------------------------------------------------------

MIRRORED_KEYS = frozenset(
    {
        "status",
        "last_contacted_at",
        "last_inbound_at",
        "follow_up_count",
        "next_action_type",
        "next_action_due_at",
    }
)
_MOMENT_KEYS = frozenset({"last_contacted_at", "last_inbound_at", "next_action_due_at"})


def _count(raw: Any) -> int:
    """A count as `decode_prospect` reads it: an empty cell is 0."""
    return parse_int(raw, "follow_up_count")


def same_cell(key: str, raw: Any, text: str, zone: ZoneInfo) -> bool:
    """Whether a cell holds the value `text` stands for, however a person typed it.

    `raw` is the cell as the API gave it (a number stays a number), `text` the canonical form the
    adapter writes. A cell that cannot be interpreted is never "the same": the caller then refuses
    to write over it.
    """
    try:
        if key in _MOMENT_KEYS:
            return parse_datetime(raw, zone, key) == parse_datetime(text, zone, key)
        if key == "follow_up_count":
            return _count(raw) == _count(text)
        if key == "status":
            return (parse_enum(raw, ProspectStatus, key) or ProspectStatus.NEW) == (
                parse_enum(text, ProspectStatus, key) or ProspectStatus.NEW
            )
        if key == "next_action_type":
            return parse_enum(raw, SalesActionType, key) == parse_enum(text, SalesActionType, key)
    except ValueError:
        return False
    return text_of(raw) == text_of(text)


def mirrored_values(prospect: Prospect) -> dict[str, Any]:
    """What a decoded row holds in the columns Revansa mirrors, as typed values."""
    schedule = prospect.schedule
    return {
        "last_contacted_at": schedule.last_contacted_at,
        "last_inbound_at": schedule.last_inbound_at,
        "follow_up_count": schedule.follow_up_count,
        "next_action": (schedule.next_action_type, schedule.next_action_due_at),
    }
