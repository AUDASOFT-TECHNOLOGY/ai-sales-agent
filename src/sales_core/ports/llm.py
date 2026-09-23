"""Port for language-model providers (OpenAI first)."""

from typing import Protocol, runtime_checkable

from sales_core.contracts import LLMRequest, LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    """Injected into Brains. The Core itself never calls a model."""

    def complete(self, request: LLMRequest) -> LLMResponse: ...
