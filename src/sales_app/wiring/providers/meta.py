"""The Meta provider: the models Meta serves through its own model API (Muse Spark).

A provider of its own, not a flavour of another: its credentials, its endpoint and its models are
Meta's. It shares a technical adapter family with OpenAI because the two APIs speak the same
protocol; that is a fact about the adapter, not a reason to merge the providers.

Declared but not runnable yet: it has no `build`, so the runtime cannot construct it and no client
can be run on it. Two things must be settled first and are not done here: what its answers look
like when a structured output is asked for (`capabilities` says it is unverified), and the wording
of the adapter's errors, which names its provider. No call to Meta is made anywhere.
"""

import re

from sales_app.secret_store import ScopeKind
from sales_app.wiring.providers.types import (
    CredentialDeclaration,
    EmptySettings,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
)

KEY = "meta"
FAMILY = "openai_compatible"

META_API_KEY = "meta.api_key"
META_BASE_URL = "meta.base_url"

DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.LLM,
    label="Meta",
    family=FAMILY,
    settings_models={ProviderKind.LLM: EmptySettings},
    credentials=(
        CredentialDeclaration(
            key=META_API_KEY,
            label="Meta model API key",
            scope=ScopeKind.GLOBAL,
            required_when=Requirement.ALWAYS,
        ),
        # The endpoint is entered by the Admin: it is data about the deployment, not built in.
        CredentialDeclaration(
            key=META_BASE_URL,
            label="Meta model API base URL",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            required_when=Requirement.ALWAYS,
            pattern=re.compile(r"^https://[^\s/?#@]+(/[^\s?#]*)?$"),
            rule="must be an https URL without credentials",
        ),
    ),
    capabilities={"structured_output": "unverified"},
    build=None,
)
