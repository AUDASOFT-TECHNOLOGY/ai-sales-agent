"""Provider descriptors: the one place that says what each provider is and needs.

`default_registry()` gives a fresh registry holding the providers Revansa knows. Adding a provider
means writing its module and listing it here; nothing else in the application names it.
"""

from sales_app.wiring.providers import google_sheets, hubspot, meta, openai, whatsapp
from sales_app.wiring.providers.registry import (
    DuplicateCredentialError,
    DuplicateProviderError,
    ProviderRegistry,
    UnknownProviderError,
    fake_descriptor,
)
from sales_app.wiring.providers.types import (
    ChannelSupport,
    CredentialDeclaration,
    EmptySettings,
    ModelList,
    Product,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
    UniquenessRule,
)


def default_registry() -> ProviderRegistry:
    return ProviderRegistry(
        [
            google_sheets.DESCRIPTOR,
            hubspot.DESCRIPTOR,
            whatsapp.DESCRIPTOR,
            openai.DESCRIPTOR,
            meta.DESCRIPTOR,
        ]
    )


__all__ = [
    "ChannelSupport",
    "CredentialDeclaration",
    "DuplicateCredentialError",
    "DuplicateProviderError",
    "EmptySettings",
    "ModelList",
    "Product",
    "ProviderDescriptor",
    "ProviderKind",
    "ProviderRegistry",
    "Requirement",
    "UniquenessRule",
    "UnknownProviderError",
    "default_registry",
    "fake_descriptor",
    "google_sheets",
    "hubspot",
    "meta",
    "openai",
    "whatsapp",
]
