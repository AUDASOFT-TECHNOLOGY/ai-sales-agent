"""Connects the existing sources of problems to the notifications (spec 14).

Each source only calls a hook with a client id (`on_reauth`, `on_connected`, the scheduler's job
result); what is said and to whom is decided here. Reading a problem as "to renew" notifies the client
(who can act) and the Admin (who supports), once while it stays open; reconnecting closes both.
"""

from sales_app.notifications import (
    AUTHORIZATION_REVOKED,
    REPORT_FAILURE,
    SYNC_FAILURE,
    NotificationService,
)

GOOGLE_MAIL, GOOGLE_SHEETS, HUBSPOT, WHATSAPP, TELEGRAM = (
    "gmail",
    "sheets",
    "hubspot",
    "whatsapp",
    "telegram",
)
_LABEL = {
    GOOGLE_MAIL: "Gmail",
    GOOGLE_SHEETS: "Google Sheets",
    HUBSPOT: "HubSpot",
    WHATSAPP: "WhatsApp",
    TELEGRAM: "Telegram",
}


def reauth_key(source: str) -> str:
    return f"reauth:{source}"


def notify_reauth(notifications: NotificationService, source: str, client_id: str) -> None:
    label = _LABEL.get(source, source)
    notifications.notify(
        AUTHORIZATION_REVOKED,
        "client",
        client_id,
        title=f"{label} needs to be connected again",
        message=f"The {label} authorisation is no longer valid, so this connection is paused.",
        action=f"Open the {label} connection and reconnect it.",
        dedupe_key=reauth_key(source),
    )
    notifications.notify(
        AUTHORIZATION_REVOKED,
        "admin",
        client_id,
        title=f"{label} authorisation of {client_id} must be renewed",
        message=f"The {label} authorisation of this client is no longer valid.",
        action="Ask the client to reconnect; nothing to change on the platform.",
        dedupe_key=reauth_key(source),
    )


def notify_reconnected(notifications: NotificationService, source: str, client_id: str) -> None:
    notifications.resolve_both(client_id, reauth_key(source))


def wire_reauth(
    notifications: NotificationService,
    *,
    gmail=None,
    sheets=None,
    hubspot=None,
    whatsapp=None,
    telegram=None,
) -> None:
    """Install the hooks on the services that mark an authorisation as to be renewed."""
    for source, service in ((GOOGLE_MAIL, gmail), (GOOGLE_SHEETS, sheets), (HUBSPOT, hubspot)):
        grants = getattr(service, "grants", None)
        if grants is not None:
            grants.on_reauth = _reauth_hook(notifications, source)
            grants.on_connected = _connected_hook(notifications, source)
    if whatsapp is not None:
        whatsapp.on_reauth = _reauth_hook(notifications, WHATSAPP)
        whatsapp.on_connected = _connected_hook(notifications, WHATSAPP)
    if telegram is not None:
        telegram.on_reauth = _reauth_hook(notifications, TELEGRAM)


def _reauth_hook(notifications: NotificationService, source: str):
    return lambda client_id: notify_reauth(notifications, source, client_id)


def _connected_hook(notifications: NotificationService, source: str):
    return lambda client_id: notify_reconnected(notifications, source, client_id)


def job_result_hook(notifications: NotificationService):
    """For the scheduler: a failed write-back or report opens a notification, a good one closes it."""

    def hook(result) -> None:
        job = str(result.job.value)
        if job == "sync":
            kind, key = SYNC_FAILURE, "sync"
            texts = {
                "title": "The synchronisation with your system failed",
                "message": "Results could not be written back to the connected system.",
                "action": "Check the connection to the system; it will be retried automatically.",
            }
        elif job == "report":
            kind, key = REPORT_FAILURE, "report"
            texts = {}
        else:
            return
        if result.ok:
            notifications.resolve_both(result.client_id, key)
            return
        notifications.notify(kind, "client", result.client_id, dedupe_key=key, **texts)
        notifications.notify(kind, "admin", result.client_id, dedupe_key=key, **texts)

    return hook
