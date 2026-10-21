"""Delivery, reception and model failures as notifications (spec 14).

The channels, the inbox poll and the language model tell `OperationalSignals` how each call went;
what is said and to whom is decided here. One open notification per client, channel and type
(`send:<channel>`, `receive:<channel>`, `llm:<type>`): a repeated failure changes nothing while it is
open, and the next success closes it. Texts are fixed sentences with an action: never an address, a
token, a message body or the provider's own text.

`NotifyingChannel` and `NotifyingLLMProvider` are thin wrappers (adapters of the application, the
Core and the integrations know nothing of notifications). They never change the call they wrap and a
notification that fails never fails it.
"""

import logging

from sales_app.notifications import (
    CONFIG_PROBLEM,
    QUOTA_REACHED,
    RECEIVE_FAILURE,
    SEND_FAILURE,
    NotificationService,
)
from sales_core.contracts.messaging import DEFERRED_KEY
from sales_core.ports.llm import LLMAuthError, LLMModelError, LLMRateLimitError

log = logging.getLogger(__name__)

_CHANNEL_LABEL = {"email": "Email", "telegram": "Telegram", "whatsapp": "WhatsApp"}
LLM_KEYS = ("llm:quota", "llm:auth", "llm:model")


def _label(channel: str) -> str:
    return _CHANNEL_LABEL.get(channel, channel)


class OperationalSignals:
    def __init__(self, notifications: NotificationService) -> None:
        self._n = notifications

    def __repr__(self) -> str:
        return "OperationalSignals()"

    def _both(
        self,
        kind: str,
        client_id: str,
        key: str,
        *,
        title: str,
        message: str,
        client_action: str,
        admin_title: str,
        admin_action: str,
    ) -> None:
        self._n.notify(
            kind, "client", client_id, title=title, message=message, action=client_action,
            dedupe_key=key,
        )
        self._n.notify(
            kind, "admin", client_id, title=admin_title, message=message, action=admin_action,
            dedupe_key=key,
        )

    # ---- sending ----
    def send_ok(self, client_id: str, channel: str) -> None:
        self._n.resolve_both(client_id, f"send:{channel}")

    def send_failed(self, client_id: str, channel: str, *, retryable: bool) -> None:
        label = _label(channel)
        message = (
            f"A {label} message could not be sent; it will be retried."
            if retryable
            else f"A {label} message was refused for good (invalid recipient or refused by {label})."
        )
        self._both(
            SEND_FAILURE,
            client_id,
            f"send:{channel}",
            title=f"A {label} message was not sent",
            message=message,
            client_action=f"Check the {label} connection and the contact's details.",
            admin_title=f"{label} sending is failing for {client_id}",
            admin_action=f"Check the {label} channel status of this client.",
        )

    # ---- receiving ----
    def receive_ok(self, client_id: str, channel: str) -> None:
        self._n.resolve_both(client_id, f"receive:{channel}")

    def receive_failed(self, client_id: str, channel: str) -> None:
        label = _label(channel)
        self._both(
            RECEIVE_FAILURE,
            client_id,
            f"receive:{channel}",
            title=f"Your {label} replies could not be read",
            message=f"Revansa could not read the incoming {label} messages; it will retry.",
            client_action=f"Check that the {label} connection is still authorised.",
            admin_title=f"{label} reception is failing for {client_id}",
            admin_action=f"Check the {label} channel status of this client.",
        )

    # ---- the language model ----
    def llm_ok(self, client_id: str) -> None:
        for key in LLM_KEYS:
            self._n.resolve_both(client_id, key)

    def llm_failed(self, client_id: str, error: Exception) -> None:
        if isinstance(error, LLMRateLimitError):
            kind, key = QUOTA_REACHED, "llm:quota"
            title = "The AI provider quota or rate limit was reached"
            message = "The language model provider is throttling or its quota is used up."
            client_action = "The agent will retry; contact support if this lasts."
            admin_action = "Check the provider account quota and billing, or the model assignment."
        elif isinstance(error, LLMAuthError):
            kind, key = CONFIG_PROBLEM, "llm:auth"
            title = "The AI provider refused the credentials"
            message = "The language model provider refused the platform's key."
            client_action = "Contact support: the agent cannot write until this is fixed."
            admin_action = "Check the provider key in the platform settings."
        elif isinstance(error, LLMModelError):
            kind, key = CONFIG_PROBLEM, "llm:model"
            title = "The AI model is not available"
            message = "The assigned language model does not exist or refused the request."
            client_action = "Contact support: the agent cannot write until this is fixed."
            admin_action = "Check the model assigned to this client."
        else:
            return  # timeouts and unusable answers are transient and retried: not worth a notice
        self._both(
            kind,
            client_id,
            key,
            title=title,
            message=message,
            client_action=client_action,
            admin_title=f"{title} ({client_id})",
            admin_action=admin_action,
        )


class NotifyingChannel:
    """A `MessagingChannel` that tells `OperationalSignals` how each send went. A message deferred
    by the daily cap is not a failure."""

    def __init__(self, inner, client_id: str, signals: OperationalSignals) -> None:
        self._inner = inner
        self._client_id = client_id
        self._signals = signals

    def __repr__(self) -> str:
        return "NotifyingChannel()"

    @property
    def channel(self):
        return self._inner.channel

    def send(self, message):
        name = str(getattr(self._inner.channel, "value", self._inner.channel))
        try:
            result = self._inner.send(message)
        except Exception:
            self._tell(self._signals.send_failed, name, retryable=True)
            raise
        if result.ok:
            self._tell(self._signals.send_ok, name)
        elif not (result.metadata or {}).get(DEFERRED_KEY):
            self._tell(self._signals.send_failed, name, retryable=result.retryable)
        return result

    def _tell(self, call, name: str, **kw) -> None:
        try:
            call(self._client_id, name, **kw)
        except Exception:  # noqa: BLE001 - a notification never breaks a send
            log.error("a send notification failed")


class NotifyingLLMProvider:
    """An `LLMProvider` that tells `OperationalSignals` about quota, credential and model errors."""

    def __init__(self, inner, client_id: str, signals: OperationalSignals) -> None:
        self._inner = inner
        self._client_id = client_id
        self._signals = signals

    def __repr__(self) -> str:
        return "NotifyingLLMProvider()"

    def complete(self, request):
        try:
            response = self._inner.complete(request)
        except Exception as error:
            try:
                self._signals.llm_failed(self._client_id, error)
            except Exception:  # noqa: BLE001
                log.error("a model notification failed")
            raise
        try:
            self._signals.llm_ok(self._client_id)
        except Exception:  # noqa: BLE001
            log.error("a model notification failed")
        return response
