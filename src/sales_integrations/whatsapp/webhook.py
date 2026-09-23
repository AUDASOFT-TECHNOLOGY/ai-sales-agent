"""Authenticating and parsing WhatsApp Cloud API webhooks."""

import hashlib
import hmac
import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sales_core.contracts import Channel, InboundEvent, InboundMessage
from sales_core.ports import InvalidSignatureError, MalformedPayloadError

SIGNATURE_HEADER = "x-hub-signature-256"


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
            for raw in value.get("messages") or []:
                events.append(InboundEvent(routing_key=key, message=self._message(raw)))
        return events

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
                    yield str(value["metadata"]["phone_number_id"]), value
        except (KeyError, TypeError, AttributeError) as error:
            raise MalformedPayloadError(f"unexpected payload structure: {error!r}") from error

    @staticmethod
    def _message(raw: Mapping[str, Any]) -> InboundMessage:
        try:
            kind = str(raw.get("type", "unknown"))
            return InboundMessage(
                channel=Channel.WHATSAPP,
                sender=str(raw["from"]),
                external_id=str(raw["id"]),
                text=WhatsAppWebhookParser._text(kind, raw),
                timestamp=datetime.fromtimestamp(int(raw["timestamp"]), UTC),
                metadata={"type": kind},
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MalformedPayloadError(f"unreadable message: {error!r}") from error

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
        return ""  # media and other types: no text, the kind is kept in the metadata
