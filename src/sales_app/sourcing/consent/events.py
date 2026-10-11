"""The events of the consent registry, and the chain that makes them tamper-evident.

Each event carries the signature of the one before it, and its own signature covers every field, so
changing, deleting, reordering or inserting an event breaks the chain. The first event links back to
a value only the key holder can compute. A contact appears only as a keyed digest (see `keys`).

What this protects against, and what it does not: it reveals edits made to the log by anyone who
does not hold the key, including someone with write access to the file or the spreadsheet it lives
in. It does **not** protect against an attacker able to restore, together and consistently, both the
log and the secret store that holds the key and the anchor: that attacker can roll the registry back
to an earlier valid state without being noticed.
"""

import hmac
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sales_app.sourcing.consent.errors import ConsentFormatError
from sales_app.sourcing.consent.keys import TOKEN, ConsentKeys


class EventType(StrEnum):
    OPT_OUT = "opt_out"
    RELEASE = "release"


class Origin(StrEnum):
    INBOUND_KEYWORD = "inbound_keyword"  # the contact's own message
    SOURCE = "source"  # a do-not-contact signal in the client's CRM or sheet
    OPERATOR = "operator"  # recorded by a person through the operator commands
    IMPORT = "import"


class ReasonCode(StrEnum):
    KEYWORD = "keyword"
    SOURCE_SIGNAL = "source_signal"
    OPERATOR_REQUEST = "operator_request"
    IMPORTED = "imported"
    WRITTEN_CONSENT = "written_consent"
    REGISTRATION_ERROR = "registration_error"
    OTHER = "other"


OPT_OUT_REASONS = frozenset(
    {
        ReasonCode.KEYWORD,
        ReasonCode.SOURCE_SIGNAL,
        ReasonCode.OPERATOR_REQUEST,
        ReasonCode.IMPORTED,
        ReasonCode.OTHER,
    }
)
RELEASE_REASONS = frozenset(
    {ReasonCode.WRITTEN_CONSENT, ReasonCode.REGISTRATION_ERROR, ReasonCode.OTHER}
)

EVENT_ID = re.compile(r"[0-9a-f]{32}")
ACTOR = re.compile(r"[A-Za-z0-9_.:@-]{1,64}")
EVIDENCE = re.compile(r"[A-Za-z0-9 ._:/#-]{0,80}")
_FIELDS = (
    "seq",
    "event_id",
    "key",
    "type",
    "origin",
    "at",
    "actor",
    "reason",
    "evidence_ref",
    "prev_mac",
    "mac",
)


