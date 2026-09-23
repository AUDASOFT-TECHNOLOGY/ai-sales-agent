"""Scripted language-model provider for tests. Never calls a model."""

from collections import deque
from collections.abc import Iterable

from sales_core.contracts import LLMRequest, LLMResponse

type Scripted = LLMResponse | str | Exception

FAKE_MODEL = "fake-model"


def _to_response(item: LLMResponse | str) -> LLMResponse:
    return item if isinstance(item, LLMResponse) else LLMResponse(text=item, model=FAKE_MODEL)


class FakeLLMProvider:
    """Implements `sales_core.ports.LLMProvider`.

    Replies come from a FIFO script (`responses` / `enqueue`); once it is empty the optional
    `default` is used, otherwise `LookupError` is raised so a test never silently gets a reply
    it did not plan for. A scripted `Exception` is raised instead of returned. Every request is
    recorded, including the ones that end in an exception.
    """

    def __init__(
        self,
        responses: Iterable[Scripted] = (),
        *,
        default: LLMResponse | str | None = None,
    ) -> None:
        self._script: deque[Scripted] = deque(responses)
        self._default = default
        self._calls: list[LLMRequest] = []

    def enqueue(self, *items: Scripted) -> None:
        self._script.extend(items)

    @property
    def calls(self) -> tuple[LLMRequest, ...]:
        return tuple(self._calls)

    @property
    def last_request(self) -> LLMRequest | None:
        return self._calls[-1] if self._calls else None

    def complete(self, request: LLMRequest) -> LLMResponse:
        self._calls.append(request)
        if self._script:
            item = self._script.popleft()
        elif self._default is not None:
            item = self._default
        else:
            raise LookupError("FakeLLMProvider has no scripted response left")
        if isinstance(item, Exception):
            raise item
        return _to_response(item)
