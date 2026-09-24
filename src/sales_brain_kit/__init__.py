"""Shared, client-agnostic building blocks for Business Brains.

This package holds the "consultative sales" Brain family: its settings schema and its
`BrainSettingsSpec`. It depends only on `sales_core` (and pydantic). `sales_core` never imports it,
and it knows no client. A client Brain package uses it and may expose
`SETTINGS_SPEC = ConsultativeSalesSpec()` next to its factory.
"""

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
    "ConsultativeSalesSettings",
    "ConsultativeSalesSpec",
    "SettingsInvalidError",
    "parse_settings",
]
