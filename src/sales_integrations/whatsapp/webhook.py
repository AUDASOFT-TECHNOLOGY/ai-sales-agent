"""Authenticating and parsing WhatsApp Cloud API webhooks."""

import hashlib
import hmac
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sales_core.contracts import Channel, InboundEvent, InboundMessage
from sales_core.ports import InvalidSignatureError, MalformedPayloadError

SIGNATURE_HEADER = "x-hub-signature-256"

# Message types that are not something a prospect said: they must not start a reply.
_NOT_A_MESSAGE = frozenset({"reaction", "system", "ephemeral"})
_CAPTIONED = frozenset({"image", "video", "document"})

DELIVERY = "delivery"  # the status of a message we sent: sent, delivered, read, failed
TEMPLATE = "template"  # a message template was approved, rejected, paused, disabled...
PHONE = "phone"  # quality or messaging-limit change of a phone number
ACCOUNT = "account"  # the account was changed, restricted, or the app was removed from it


@dataclass(frozen=True, slots=True)
class WhatsAppNotification:
    """Something Meta tells us that is not a message from a prospect. `waba_id` is the account it
    is about (always present); `phone_number_id` only for what concerns a number."""

    kind: str
    waba_id: str
    phone_number_id: str | None = None
    data: Mapping[str, str] = field(default_factory=dict)


