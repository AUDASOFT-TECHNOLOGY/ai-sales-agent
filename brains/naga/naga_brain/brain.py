"""Naga's Brain: the generic consultative sales Brain, run on Naga's settings.

This package is wiring only. The commercial intelligence (what is known, which product, what to ask,
what comes next, which language, what is paid how) is the kit's, and everything Naga-specific is
*data*: Naga's settings, stored with the client's configuration and edited by a business user. No
product, price, language, market, payment detail or process is written here.

The application finds `SETTINGS_SPEC` next to `create_brain` (named by `brain.factory`), so Naga's
settings are validated when they are published and when they are loaded.
"""

from collections.abc import Mapping

from sales_brain_kit import ConsultativeSalesBrain, ConsultativeSalesSpec, parse_settings
from sales_core.ports import LLMProvider

SETTINGS_SPEC = ConsultativeSalesSpec()


class NagaBrain(ConsultativeSalesBrain):
    """Implements `sales_core.ports.Brain` for Naga."""


def create_brain(llm: LLMProvider, settings: Mapping[str, object]) -> NagaBrain:
    """A `BrainFactory`: Naga's Brain from Naga's validated settings.

    Invalid or incomplete settings raise `SettingsInvalidError`, which the application reports as a
    Brain that cannot be built.
    """
    return NagaBrain(llm, parse_settings(settings))
