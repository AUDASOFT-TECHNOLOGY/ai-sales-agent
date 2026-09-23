"""Sending through the WhatsApp Business Cloud API."""

from datetime import UTC, datetime
from typing import Any

import httpx

from sales_core.contracts import Channel, OutboundMessage, SendResult, SendStatus
from sales_core.ports import Clock

DEFAULT_BASE_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v21.0"
DEFAULT_TIMEOUT_SECONDS = 15.0

# Meta error codes that mean *our* credentials or permissions are broken, not that this
# prospect is unreachable. They must not be reported as permanent failures, because the Engine
# pauses a prospect on those; they are reported as retryable instead.
_OPERATOR_ERROR_CODES = frozenset({"0", "3", "10", "190", "200"})


class WhatsAppChannel:
    """Implements `sales_core.ports.MessagingChannel`.

    WhatsApp has no idempotency key, so the Engine's `SENDING` guard is what prevents duplicate
    sends. To keep that guard meaningful this adapter distinguishes:

    - request certainly not delivered (connection refused, 429, 5xx, credential problems):
      returned as a *transient* failure;
    - request rejected for this message or recipient (other 4xx): a *permanent* failure;
    - outcome unknown (timeout or reset after the request was sent, unreadable success reply):
      an exception is raised, so the Engine leaves the action in `SENDING` for a human.
    """

    def __init__(
        self,
        *,
        token: str,
        phone_number_id: str,
        api_version: str = DEFAULT_API_VERSION,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/{api_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._clock = clock

    @property
    def channel(self) -> Channel:
        return Channel.WHATSAPP

    def send(self, message: OutboundMessage) -> SendResult:
        try:
            body = self._body(message)
        except ValueError as error:
            return self._failure(SendStatus.FAILED_PERMANENT, "invalid_message", str(error))
        try:
            response = self._client.post(self._url, json=body, headers=self._headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            return self._failure(SendStatus.FAILED_TRANSIENT, "connect_error", str(error))
        # Any other transport error (read timeout, reset...) propagates: the outcome is unknown.

        if response.is_success:
            return self._accepted(response)
        return self._rejected(response)

    # ---- request -------------------------------------------------------------------------

    @staticmethod
    def _body(message: OutboundMessage) -> dict[str, Any]:
        to = "".join(ch for ch in message.recipient if ch.isdigit())
        if not to:
            raise ValueError(f"recipient {message.recipient!r} has no digits")
        body: dict[str, Any] = {"messaging_product": "whatsapp", "to": to}
        draft = message.draft
        if draft.template_id:
            if not draft.language:
                raise ValueError("a template message needs a language code")
            template: dict[str, Any] = {
                "name": draft.template_id,
                "language": {"code": draft.language},
            }
            if draft.template_params:
                parameters = [{"type": "text", "text": v} for v in draft.template_params.values()]
                template["components"] = [{"type": "body", "parameters": parameters}]
            return body | {"type": "template", "template": template}
        return body | {"type": "text", "text": {"body": draft.text}}

    # ---- responses -----------------------------------------------------------------------

    def _accepted(self, response: httpx.Response) -> SendResult:
        try:
            external_id = response.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            # Accepted by HTTP status but the id is unreadable: do not guess, do not retry.
            raise RuntimeError(
                f"WhatsApp accepted the request but the reply is unreadable: {error}"
            ) from error
        return SendResult(
            status=SendStatus.ACCEPTED, timestamp=self._now(), external_id=external_id
        )

    def _rejected(self, response: httpx.Response) -> SendResult:
        code, text = str(response.status_code), response.text[:200]
        try:
            error = response.json()["error"]
            code = str(error.get("code", code))
            text = str(error.get("message", text))
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        operator_problem = response.status_code in (401, 403) or code in _OPERATOR_ERROR_CODES
        transient = response.status_code == 429 or response.status_code >= 500 or operator_problem
        status = SendStatus.FAILED_TRANSIENT if transient else SendStatus.FAILED_PERMANENT
        return self._failure(status, code, f"HTTP {response.status_code}: {text}")

    def _failure(self, status: SendStatus, code: str, message: str) -> SendResult:
        return SendResult(
            status=status, timestamp=self._now(), error_code=code, error_message=message
        )

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)