def compute_signature(app_secret: str, body: bytes) -> str:
    """The `X-Hub-Signature-256` value Meta would send for `body`."""
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WhatsAppWebhookParser:
    """Implements `sales_core.ports.WebhookParser`.

    `app_secrets` maps a routing key (the receiving phone-number id) to the Meta app secret that
    signs requests for it. The routing key is read from the body first, then the signature is
    checked with that key's secret, and nothing is returned unless every known key verifies.
    Payloads addressed to a key with no configured secret are returned unverified so the caller
    can log them; the app layer has no client to route them to, so they are never acted on.
    """

    def __init__(self, *, app_secrets: Mapping[str, str], verify_token: str | None) -> None:
        self._secrets = dict(app_secrets)
        self._verify_token = verify_token

    def handshake(self, query: Mapping[str, str]) -> str | None:
        """Meta's subscription check: echo `hub.challenge` if `hub.verify_token` matches."""
        expected = self._verify_token
        if not expected or query.get("hub.mode") != "subscribe":
            return None
        supplied = query.get("hub.verify_token", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return None
        return query.get("hub.challenge")

    def parse(self, body: bytes, headers: Mapping[str, str]) -> Sequence[InboundEvent]:
        payload = self._load(body)
        changes = list(self._changes(payload))
        self._verify(body, headers, {key for key, _ in changes})
        events: list[InboundEvent] = []
        for key, value in changes:
            names = self._contact_names(value)
            for raw in value.get("messages") or []:
                if not isinstance(raw, Mapping) or raw.get("type") in _NOT_A_MESSAGE:
                    continue
                events.append(InboundEvent(routing_key=key, message=self._message(raw, names)))
        return events

    def notifications(
        self, body: bytes, headers: Mapping[str, str]
    ) -> Sequence[WhatsAppNotification]:
        """Delivery statuses, template, number and account events. Template and account events
        carry no phone-number routing key (they belong to the account), so every notification is
        authenticated with the app secret itself: a request that no known secret signs raises.
        With no secret known there is nothing to authenticate with and nothing is returned."""
        payload = self._load(body)
        found: list[WhatsAppNotification] = []
        try:
            for entry in payload.get("entry") or []:
                waba = str(entry.get("id", ""))
                for change in entry.get("changes") or []:
                    found.extend(self._notifications_of(waba, change))
        except (KeyError, TypeError, AttributeError, ValueError, IndexError) as error:
            raise MalformedPayloadError(f"unexpected payload structure: {error!r}") from error
        if not found:
            return []  # only messages (see `parse`): nothing here to authenticate
        secrets = set(self._secrets.values())
        if not secrets:
            return []
        supplied = {name.lower(): value for name, value in headers.items()}.get(SIGNATURE_HEADER)
        if not supplied:
            raise InvalidSignatureError("missing signature")
        if not any(
            hmac.compare_digest(supplied.encode(), compute_signature(secret, body).encode())
            for secret in secrets
        ):
            raise InvalidSignatureError("signature does not match")
        return found

    @staticmethod
    def _notifications_of(waba: str, change: Mapping[str, Any]) -> Iterator[WhatsAppNotification]:
        name, value = str(change.get("field", "")), change.get("value") or {}
        phone = (value.get("metadata") or {}).get("phone_number_id")
        phone = str(phone) if phone else None
        if name == "messages":
            for status in value.get("statuses") or []:
                data = {
                    "message_id": str(status["id"]),
                    "status": str(status["status"]),
                    "recipient": str(status.get("recipient_id", "")),
                    "at": datetime.fromtimestamp(int(status["timestamp"]), UTC).isoformat(),
                }
                errors = status.get("errors") or []
                if errors and isinstance(errors[0], Mapping):
                    first = errors[0]
                    data["error_code"] = str(first.get("code", ""))
                    data["error_title"] = str(first.get("title") or first.get("message") or "")[
                        :200
                    ]
                yield WhatsAppNotification(DELIVERY, waba, phone, data)
        elif name in (
            "message_template_status_update",
            "message_template_quality_update",
            "template_category_update",
        ):
            yield WhatsAppNotification(
                TEMPLATE,
                waba,
                None,
                {
                    "field": name,
                    "template_id": str(value.get("message_template_id", "")),
                    "name": str(value.get("message_template_name", "")),
                    "language": str(value.get("message_template_language", "")),
                    "event": str(value.get("event", "")),
                    "reason": str(value.get("reason") or "")[:300],
                    "quality": str(value.get("new_quality_score", "")),
                    "category": str(value.get("new_category", "")),
                },
            )
        elif name in ("phone_number_quality_update", "phone_number_name_update"):
            yield WhatsAppNotification(
                PHONE,
                waba,
                phone,
                {
                    "field": name,
                    "event": str(value.get("event", "")),
                    "limit": str(value.get("current_limit", "")),
                    "phone": str(value.get("display_phone_number", "")),
                },
            )
        elif name in ("account_update", "account_review_update", "account_alerts"):
            info = value.get("waba_info") or {}
            account = str(info.get("waba_id") or waba)
            yield WhatsAppNotification(
                ACCOUNT,
                account,
                None,
                {
                    "field": name,
                    "event": str(value.get("event") or value.get("decision") or ""),
                    "waba_id": account,
                },
            )

    # ---- authenticity --------------------------------------------------------------------

    def _verify(self, body: bytes, headers: Mapping[str, str], keys: set[str]) -> None:
        known = [k for k in keys if k in self._secrets]
        if not known:
            return
        supplied = {name.lower(): value for name, value in headers.items()}.get(SIGNATURE_HEADER)
        if not supplied:
            raise InvalidSignatureError("missing signature")
        for key in known:
            expected = compute_signature(self._secrets[key], body)
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                raise InvalidSignatureError("signature does not match")

    # ---- payload -------------------------------------------------------------------------

    @staticmethod
    def _load(body: bytes) -> Mapping[str, Any]:
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise MalformedPayloadError(f"body is not valid JSON: {error}") from error
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            raise MalformedPayloadError("not a WhatsApp Business Account payload")
        return payload

    @staticmethod
    def _changes(payload: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
        try:
            for entry in payload.get("entry") or []:
                for change in entry.get("changes") or []:
                    value = change["value"]
                    metadata = value.get("metadata") if isinstance(value, Mapping) else None
                    if not isinstance(metadata, Mapping) or not metadata.get("phone_number_id"):
                        if change.get("field") not in (None, "messages"):
                            continue  # template, account and alert events: see `notifications`
                        raise KeyError("metadata.phone_number_id")
                    yield str(metadata["phone_number_id"]), value
        except (KeyError, TypeError, AttributeError) as error:
            raise MalformedPayloadError(f"unexpected payload structure: {error!r}") from error

    @staticmethod
    def _contact_names(value: Mapping[str, Any]) -> dict[str, str]:
        """`wa_id` -> the name the person gave their WhatsApp profile (only what Meta sends)."""
        names: dict[str, str] = {}
        for contact in value.get("contacts") or []:
            if not isinstance(contact, Mapping):
                continue
            name = (contact.get("profile") or {}).get("name")
            if contact.get("wa_id") and isinstance(name, str) and name.strip():
                names[str(contact["wa_id"])] = name.strip()[:120]
        return names

    @staticmethod
    def _message(raw: Mapping[str, Any], names: Mapping[str, str] | None = None) -> InboundMessage:
        try:
            kind = str(raw.get("type", "unknown"))
            metadata = WhatsAppWebhookParser._metadata(kind, raw)
            name = (names or {}).get(str(raw["from"]))
            if name:
                metadata["profile_name"] = name
            return InboundMessage(
                channel=Channel.WHATSAPP,
                sender=str(raw["from"]),
                external_id=str(raw["id"]),
                text=WhatsAppWebhookParser._text(kind, raw),
                timestamp=datetime.fromtimestamp(int(raw["timestamp"]), UTC),
                metadata=metadata,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MalformedPayloadError(f"unreadable message: {error!r}") from error

    @staticmethod
    def _metadata(kind: str, raw: Mapping[str, Any]) -> dict[str, str]:
        """The kind of content, and the provider's id of the media when there is one: a reference
        to what was sent (a receipt, a photo), not a copy of it. Nothing is downloaded or stored."""
        metadata = {"type": kind}
        content = raw.get(kind)
        if kind != "text" and isinstance(content, Mapping) and content.get("id"):
            metadata["media_id"] = str(content["id"])
        return metadata

    @staticmethod
    def _text(kind: str, raw: Mapping[str, Any]) -> str:
        if kind == "text":
            return str(raw["text"]["body"])
        if kind == "button":
            return str(raw["button"]["text"])
        if kind == "interactive":
            interactive = raw["interactive"]
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            return str(reply.get("title", ""))
        if kind in _CAPTIONED:
            content = raw.get(kind)
            return str(content.get("caption") or "") if isinstance(content, Mapping) else ""
        return ""  # other media and types: no text, the kind is kept in the metadata
