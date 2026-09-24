"""A stand-in Brain package whose module declares settings rules (the consultative sales spec)."""

from collections.abc import Mapping

from app_test_brains import EchoBrain

from sales_brain_kit import ConsultativeSalesSpec
from sales_core.ports import LLMProvider

SETTINGS_SPEC = ConsultativeSalesSpec()
CREATED: list[Mapping[str, object]] = []  # the settings each Brain was built with


def create_brain(llm: LLMProvider, settings: Mapping[str, object]) -> EchoBrain:
    CREATED.append(settings)
    return EchoBrain(llm, settings)
