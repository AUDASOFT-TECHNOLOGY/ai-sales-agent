"""Port implemented by every Business Brain (one per client)."""

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from sales_core.contracts import BrainDecision, BrainRequest
from sales_core.ports.llm import LLMProvider


@runtime_checkable
class Brain(Protocol):
    """Decides what to do for one prospect. Client knowledge lives entirely behind this call.

    A Brain receives its collaborators (e.g. an `LLMProvider`) at construction time, never
    through the request, so the Core stays independent of how a Brain reaches its model.
    """

    def decide(self, request: BrainRequest) -> BrainDecision: ...


# Entry point every Brain package exposes (named by `brain.factory` in client configuration).
# It receives the client's own LLM provider and its opaque Brain settings.
type BrainFactory = Callable[[LLMProvider, Mapping[str, object]], Brain]
