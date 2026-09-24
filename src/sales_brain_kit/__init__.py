"""Shared, client-agnostic building blocks for Business Brains.

This package holds the "consultative sales" Brain family: its settings schema, its
`BrainSettingsSpec` and the Brain that runs on those settings (`ConsultativeSalesBrain`). It
depends only on `sales_core` (and pydantic). `sales_core` never imports it, and it knows no client.
A client Brain package uses it and may expose `SETTINGS_SPEC = ConsultativeSalesSpec()` and
`create_brain` next to its factory.
"""

from sales_brain_kit.consultative import (
    BrainOutputError,
    ConsultativeSalesBrain,
    create_brain,
)
from sales_brain_kit.readiness import ReadinessReport, check_readiness
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_brain_kit.spec import (
    FAMILY,
    SETTINGS_VERSION,
    ConsultativeSalesSpec,
    SettingsInvalidError,
    parse_settings,
)

__all__ = [
    "FAMILY",
    "SETTINGS_VERSION",
    "BrainOutputError",
    "ConsultativeSalesBrain",
    "ConsultativeSalesSettings",
    "ConsultativeSalesSpec",
    "ReadinessReport",
    "SettingsInvalidError",
    "check_readiness",
    "create_brain",
    "parse_settings",
]
