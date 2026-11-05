"""The LLM usage register: what each model call of a client's agent consumed.

The Core never calls a model and knows nothing of this: the runtime wraps the client's `LLMProvider`
in a `RecordingLLMProvider`, which hands every call to a `LLMUsageStore`. Recording is best effort:
a store that fails is logged (the type of the failure, never a message) and the call's result or
error goes on exactly as it would have. A figure the provider did not report is kept as None, never
as a made-up zero. Only counts are kept: never a prompt, an answer or a contact.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sales_core.contracts import LLMRequest, LLMResponse
from sales_core.ports import LLMProvider

log = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class LLMUsageRecord:
    client_id: str
    occurred_at: datetime
    provider: str
    model: str
    status: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    # The credit ledger (`sales_app.credits`); None on a call that was not priced.
    call_id: str | None = None
    operation: str | None = None
    cost_pico_usd: int | None = None  # the real API cost, in trillionths of a dollar
    credits_nano: int | None = None  # the credits it makes, in billionths of a credit


class CreditMeter(Protocol):
    """What the recording wrapper asks of the credits. `begin` raises `LLMCreditsExhaustedError`."""

    def begin(self, client_id: str, provider: str, model: str, request: LLMRequest) -> object: ...

    def cancel(self, hold: object) -> None: ...

    def settle(self, hold: object, record: LLMUsageRecord) -> None:
        """Prices `record` (its tokens), writes it to the register and counts the credits, once."""
        ...


class LLMUsageStore(Protocol):
    """One client's register of model calls."""

    def record(self, record: LLMUsageRecord) -> None: ...

    def list_usage(self) -> list[LLMUsageRecord]:
        """This client's calls, oldest first."""
        ...


class InMemoryLLMUsageStore:
    """For tests and for a deployment with no database: nothing survives the process."""

    def __init__(self, client_id: str) -> None:
        if not client_id:
            raise ValueError("a usage store needs a client id")
        self._client_id = client_id
        self._records: list[LLMUsageRecord] = []
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"InMemoryLLMUsageStore(client={self._client_id!r})"

    def record(self, record: LLMUsageRecord) -> None:
        if record.client_id != self._client_id:
            raise ValueError("a usage record belongs to another client")
        with self._lock:
            self._records.append(record)

    def list_usage(self) -> list[LLMUsageRecord]:
        with self._lock:
            return list(self._records)


class RecordingLLMProvider:
    """An `LLMProvider` that records each call of the one it wraps, then answers as it did."""

    def __init__(
        self,
        inner: LLMProvider,
        store: LLMUsageStore,
        *,
        client_id: str,
        provider: str,
        model: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        credits: CreditMeter | None = None,
    ) -> None:
        self._inner = inner
        self._store = store
        self._client_id = client_id
        self._provider = provider
        self._model = model  # the configured model: what a failed call is attributed to
        self._now = now
        self._credits = credits

    def complete(self, request: LLMRequest) -> LLMResponse:
        hold = None
        if self._credits is not None:  # refuses here, before any model call, when credits fail
            hold = self._credits.begin(self._client_id, self._provider, self._model, request)
        try:
            response = self._inner.complete(request)
        except Exception:
            self._cancel(hold)
            self._record(STATUS_ERROR, self._model, None, None)
            raise
        call_id = response.metadata.get("request_id") or uuid.uuid4().hex
        self._record(
            STATUS_OK,
            response.model or self._model,
            response.input_tokens,
            response.output_tokens,
            hold=hold,
            call_id=call_id,
            operation=request.purpose,
        )
        return response

    def _cancel(self, hold: object | None) -> None:
        if hold is None or self._credits is None:
            return
        try:
            self._credits.cancel(hold)
        except Exception as error:  # noqa: BLE001 - an expired hold is swept later
            log.warning("credit hold not released (%s): %s", self._client_id, error.__class__.__name__)

    def _record(
        self,
        status: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        *,
        hold: object | None = None,
        call_id: str | None = None,
        operation: str | None = None,
    ) -> None:
        record = LLMUsageRecord(
            client_id=self._client_id,
            occurred_at=self._now(),
            provider=self._provider,
            model=model,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            call_id=call_id,
            operation=operation,
        )
        try:
            if self._credits is not None and hold is not None:
                self._credits.settle(hold, record)
            else:
                self._store.record(record)
        except Exception as error:  # noqa: BLE001 - never block the commercial answer
            log.warning(
                "LLM usage not recorded (%s): %s", self._client_id, error.__class__.__name__
            )
