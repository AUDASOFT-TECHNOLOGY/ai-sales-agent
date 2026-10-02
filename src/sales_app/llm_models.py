"""Which models Admin Settings offers for each LLM provider.

Settings is where the Admin manages them: for OpenAI, a list of `{name, id, active}` kept in the
credential store (`openai.models`, a JSON array -- no separate storage). They sit on top of the
deployment's allow-list (`Settings.allowed_llm_models`), which stays a hard guardrail: a model must
pass both. Nothing is invented: until the Admin adds a model (or an older deployment still holds the
two legacy `openai.model_sol` / `openai.model_lune` ids, which are shown as models until the list is
first saved) a provider has no model of its own and keeps the old behaviour.

The list's parsing lives in `sales_app.config.offered_models`, which the configuration loader also
uses: a client whose model is not active there is not ready.
"""

import json

from sales_app.config.llm_registry import LLMRegistry
from sales_app.config.offered_models import (
    MAX_NAME_LENGTH,
    MODEL_ID_PATTERN,
    OfferedModel,
    models_from,
    validate_model,
)
from sales_app.config.secrets import OPENAI_MODELS
from sales_app.secret_store import GLOBAL_SCOPE, CredentialService, InvalidCredentialError

__all__ = [
    "MAX_NAME_LENGTH",
    "MODEL_ID_PATTERN",
    "OfferedModel",
    "check_model",
    "configured_models",
    "model_names",
    "save_models",
    "stored_models",
    "validate_model",
]


def stored_models(credentials: CredentialService) -> list[OfferedModel] | None:
    """The OpenAI models of Settings; None when the Admin never saved the list and no legacy id
    exists either."""
    return models_from(lambda key: credentials.reveal(GLOBAL_SCOPE, key))


def save_models(
    credentials: CredentialService, models: list[OfferedModel], *, actor: str
) -> list[OfferedModel]:
    """Replaces the list. InvalidCredentialError if a model breaks a rule, an id repeats or the
    list outgrows what the store accepts."""
    cleaned = [validate_model(m) for m in models]
    if len({m.id for m in cleaned}) != len(cleaned):
        raise InvalidCredentialError("a model id may only appear once")
    payload = json.dumps([{"name": m.name, "id": m.id, "active": m.active} for m in cleaned])
    credentials.save(GLOBAL_SCOPE, OPENAI_MODELS, payload, actor=actor)
    return cleaned


def configured_models(credentials: CredentialService) -> dict[str, list[str]]:
    """Provider -> the ids a client may pick (the active ones). A provider is only present once
    Settings holds a list for it, so deactivating every model offers none rather than all."""
    models = stored_models(credentials)
    if models is None:
        return {}
    return {"openai": [m.id for m in models if m.active]}


def model_names(credentials: CredentialService) -> dict[str, dict[str, str]]:
    """Provider -> {model id -> readable name}, for showing a client's choices."""
    models = stored_models(credentials)
    return {"openai": {m.id: m.name for m in models}} if models else {}


def check_model(
    registry: LLMRegistry, credentials: CredentialService, provider: str, model: str
) -> str | None:
    """None if the model may be used; otherwise the reason it may not."""
    reason = registry.check(provider, model)
    if reason is not None:
        return reason
    offered = configured_models(credentials).get(provider)
    if offered is not None and model not in offered:
        return f"llm.model {model!r} is not configured in Settings for provider {provider!r}"
    return None
