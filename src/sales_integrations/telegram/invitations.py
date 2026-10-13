"""Invitations: the link that lets a prospect we already know start the conversation.

A Telegram bot cannot write first, so a prospect from a spreadsheet or a CRM has to press **Start**
on the client's bot. `https://t.me/<bot>?start=<payload>` carries the payload back to us in the
`/start` message, and this module makes and reads it:

    payload = signature (16 characters) + the prospect id, both base64url

The signature is an HMAC under a key derived from the client's webhook secret, which only this
client's runtime and Telegram's own header know. Nobody can therefore invent an invitation, and one
for another client's bot, or for another prospect, does not verify. An invitation is not a secret
(whoever holds the link may use it first); the Engine makes that harmless by attaching a prospect
to one chat only, ever (`Engine._link_sender`).

Telegram accepts at most 64 characters of `[A-Za-z0-9_-]` in the payload, so a prospect id longer
than 36 bytes has no invitation (`make_invitation` returns None): such a person is reached by
sharing their phone number instead.
"""

import base64
import hashlib
import hmac

MAX_PAYLOAD = 64
SIGNATURE_BYTES = 12
SIGNATURE_CHARS = 16  # 12 bytes in base64url, no padding
_LABEL = b"revansa-telegram-invitation-v1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(secret: str, prospect_id: bytes) -> str:
    key = hmac.new(secret.encode("utf-8"), _LABEL, hashlib.sha256).digest()
    return _b64(hmac.new(key, prospect_id, hashlib.sha256).digest()[:SIGNATURE_BYTES])


def make_invitation(secret: str, prospect_id: str) -> str | None:
    """The `start` payload that invites this prospect, or None if the id is too long for one."""
    raw = prospect_id.encode("utf-8")
    if not raw:
        return None
    payload = _sign(secret, raw) + _b64(raw)
    return payload if len(payload) <= MAX_PAYLOAD else None


def read_invitation(secret: str, payload: str) -> str | None:
    """The prospect id an invitation names, or None if it is not one this secret issued."""
    if len(payload) <= SIGNATURE_CHARS or len(payload) > MAX_PAYLOAD:
        return None
    signature, encoded = payload[:SIGNATURE_CHARS], payload[SIGNATURE_CHARS:]
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        prospect_id = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if _b64(raw) != encoded or not hmac.compare_digest(signature, _sign(secret, raw)):
        return None
    return prospect_id


def invitation_link(bot_username: str, payload: str) -> str:
    return f"https://t.me/{bot_username}?start={payload}"
