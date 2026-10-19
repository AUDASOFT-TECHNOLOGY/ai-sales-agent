"""Sending through the WhatsApp Business Cloud API."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from sales_core.contracts import Channel, OutboundMessage, SendResult, SendStatus
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_core.ports import Clock

DEFAULT_BASE_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v21.0"
DEFAULT_TIMEOUT_SECONDS = 15.0

# Meta error codes that mean *our* credentials or permissions are broken, not that this
# prospect is unreachable. They must not be reported as permanent failures, because the Engine
# pauses a prospect on those; they are reported as retryable instead.
_OPERATOR_ERROR_CODES = frozenset({"0", "3", "10", "190", "200"})
# Problems of the account or of the moment, never of this message: nothing was sent and the same
# message can go later ("not now"), so the Engine keeps it approved without using up an attempt.
#   130429 throughput   131048 number restricted (spam)   131049 withheld for a healthy ecosystem
#   131056 too many to the same recipient   131042 billing / payment   131031 account locked
#   133010 number not registered   131047 outside the 24 h window (our estimate was wrong)
_DEFER_CODES = frozenset(
    {"130429", "131048", "131049", "131056", "131042", "131031", "133010", "131047"}
)
# The template cannot be used as it stands: missing, paused, disabled, or its variables do not
# match. The account's templates need the Admin's attention; the message itself is not at fault.
_TEMPLATE_PROBLEM_CODES = frozenset(
    {"132000", "132001", "132005", "132007", "132012", "132015", "132016"}
)
WINDOW_HOURS = 24
TEMPLATE_REQUIRED = "template_required"
MAX_VARIABLE_LENGTH = 1024
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class OpeningTemplate:
    """An approved template the account may use to write first, or after the 24 h window.

    It carries the message in its single body variable: a template with no variable would deliver
    something other than what the agent wrote, so it is never used for that."""

    name: str
    language: str
    parameter_names: tuple[str, ...] = ()  # the body variables, in order

    @property
    def carries_text(self) -> bool:
        return len(self.parameter_names) == 1


@dataclass(frozen=True, slots=True)
class SendPolicy:
    """What the channel needs to know to follow WhatsApp's rules. A channel without a policy sends
    exactly what it is given.

    - `last_inbound(digits)`: when this person last wrote to the account (None: never / unknown).
    - `opening_template()`: the approved template to open or reopen a conversation (None: none).
    - `on_auth_failure()`: Meta refused the token; the client has to connect again.
    - `on_template_problem(name, language, code)`: Meta refused a template that was chosen.
    - `on_window_closed(digits)`: Meta says the 24 hours are over although we believed them open
      (131047): the window is forgotten, so the next try uses a template.
    """

    last_inbound: Callable[[str], datetime | None] | None = None
    opening_template: Callable[[], OpeningTemplate | None] | None = None
    on_auth_failure: Callable[[], None] | None = None
    on_template_problem: Callable[[str, str, str], None] | None = None
    on_window_closed: Callable[[str], None] | None = None


class _NotNow(Exception):
    """The message cannot go now but can later: it is deferred, not failed."""


class WhatsAppChannel:
    """Implements `sales_core.ports.MessagingChannel`.

    WhatsApp has no idempotency key, so the Engine's `SENDING` guard is what prevents duplicate
    sends. To keep that guard meaningful this adapter distinguishes:

    - request certainly not delivered (connection refused, 429, 5xx, credential problems):
      returned as a *transient* failure;
    - request rejected for this message or recipient (other 4xx): a *permanent* failure;
    - outcome unknown (timeout or reset after the request was sent, unreadable success reply):
      an exception is raised, so the Engine leaves the action in `SENDING` for a human.

    With a `SendPolicy` it also follows WhatsApp's 24-hour customer-service window: free text only
    to a person who wrote in the last 24 hours, an approved template otherwise.
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
        policy: SendPolicy | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/{api_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._clock = clock
        self._policy = policy

    @property
    def channel(self) -> Channel:
        return Channel.WHATSAPP

    def send(self, message: OutboundMessage) -> SendResult:
        try:
            message = self._within_the_rules(message)
        except _NotNow as refusal:
            return self._failure(
                SendStatus.FAILED_TRANSIENT, TEMPLATE_REQUIRED, str(refusal), deferred=True
            )
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

    # ---- WhatsApp's rules ----------------------------------------------------------------

    def _within_the_rules(self, message: OutboundMessage) -> OutboundMessage:
        """Free text only while the person wrote to us in the last 24 hours. Outside it, an
        approved template carrying the text goes instead; with none, the message waits (nothing is
        sent, nothing is lost)."""
        policy = self._policy
        if policy is None or policy.last_inbound is None or message.draft.template_id:
            return message
        digits = "".join(ch for ch in message.recipient if ch.isdigit())
        last = policy.last_inbound(digits)
        if last is not None and self._now() - last < timedelta(hours=WINDOW_HOURS):
            return message
        template = policy.opening_template() if policy.opening_template is not None else None
        if template is None or not template.carries_text:
            raise _NotNow(
                "outside the 24-hour window and no approved opening template with one variable "
                "is available"
            )
        text = _WHITESPACE.sub(" ", message.draft.text).strip()[:MAX_VARIABLE_LENGTH]
        draft = replace(
            message.draft,
            template_id=template.name,
            language=template.language,
            template_params={template.parameter_names[0]: text},
        )
        return replace(message, draft=draft)

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
                parameters = [
                    {"type": "text", "text": value}
                    if name.isdigit()
                    else {"type": "text", "parameter_name": name, "text": value}
                    for name, value in draft.template_params.items()
                ]
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
        policy = self._policy
        operator_problem = response.status_code in (401, 403) or code in _OPERATOR_ERROR_CODES
        if operator_problem and policy is not None and policy.on_auth_failure is not None:
            policy.on_auth_failure()
        template_problem = code in _TEMPLATE_PROBLEM_CODES
        if template_problem and policy is not None and policy.on_template_problem is not None:
            policy.on_template_problem(*self._template_of(response), code)
        if code == "131047" and policy is not None and policy.on_window_closed is not None:
            policy.on_window_closed(self._digits_of(response))
        deferred = operator_problem or template_problem or code in _DEFER_CODES
        transient = response.status_code == 429 or response.status_code >= 500 or deferred
        status = SendStatus.FAILED_TRANSIENT if transient else SendStatus.FAILED_PERMANENT
        return self._failure(
            status, code, f"HTTP {response.status_code}: {text}", deferred=deferred
        )

    @staticmethod
    def _digits_of(response: httpx.Response) -> str:
        try:
            return str(json.loads(response.request.content)["to"])
        except (ValueError, KeyError, TypeError, RuntimeError):
            return ""

    @staticmethod
    def _template_of(response: httpx.Response) -> tuple[str, str]:
        try:
            template = json.loads(response.request.content)["template"]
            return str(template["name"]), str(template["language"]["code"])
        except (ValueError, KeyError, TypeError, RuntimeError):
            return "", ""

    def _failure(
        self, status: SendStatus, code: str, message: str, *, deferred: bool = False
    ) -> SendResult:
        return SendResult(
            status=status,
            timestamp=self._now(),
            error_code=code,
            error_message=message,
            metadata={DEFERRED_KEY: DEFERRED_VALUE} if deferred else {},
        )

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)
