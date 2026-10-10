"""The OpenAI models Admin Settings offers, read from the credential store.

Kept apart from `sales_app.llm_models` (which needs the credential service and the registry) so the
configuration loader can ask the same question the Admin API does -- which model ids are active --
without a dependency cycle. The list is a JSON array of `{name, id, active}` under `openai.models`;
until the Admin first saves it, the two legacy `openai.model_sol` / `openai.model_lune` ids stand
in for it. Nothing is invented: no stored list and no legacy id means "Settings holds no list".
"""

import json
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace

from sales_app.config.secrets import OPENAI_MODEL_LUNE, OPENAI_MODEL_SOL, OPENAI_MODELS
from sales_app.model_catalog import MODEL_KEY_PATTERN
from sales_app.secret_store import InvalidCredentialError

MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_NAME_LENGTH = 80

# The legacy credentials that stand in for the list until the Admin first saves it: key -> name.
LEGACY_OPENAI_MODELS: Mapping[str, str] = {
    OPENAI_MODEL_SOL: "GPT Sol",
    OPENAI_MODEL_LUNE: "GPT Lune",
}

_KEY = re.compile(MODEL_KEY_PATTERN)
_MAX_KEY_LENGTH = 64


_OUTCOME = re.compile(r"^[a-z][a-z_]{0,39}$")


@dataclass(frozen=True, slots=True)
class ModelTest:
    """How the last real test of a model ended, and when. Nothing else is kept of it: no request,
    no answer, no credential."""

    outcome: str  # e.g. "usable", "model_not_found"
    at: str  # ISO 8601, UTC


@dataclass(frozen=True, slots=True)
class OfferedModel:
    """A model of the Admin's list. `id` is what the provider is called with; `key` is Revansa's
    stable name for it in the model catalog, derived from the name once and kept (None: not yet)."""

    name: str
    id: str
    active: bool = True
    key: str | None = None
    last_test: ModelTest | None = None  # None: never tested


def validate_model(model: OfferedModel) -> OfferedModel:
    """The cleaned model, or InvalidCredentialError describing the rule that was broken."""
    name, model_id = model.name.strip(), model.id.strip()
    if not name or len(name) > MAX_NAME_LENGTH or any(ord(ch) < 32 for ch in name):
        raise InvalidCredentialError(f"model name must be 1-{MAX_NAME_LENGTH} printable characters")
    if not MODEL_ID_PATTERN.match(model_id):
        raise InvalidCredentialError("model id must be letters, digits, - _ . : only")
    if model.key is not None and not _KEY.match(model.key):
        raise InvalidCredentialError("model key must be lowercase letters, digits and - only")
    return OfferedModel(name, model_id, model.active, model.key, model.last_test)


def derive_key(*sources: str) -> str:
    """A catalog key from the first source that makes a valid one ("Big Model 2" -> "big-model-2")."""
    for text in sources:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        if slug and not slug[0].isalpha():
            slug = f"model-{slug}"
        slug = slug[:_MAX_KEY_LENGTH].strip("-")
        if _KEY.match(slug):
            return slug
    return "model"


def assign_keys(models: list[OfferedModel], reserved: Collection[str] = ()) -> list[OfferedModel]:
    """Every model with a key that is valid and unique among them and outside `reserved` (the keys
    of other providers' models). A key already held is kept; the first holder of a repeated key
    keeps it. Only models without a usable key get a new one, so keys never change once set."""
    taken = set(reserved)
    keep: list[bool] = []
    for model in models:
        usable = model.key is not None and _KEY.match(model.key) and model.key not in taken
        if usable:
            taken.add(model.key)  # type: ignore[arg-type]
        keep.append(bool(usable))
    result: list[OfferedModel] = []
    for model, kept in zip(models, keep, strict=True):
        if kept:
            result.append(model)
            continue
        base = derive_key(model.name, model.id)
        key, number = base, 1
        while key in taken:
            number += 1
            suffix = f"-{number}"
            key = f"{base[: _MAX_KEY_LENGTH - len(suffix)]}{suffix}"
        taken.add(key)
        result.append(replace(model, key=key))
    return result


def _parse(raw: str) -> list[OfferedModel]:
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    models: list[OfferedModel] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        tested = item.get("last_test")
        record = (
            ModelTest(tested["outcome"], tested["at"])
            if isinstance(tested, dict)
            and isinstance(tested.get("outcome"), str)
            and _OUTCOME.match(tested["outcome"])
            and isinstance(tested.get("at"), str)
            and 0 < len(tested["at"]) <= 40
            else None
        )
        try:
            model = validate_model(
                OfferedModel(
                    item.get("name"),  # type: ignore[arg-type]
                    item.get("id"),  # type: ignore[arg-type]
                    item.get("active") is True,
                    key if isinstance(key, str) and _KEY.match(key) else None,
                    record,
                )
            )
        except (InvalidCredentialError, AttributeError):
            continue  # a hand-edited entry that breaks the rules is ignored, not trusted
        if all(m.id != model.id for m in models):
            models.append(model)
    return models


def models_from(
    read: Callable[[str], str | None],
    list_key: str = OPENAI_MODELS,
    legacy_keys: Mapping[str, str] = LEGACY_OPENAI_MODELS,
) -> list[OfferedModel] | None:
    """The models of Settings, given a function that reads a global credential by key; None when
    the Admin never saved the list (under `list_key`) and no legacy id exists either. Models are
    returned as stored: a key is only there if one was saved (see `assign_keys`)."""
    raw = read(list_key)
    if raw is not None:
        return _parse(raw)
    legacy = [
        OfferedModel(name, value) for key, name in legacy_keys.items() if (value := read(key))
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
