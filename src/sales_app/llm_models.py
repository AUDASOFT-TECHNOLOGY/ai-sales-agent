"""Which models Admin Settings offers for each LLM provider.

Settings is where the Admin manages them: for OpenAI, a list of `{name, id, active}` kept in the
credential store (`openai.models`, a JSON array -- no separate storage). They sit on top of the
deployment's allow-list (`Settings.allowed_llm_models`), which stays a hard guardrail: a model must
pass both. Nothing is invented: until the Admin adds a model (or an older deployment still holds the
two legacy `openai.model_sol` / `openai.model_lune` ids, which are shown as models until the list is
first saved) a provider has no model of its own and keeps the old behaviour.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from sales_app.config.llm_registry import LLMRegistry
from sales_app.config.secrets import OPENAI_MODEL_LUNE, OPENAI_MODEL_SOL, OPENAI_MODELS
from sales_app.secret_store import GLOBAL_SCOPE, CredentialService, InvalidCredentialError

MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_NAME_LENGTH = 80

_LEGACY_OPENAI: Mapping[str, str] = {OPENAI_MODEL_SOL: "GPT Sol", OPENAI_MODEL_LUNE: "GPT Lune"}


@dataclass(frozen=True, slots=True)
class OfferedModel:
    name: str
    id: str
    active: bool = True


def validate_model(model: OfferedModel) -> OfferedModel:
    """The cleaned model, or InvalidCredentialError describing the rule that was broken."""
    name, model_id = model.name.strip(), model.id.strip()
    if not name or len(name) > MAX_NAME_LENGTH or any(ord(ch) < 32 for ch in name):
        raise InvalidCredentialError(f"model name must be 1-{MAX_NAME_LENGTH} printable characters")
    if not MODEL_ID_PATTERN.match(model_id):
        raise InvalidCredentialError("model id must be letters, digits, - _ . : only")
    return OfferedModel(name, model_id, model.active)


def _parse(raw: str) -> list[OfferedModel]:
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    models: list[OfferedModel] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            model = validate_model(
                OfferedModel(item.get("name"), item.get("id"), item.get("active") is True)  # type: ignore[arg-type]
            )
        except (InvalidCredentialError, AttributeError):
            continue  # a hand-edited entry that breaks the rules is ignored, not trusted
        if all(m.id != model.id for m in models):
            models.append(model)
    return models


def stored_models(credentials: CredentialService) -> list[OfferedModel] | None:
    """The OpenAI models of Settings; None when the Admin never saved the list and no legacy id
    exists either."""
    raw = credentials.reveal(GLOBAL_SCOPE, OPENAI_MODELS)
    if raw is not None:
        return _parse(raw)
    legacy = [
        OfferedModel(name, value)
        for key, name in _LEGACY_OPENAI.items()
        if (value := credentials.reveal(GLOBAL_SCOPE, key))
    ]
    return list({m.id: m for m in legacy}.values()) or None


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
