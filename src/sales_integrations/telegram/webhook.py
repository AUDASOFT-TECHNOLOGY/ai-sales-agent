"""Authenticating and parsing the updates Telegram posts to a client's webhook.

Telegram does not sign a request: it repeats, in a header, the `secret_token` chosen when the
webhook was registered. Each client's bot has its own secret, so the header says which client the
update is for, and nothing is read from the body before it matched one. A request that carries no
known secret is refused whole (401), whatever its body.

Only what a sales conversation needs is kept: a private chat with a person, never a group, a
channel or another bot. What is learned about the sender (`/start` with one of our invitations, a
phone number the person shared *from their own account*) is passed on as a verified claim; text
the sender typed never is.
"""

import hmac
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sales_core.contracts import Channel, InboundEvent, InboundMessage
from sales_core.ports import InvalidSignatureError, MalformedPayloadError
from sales_integrations.telegram.invitations import read_invitation

SECRET_HEADER = "x-telegram-bot-api-secret-token"
_NAME_LIMIT = 80
_MEDIA = ("photo", "document", "voice", "audio", "video", "video_note", "sticker", "animation")


class TelegramWebhookParser:
    """Implements `sales_core.ports.WebhookParser`.

    `app_secrets` maps a routing key (the bot's id) to the webhook secret of that client's bot. The
    secret is also the key invitations are signed with.
    """

    def __init__(self, *, app_secrets: Mapping[str, str], verify_token: str | None = None) -> None:
        self._secrets = {key: secret for key, secret in app_secrets.items() if key and secret}

    def handshake(self, query: Mapping[str, str]) -> str | None:
        return None  # Telegram has no subscription handshake

    def parse(self, body: bytes, headers: Mapping[str, str]) -> Sequence[InboundEvent]:
        key = self._authenticate(headers)
        update = self._load(body)
        message = update.get("message")
        if not isinstance(message, dict):
            return []  # edited messages, reactions...: not part of a sales conversation
        event = self._event(key, self._secrets[key], message)
        return [] if event is None else [event]

    # ---- authenticity --------------------------------------------------------------------

    def _authenticate(self, headers: Mapping[str, str]) -> str:
        supplied = {name.lower(): value for name, value in headers.items()}.get(SECRET_HEADER)
        if not supplied:
            raise InvalidSignatureError("missing secret token")
        matched = None
        for key, secret in self._secrets.items():  # no early exit: every secret is compared
            if hmac.compare_digest(supplied.encode(), secret.encode()):
                matched = key
        if matched is None:
            raise InvalidSignatureError("secret token does not match")
        return matched

    # ---- payload -------------------------------------------------------------------------

    @staticmethod
    def _load(body: bytes) -> Mapping[str, Any]:
        try:
            update = json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise MalformedPayloadError(f"body is not valid JSON: {error}") from error
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            raise MalformedPayloadError("not a Telegram update")
        return update

    def _event(self, key: str, secret: str, raw: Mapping[str, Any]) -> InboundEvent | None:
        try:
            chat, sender = raw["chat"], raw.get("from") or {}
            if chat.get("type") != "private" or sender.get("is_bot"):
                return None
            chat_id = str(int(chat["id"]))
            kind, text = self._kind_and_text(raw)
            metadata = {"type": kind}
            name = self._name(sender)
            if name:
                metadata["sender_name"] = name
            if isinstance(sender.get("language_code"), str):
                metadata["language"] = sender["language_code"][:16]
            media = self._media_id(raw)
            if media:
                metadata["media_id"] = media

            linked: str | None = None
            shared: str | None = None
            if kind == "start":
                payload = text.partition(" ")[2].strip()
                linked = read_invitation(secret, payload) if payload else None
                text = ""  # the command is not something the person said
            elif kind == "contact":
                contact = raw["contact"]
                if str(contact.get("user_id")) == str(sender.get("id")):  # their own number
                    shared = str(contact["phone_number"]).strip() or None
                text = ""  # never keep the number in the conversation

            return InboundEvent(
                routing_key=key,
                message=InboundMessage(
                    channel=Channel.TELEGRAM,
                    sender=chat_id,
                    external_id=f"{chat_id}:{int(raw['message_id'])}",
                    text=text,
                    timestamp=datetime.fromtimestamp(int(raw["date"]), UTC),
                    metadata=metadata,
                    linked_prospect_id=linked,
                    shared_phone=shared,
                ),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise MalformedPayloadError(f"unreadable message: {error!r}") from error

    @staticmethod
    def _kind_and_text(raw: Mapping[str, Any]) -> tuple[str, str]:
        text = raw.get("text")
        if isinstance(text, str):
            command = text.split(maxsplit=1)[0].split("@", 1)[0] if text.strip() else ""
            if command == "/start":
                return "start", "/start " + text.partition(" ")[2]
            return "text", text
        if isinstance(raw.get("contact"), dict):
            return "contact", ""
        caption = raw.get("caption")
        for media in _MEDIA:
            if media in raw:
                return media, caption if isinstance(caption, str) else ""
        return "unknown", ""

    @staticmethod
    def _name(sender: Mapping[str, Any]) -> str:
        parts = [sender.get("first_name"), sender.get("last_name")]
        return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())[:_NAME_LIMIT]

    @staticmethod
    def _media_id(raw: Mapping[str, Any]) -> str | None:
        """Telegram's id of what was sent (a receipt, a photo): a reference, never a download."""
        for media in _MEDIA:
            content = raw.get(media)
            if media == "photo" and isinstance(content, list) and content:
                content = content[-1]  # the largest size
            if isinstance(content, dict) and content.get("file_id"):
                return str(content["file_id"])[:200]
        return None
