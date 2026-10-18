"""Where a client's e-mail sending stands against its daily limit, in words a client can read.

Read-only and nothing technical: the client sees whether e-mails can leave today and why not (the
limit is not set, it is reached, it cannot be checked), never how the limit is stored. Setting it is
the Admin's (`sales_app.api.admin_send_cap`).
"""

from typing import Any

from sales_app.send_cap import utc_day

OK = "ok"
NOT_SET = "not_set"
REACHED = "reached"
UNAVAILABLE = "unavailable"

_MESSAGES = {
    NOT_SET: "Your daily sending limit is not set yet: no email is sent until Revansa sets it.",
    REACHED: "Your daily sending limit is reached: sending resumes tomorrow.",
    UNAVAILABLE: "Your daily sending limit cannot be checked right now: no email is sent until it can.",
}


def email_sending(application: Any, client_id: str) -> dict[str, Any]:
    """`{state, message, daily_limit, sent_today}` for the client's e-mail channel (all e-mails of
    the client count: prospecting, replies, follow-ups and the daily report)."""
    limits = getattr(application, "send_limits", None)
    if limits is None:
        return _answer(UNAVAILABLE)
    try:
        effective = limits.effective(client_id)
        if effective is None:
            return _answer(NOT_SET)
        used = limits.used(client_id, "email", utc_day(application.clock.now()))
    except Exception:  # noqa: BLE001 - a store down is said as "cannot be checked", never its text
        return _answer(UNAVAILABLE)
    state = REACHED if used >= effective else OK
    return _answer(state, daily_limit=effective, sent_today=used)


def _answer(state: str, **figures: int) -> dict[str, Any]:
    return {
        "state": state,
        "message": _MESSAGES.get(state),
        "daily_limit": figures.get("daily_limit"),
        "sent_today": figures.get("sent_today"),
    }