def evidence_problem(value: object) -> str | None:
    """Why a reference is not acceptable, or None. Never repeats it."""
    if not isinstance(value, str) or not EVIDENCE.fullmatch(value):
        return "the reference has a character outside letters, digits, space and . _ : / # -"
    return None


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsentEvent:
    seq: int
    event_id: str
    key: str  # the contact's digest, never the contact
    type: EventType
    origin: Origin
    at: datetime
    actor: str
    reason: ReasonCode
    evidence_ref: str
    prev_mac: str
    mac: str

    def __post_init__(self) -> None:
        if not isinstance(self.seq, int) or isinstance(self.seq, bool) or self.seq < 1:
            raise ConsentFormatError("an event needs a sequence number of 1 or more")
        for name, pattern in (
            ("event_id", EVENT_ID),
            ("key", TOKEN),
            ("actor", ACTOR),
            ("prev_mac", TOKEN),
            ("mac", TOKEN),
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise ConsentFormatError(f"an event field is malformed: {name}")
        if evidence_problem(self.evidence_ref) is not None:
            raise ConsentFormatError("an event field is malformed: evidence_ref")
        if not isinstance(self.at, datetime) or self.at.tzinfo is None:
            raise ConsentFormatError("an event time must carry a time zone")
        if self.type is EventType.OPT_OUT and self.reason not in OPT_OUT_REASONS:
            raise ConsentFormatError("that reason cannot describe an opt-out")
        if self.type is EventType.RELEASE and self.reason not in RELEASE_REASONS:
            raise ConsentFormatError("that reason cannot describe a release")


def _signed_text(fields: dict[str, str]) -> bytes:
    return "\n".join(f"{name}={fields[name]}" for name in _FIELDS if name != "mac").encode("utf-8")


def _fields(event: ConsentEvent) -> dict[str, str]:
    return {
        "seq": str(event.seq),
        "event_id": event.event_id,
        "key": event.key,
        "type": event.type.value,
        "origin": event.origin.value,
        "at": _stamp(event.at),
        "actor": event.actor,
        "reason": event.reason.value,
        "evidence_ref": event.evidence_ref,
        "prev_mac": event.prev_mac,
        "mac": event.mac,
    }


def sign_event(
    keys: ConsentKeys,
    *,
    seq: int,
    key: str,
    type: EventType,
    origin: Origin,
    at: datetime,
    actor: str,
    reason: ReasonCode,
    evidence_ref: str = "",
    prev_mac: str,
    event_id: str | None = None,
) -> ConsentEvent:
    unsigned = ConsentEvent(
        seq=seq,
        event_id=event_id or uuid.uuid4().hex,
        key=key,
        type=type,
        origin=origin,
        at=at,
        actor=actor,
        reason=reason,
        evidence_ref=evidence_ref,
        prev_mac=prev_mac,
        mac=keys.genesis_mac(),  # a placeholder of the right shape, replaced just below
    )
    mac = keys.sign(_signed_text(_fields(unsigned)))
    return ConsentEvent(**{**{f: getattr(unsigned, f) for f in _FIELDS}, "mac": mac})


def to_line(event: ConsentEvent) -> str:
    """One JSON line. The sequence number is a JSON number here; in the signed text it is its
    decimal digits, which is the same value written once."""
    return json.dumps({**_fields(event), "seq": event.seq}, sort_keys=True, separators=(",", ":"))


def from_line(line: str) -> ConsentEvent:
    """Strict: exactly the known fields, each of the right type and shape."""
    try:
        data = json.loads(line)
    except ValueError:
        raise ConsentFormatError("a log line is not valid JSON") from None
    if not isinstance(data, dict) or set(data) != set(_FIELDS):
        raise ConsentFormatError("a log line does not have exactly the expected fields")
    if not all(isinstance(data[name], str) for name in _FIELDS if name != "seq"):
        raise ConsentFormatError("a log line has a field of the wrong type")
    try:
        at = datetime.fromisoformat(data["at"])
        event = ConsentEvent(
            seq=data["seq"] if isinstance(data["seq"], int) else -1,
            event_id=data["event_id"],
            key=data["key"],
            type=EventType(data["type"]),
            origin=Origin(data["origin"]),
            at=at,
            actor=data["actor"],
            reason=ReasonCode(data["reason"]),
            evidence_ref=data["evidence_ref"],
            prev_mac=data["prev_mac"],
            mac=data["mac"],
        )
    except (ValueError, TypeError):
        raise ConsentFormatError("a log line has an unacceptable value") from None
    if _stamp(event.at) != data["at"]:  # one spelling of a time only, so nothing is ambiguous
        raise ConsentFormatError("a log line has an unacceptable value")
    return event


@dataclass(frozen=True, slots=True)
class ChainResult:
    ok: bool
    reason: str | None  # a fixed code: seq_gap, chain_broken, mac_invalid, duplicate_event
    last_seq: int
    last_mac: str


def verify_chain(keys: ConsentKeys, events: Sequence[ConsentEvent]) -> ChainResult:
    """Check every event: numbered 1, 2, 3 without a gap, linked to the one before, correctly
    signed, none repeated."""
    previous = keys.genesis_mac()
    seen: set[str] = set()
    for position, event in enumerate(events, start=1):
        if event.seq != position:
            return ChainResult(False, "seq_gap", position - 1, previous)
        if not hmac.compare_digest(event.prev_mac, previous):
            return ChainResult(False, "chain_broken", position - 1, previous)
        expected = keys.sign(_signed_text(_fields(event)))
        if not hmac.compare_digest(event.mac, expected):
            return ChainResult(False, "mac_invalid", position - 1, previous)
        if event.event_id in seen:
            return ChainResult(False, "duplicate_event", position - 1, previous)
        seen.add(event.event_id)
        previous = event.mac
    return ChainResult(True, None, len(events), previous)
