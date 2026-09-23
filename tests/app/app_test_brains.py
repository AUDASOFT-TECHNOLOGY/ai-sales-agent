"""Tiny stand-in Brain packages, loaded by dotted path exactly as real ones are.

They are generic on purpose: no client, no business rule.
"""

from collections.abc import Mapping

from sales_core.contracts import BrainDecision, BrainRequest, DecisionType, MessageDraft
from sales_core.ports import LLMProvider

CREATED: list[tuple[LLMProvider, Mapping[str, object]]] = []  # what the factory was given

NOT_CALLABLE = 42


class EchoBrain:
    def __init__(self, llm: LLMProvider, settings: Mapping[str, object]) -> None:
        self.llm = llm
        self.settings = settings

    def decide(self, request: BrainRequest) -> BrainDecision:
        tone = self.settings.get("tone", "neutral")
        return BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE,
            message=MessageDraft(text=f"[{tone}] hello {request.prospect.display_name}"),
        )


def good_brain(llm: LLMProvider, settings: Mapping[str, object]) -> EchoBrain:
    CREATED.append((llm, settings))
    return EchoBrain(llm, settings)


def not_a_brain(llm: LLMProvider, settings: Mapping[str, object]) -> object:
    return object()


def raising_brain(llm: LLMProvider, settings: Mapping[str, object]) -> EchoBrain:
    raise RuntimeError("cannot build this brain")
