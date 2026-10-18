"""A client's request to have its e-mail mailbox activated, and where the Admin has taken it.

The Admin creates the mailbox by hand at the provider (Revansa has no provider API), so the client
*asks* and the Admin answers. This module is only the record and its rules; it reads and writes
nothing. `sales_app.email_activation` keeps it, and the loader reads it to refuse a client whose
request has not been approved.

A request is not the Admin's authorization of the Email channel (`channel_access`): that decides
whether a client may use Email at all, this one is a client asking for its own mailbox.

Statuses: `pending` (asked), `in_progress` (the Admin is on it), `rejected` (with a reason the client
sees; a deactivation ends here too) and `active` (the mailbox is assigned and its logins accepted).
A client has at most one request. A new one is possible only after `rejected`, and replaces it.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

PENDING = "pending"
IN_PROGRESS = "in_progress"
REJECTED = "rejected"
ACTIVE = "active"
STATUSES = (PENDING, IN_PROGRESS, REJECTED, ACTIVE)
OPEN_STATUSES = (PENDING, IN_PROGRESS)
MAX_REASON_LENGTH = 500


@dataclass(frozen=True, slots=True)
class ActivationRecord:
    status: str
    requested_at: datetime
    updated_at: datetime
    updated_by: str
    reason: str | None = None  # why it was rejected or deactivated; None otherwise


def format_record(record: ActivationRecord) -> str:
    return json.dumps(
        {
            "status": record.status,
            "requested_at": record.requested_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "updated_by": record.updated_by,
            "reason": record.reason,
        },
        sort_keys=True,
    )


def parse_record(value: str | None) -> ActivationRecord | None:
    """The stored record, or None when nothing is stored or what is stored cannot be read (which is
    then the same as no request: it never makes a client look activated)."""
    if not value:
        return None
    try:
        data = json.loads(value)
        status = data["status"]
        if status not in STATUSES:
            return None
        reason = data.get("reason")
        return ActivationRecord(
            status=status,
            requested_at=_moment(data["requested_at"]),
            updated_at=_moment(data["updated_at"]),
            updated_by=str(data["updated_by"]),
            reason=reason if isinstance(reason, str) and reason else None,
        )
    except (ValueError, KeyError, TypeError):
        return None


def blocks_sending(record: ActivationRecord | None) -> bool:
    """A client that asked and has not been activated does not send. One that never asked is left as
    it was: a mailbox the Admin assigned directly keeps working."""
    return record is not None and record.status != ACTIVE


def _moment(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
