"""The OpenAI provider: the models Revansa offers under it (GPT Lune, GPT Sol).

This module declares the provider (its credentials, its optional account settings, how to build its
client) and the models it serves by Revansa's key. Their upstream ids are not here: they are
deployment data given to the model catalog (`sales_app.model_catalog`).
"""

import re

from sales_app.config import ClientConfig
from sales_app.config.offered_models import LEGACY_OPENAI_MODELS
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODELS,
    OPENAI_ORGANIZATION,
    OPENAI_PROJECT,
)
from sales_app.model_catalog import ModelDescriptor
from sales_app.secret_store import ScopeKind
from sales_app.wiring.providers.types import (
    CredentialDeclaration,
    EmptySettings,
    ModelList,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
)
from sales_integrations.openai import OpenAIProvider

KEY = "openai"
FAMILY = "openai_compatible"

_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ACCOUNT_ID_RULE = "must be an OpenAI id (letters, digits, - _ . only)"
_HTTPS_URL = re.compile(r"^https://[^\s/?#@]+(/[^\s?#]*)?$")
_HTTPS_URL_RULE = "must be an https URL without credentials, e.g. https://api.openai.com/v1"


def build_provider(config: ClientConfig) -> OpenAIProvider:
    llm = config.spec.llm
    return OpenAIProvider(
        model=llm.model,
        api_key=config.secrets.get(OPENAI_API_KEY),
        organization=config.secrets.get_optional(OPENAI_ORGANIZATION),
        project=config.secrets.get_optional(OPENAI_PROJECT),
        base_url=config.secrets.get_optional(OPENAI_BASE_URL),
    )


DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.LLM,
    label="OpenAI",
    family=FAMILY,
    settings_models={ProviderKind.LLM: EmptySettings},
    credentials=(
        CredentialDeclaration(
            key=OPENAI_API_KEY,
            label="OpenAI API key",
            scope=ScopeKind.GLOBAL,
            required_when=Requirement.ALWAYS,
        ),
        CredentialDeclaration(
            key=OPENAI_ORGANIZATION,
            label="OpenAI organization",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            pattern=_ACCOUNT_ID,
            rule=_ACCOUNT_ID_RULE,
        ),
        CredentialDeclaration(
            key=OPENAI_PROJECT,
            label="OpenAI project",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            pattern=_ACCOUNT_ID,
            rule=_ACCOUNT_ID_RULE,
        ),
        CredentialDeclaration(
            key=OPENAI_BASE_URL,
            label="OpenAI base URL",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            pattern=_HTTPS_URL,
            rule=_HTTPS_URL_RULE,
        ),
    ),
    connection_keys=(OPENAI_API_KEY, OPENAI_ORGANIZATION, OPENAI_PROJECT, OPENAI_BASE_URL),
    capabilities={"structured_output": "json_schema"},
    models=(
        ModelDescriptor("gpt-lune", KEY, "GPT Lune"),
        ModelDescriptor("gpt-sol", KEY, "GPT Sol"),
    ),
    model_list=ModelList(OPENAI_MODELS, LEGACY_OPENAI_MODELS),
    build=build_provider,
)
