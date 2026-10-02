"""The OpenAI models Admin Settings offers, read from the credential store.

Kept apart from `sales_app.llm_models` (which needs the credential service and the registry) so the
configuration loader can ask the same question the Admin API does -- which model ids are active --
without a dependency cycle. The list is a JSON array of `{name, id, active}` under `openai.models`;
until the Admin first saves it, the two legacy `openai.model_sol` / `openai.model_lune` ids stand
in for it. Nothing is invented: no stored list and no legacy id means "Settings holds no list".
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sales_app.config.secrets import OPENAI_MODEL_LUNE, OPENAI_MODEL_SOL, OPENAI_MODELS
from sales_app.secret_store import InvalidCredentialError

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


def models_from(read: Callable[[str], str | None]) -> list[OfferedModel] | None:
    """The models of Settings, given a function that reads a global credential by key; None when
    the Admin never saved the list and no legacy id exists either."""
    raw = read(OPENAI_MODELS)
    if raw is not None:
        return _parse(raw)
    legacy = [
        OfferedModel(name, value) for key, name in _LEGACY_OPENAI.items() if (value := read(key))
    ]
    return list({m.id: m for m in legacy}.values()) or None


def inactive_model_reason(
    models: list[OfferedModel] | None, provider: str, model: str
) -> str | None:
    """Why `model` may not run, if Settings holds a list for OpenAI and it is not active there.
    No list (None) means Settings restricts nothing; an empty list means no model is active."""
    if provider != "openai" or models is None:
        return None
    if any(m.id == model and m.active for m in models):
        return None
    return f"LLM model {model!r} is not active in Settings"
