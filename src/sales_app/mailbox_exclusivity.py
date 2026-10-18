"""A client's e-mail comes from exactly one place: its Gmail, a mailbox Revansa provides (platform),
or the historical mailbox (custom). The settings hold one `mode`, so two cannot be named; this module
refuses the *transitions and credentials* that would leave two in force, whoever asks (the Client
channel route, the Admin settings route, the Admin credentials route), so it is the server that says
no and not only the screen.

- into Gmail: refused while a mailbox Revansa provides is assigned (or its activation is under way)
  or a mailbox password is stored for the client;
- out of Gmail (to platform or custom): refused while a Gmail grant is held (disconnect first);
- a dedicated mailbox address or password cannot be stored for a client that is in Gmail mode or
  holds a Gmail grant.

Fixed sentences, never an address or a value.
"""

from collections.abc import Mapping
from typing import Any

from sales_app.config.email_settings import GMAIL_MODE, email_mode_of
from sales_app.config.secrets import EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX
from sales_app.google_mailbox import GoogleGrantStore

DEDICATED_ASSIGNED = (
    "a Revansa mailbox is already set up for this account: it cannot also use Gmail"
)
GMAIL_CONNECTED = "Gmail is connected to this account: disconnect it before using another mailbox"
GMAIL_ACCOUNT_CONFLICT = "this account uses Gmail: it cannot also have a dedicated mailbox"


def _stored(application: Any, client_id: str, key: str) -> bool:
    from sales_app.channel_status import credential_stored

    return credential_stored(application, client_id, key)


def dedicated_in_use(application: Any, client_id: str) -> bool:
    """A mailbox Revansa provides is assigned, or its activation is under way, or a mailbox password
    is stored."""
    if _stored(application, client_id, EMAIL_PLATFORM_MAILBOX) or _stored(
        application, client_id, EMAIL_PASSWORD
    ):
        return True
    service = getattr(application, "email_activation", None)
    try:
        record = service.get(client_id) if service is not None else None
    except Exception:  # noqa: BLE001 - unreadable: say "in use", the safe side
        return True
    return record is not None and record.status in ("in_progress", "active")


def gmail_grant_held(application: Any, client_id: str) -> bool:
    gmail = getattr(application, "gmail", None)
    if gmail is None:
        return False
    grants: GoogleGrantStore = gmail.grants
    try:
        return grants.load(client_id) is not None
    except Exception:  # noqa: BLE001 - unreadable: say "held", the safe side
        return True


def mode_conflict(
    application: Any, client_id: str, old: Mapping[str, Any], new: Mapping[str, Any]
) -> str | None:
    """Why moving this client's e-mail settings from `old` to `new` would leave two mailboxes in
    force; None when it is fine (including every change that keeps the same mode)."""
    before, after = email_mode_of(old), email_mode_of(new)
    if before == after:
        return None
    if after == GMAIL_MODE:
        return DEDICATED_ASSIGNED if dedicated_in_use(application, client_id) else None
    return GMAIL_CONNECTED if gmail_grant_held(application, client_id) else None


def dedicated_credential_conflict(
    application: Any, client_id: str, document: Mapping[str, Any]
) -> str | None:
    """Why a dedicated mailbox credential cannot be stored for this client now."""
    from sales_app.channel_status import channel_settings_of

    if email_mode_of(channel_settings_of(document)) == GMAIL_MODE:
        return GMAIL_ACCOUNT_CONFLICT
    return GMAIL_CONNECTED if gmail_grant_held(application, client_id) else None


__all__ = [
    "dedicated_credential_conflict",
    "dedicated_in_use",
    "gmail_grant_held",
    "mode_conflict",
]
