"""The LLM usage register: what each model call of a client's agent consumed.

The Core never calls a model and knows nothing of this: the runtime wraps the client's `LLMProvider`
in a `RecordingLLMProvider`, which hands every call to a `LLMUsageStore`. Recording is best effort:
a store that fails is logged (the type of the failure, never a message) and the call's result or
error goes on exactly as it would have. A figure the provider did not report is kept as None, never
as a made-up zero. Only counts are kept: never a prompt, an answer or a contact.
"""

import logging
import threading
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
    ) -> None:
        self._inner = inner
        self._store = store
        self._client_id = client_id
        self._provider = provider
        self._model = model  # the configured model: what a failed call is attributed to
        self._now = now

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = self._inner.complete(request)
        except Exception:
            self._record(STATUS_ERROR, self._model, None, None)
            raise
        self._record(
            STATUS_OK,
            response.model or self._model,
            response.input_tokens,
            response.output_tokens,
        )
        return response

    def _record(
        self, status: str, model: str, input_tokens: int | None, output_tokens: int | None
    ) -> None:
        try:
            self._store.record(
                LLMUsageRecord(
                    client_id=self._client_id,
                    occurred_at=self._now(),
                    provider=self._provider,
                    model=model,
                    status=status,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
        except Exception as error:  # noqa: BLE001 - never block the commercial answer
            log.warning(
                "LLM usage not recorded (%s): %s", self._client_id, error.__class__.__name__
            )
