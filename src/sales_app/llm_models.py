"""Which models Admin Settings offers for each LLM provider.

Settings is where the Admin manages them: for OpenAI, a list of `{name, id, active, key}` kept in the
credential store (`openai.models`, a JSON array -- no separate storage). They sit on top of the
deployment's allow-list (`Settings.allowed_llm_models`), which stays a hard guardrail: a model must
pass both. Nothing is invented: until the Admin adds a model (or an older deployment still holds the
two legacy `openai.model_sol` / `openai.model_lune` ids, which are shown as models until the list is
first saved) a provider has no model of its own and keeps the old behaviour.

The list's parsing lives in `sales_app.config.offered_models`, which the configuration loader also
uses: a client whose model is not active there is not ready.
"""

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace

from sales_app.config.llm_registry import LLMRegistry
from sales_app.config.offered_models import (
    MAX_NAME_LENGTH,
    MODEL_ID_PATTERN,
    ModelTest,
    OfferedModel,
    assign_keys,
    models_from,
    validate_model,
)
from sales_app.config.secrets import OPENAI_MODELS
from sales_app.model_catalog import ModelCatalog, ModelDescriptor
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    CredentialService,
    InvalidCredentialError,
    SecretStoreError,
)
from sales_app.wiring.providers import ProviderDescriptor, ProviderKind, ProviderRegistry

__all__ = [
    "MAX_NAME_LENGTH",
    "MODEL_ID_PATTERN",
    "CatalogModelView",
    "ModelTest",
    "OfferedModel",
    "assign_keys",
    "catalog_models",
    "check_model",
    "configured_models",
    "foreign_model_keys",
    "model_names",
    "model_tests",
    "record_test",
    "save_models",
    "settings_catalog",
    "stored_models",
    "validate_model",
]


def stored_models(credentials: CredentialService) -> list[OfferedModel] | None:
    """The OpenAI models of Settings; None when the Admin never saved the list and no legacy id
    exists either."""
    return models_from(lambda key: credentials.reveal(GLOBAL_SCOPE, key))


def save_models(
    credentials: CredentialService,
    models: list[OfferedModel],
    *,
    actor: str,
    reserved: Collection[str] = (),
    apply: bool = True,
) -> list[OfferedModel]:
    """Replaces the list, giving a key to each model that has none (a key already held never
    changes; `reserved` are the keys of other providers' models, which no key may take).
    InvalidCredentialError if a model breaks a rule, an id repeats or the list outgrows what the
    store accepts. `apply=False` stores without rebuilding the running clients: only for a change
    that no client reads (see `record_test`)."""
    cleaned = assign_keys([validate_model(m) for m in models], reserved)
    if len({m.id for m in cleaned}) != len(cleaned):
        raise InvalidCredentialError("a model id may only appear once")
    payload = json.dumps([_entry(m) for m in cleaned])
    credentials.save(GLOBAL_SCOPE, OPENAI_MODELS, payload, actor=actor, apply=apply)
    return cleaned


def _entry(model: OfferedModel) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": model.name,
        "id": model.id,
        "active": model.active,
        "key": model.key,
    }
    if model.last_test is not None:  # only a model that was tested carries a record
        entry["last_test"] = {"outcome": model.last_test.outcome, "at": model.last_test.at}
    return entry


def record_test(
    credentials: CredentialService,
    model_id: str,
    record: ModelTest,
    *,
    actor: str,
    reserved: Collection[str] = (),
) -> bool:
    """Keeps how the last test of the model `model_id` ended, with the model. False if the list no
    longer holds that id (the result is then not kept) or if it cannot be stored. The list is read
    again here, so a change made while the test ran is not overwritten. Nothing is rebuilt: no
    running client reads a test record. `reserved` are the keys of other providers' models (see
    `foreign_model_keys`): saving gives a key to a model that has none, and must not take one."""
    models = list(stored_models(credentials) or [])
    if all(m.id != model_id for m in models):
        return False
    updated = [replace(m, last_test=record) if m.id == model_id else m for m in models]
    try:
        save_models(credentials, updated, actor=actor, reserved=reserved, apply=False)
    except (InvalidCredentialError, SecretStoreError):
        return False
    return True


def foreign_model_keys(providers: ProviderRegistry, list_key: str) -> frozenset[str]:
    """The keys of the models declared by the providers whose list is not `list_key`: a model of
    this list may not take one of them (a model belongs to exactly one provider)."""
    return frozenset(
        model.key
        for descriptor in providers.list(ProviderKind.LLM)
        if descriptor.model_list is None or descriptor.model_list.key != list_key
        for model in descriptor.models
    )


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


def settings_catalog(providers: ProviderRegistry, credentials: CredentialService) -> ModelCatalog:
    """The models Revansa knows, as Settings holds them: the single source of the Model IDs.

    Each model provider contributes the models its descriptor declares (known, not configured) and,
    when it keeps a list in Settings, the Admin's models: one whose key is a declared model's gives
    it its upstream id, any other is a model of its own. Nothing is invented: a provider without a
    list in Settings, or a model the Admin did not add, has no upstream id and cannot run."""
    models: list[ModelDescriptor] = []
    for descriptor, entries in _admin_models(providers, credentials):
        declared = {m.key: m for m in descriptor.models}
        for entry in entries:
            models.append(
                ModelDescriptor(
                    key=entry.key,  # type: ignore[arg-type]  # assign_keys gave it one
                    provider=descriptor.key,
                    label=entry.name,
                    upstream_id=entry.id,
                    active=entry.active,
                )
            )
            declared.pop(entry.key, None)  # type: ignore[arg-type]
        models.extend(declared.values())  # declared, and not (yet) added by the Admin
    return ModelCatalog(models)


def _admin_models(
    providers: ProviderRegistry, credentials: CredentialService
) -> list[tuple[ProviderDescriptor, list[OfferedModel]]]:
    """Each model provider with the models the Admin holds for it in Settings (keys assigned)."""
    found: list[tuple[ProviderDescriptor, list[OfferedModel]]] = []
    for descriptor in providers.list(ProviderKind.LLM):
        source = descriptor.model_list
        if source is None:
            found.append((descriptor, []))
            continue
        entries = models_from(
            lambda key: credentials.reveal(GLOBAL_SCOPE, key), source.key, source.legacy_keys
        )
        reserved = foreign_model_keys(providers, source.key)
        found.append((descriptor, assign_keys(entries or [], reserved)))
    return found


def model_tests(
    providers: ProviderRegistry, credentials: CredentialService
) -> dict[str, ModelTest]:
    """Model key -> how its last test ended. A model that was never tested has no entry."""
    return {
        entry.key: entry.last_test  # type: ignore[misc]  # assign_keys gave every entry a key
        for _descriptor, entries in _admin_models(providers, credentials)
        for entry in entries
        if entry.last_test is not None
    }


@dataclass(frozen=True, slots=True)
class CatalogModelView:
    """A model of the catalog as the Admin shows it. No upstream id: only whether one is set."""

    key: str
    label: str
    configured: bool  # an upstream id is configured for it
    active: bool  # configured, and the Admin has not switched it off
    runnable: bool  # configured, active, and its provider is actually wired in this deployment
    last_test: ModelTest | None = None  # how its last real test ended; None: never tested


def catalog_models(
    catalog: ModelCatalog,
    wired: Collection[str],
    tests: Mapping[str, ModelTest] | None = None,
) -> dict[str, tuple[CatalogModelView, ...]]:
    """Provider -> the models the catalog holds for it, whether or not that provider is wired.
    Read-only: a model never runs because it is listed here. `tests` says, by model key, how the
    last real test of each model ended."""
    tests = tests or {}
    keys = catalog.keys()
    providers = sorted({catalog.provider_of(key) for key in keys})
    return {
        provider: tuple(
            CatalogModelView(
                key=m.key,
                label=m.label,
                configured=m.configured,
                active=m.configured and m.active,
                runnable=m.configured and m.active and provider in wired,
                last_test=tests.get(m.key),
            )
            for m in sorted(catalog.models_of(provider), key=lambda m: m.key)
        )
        for provider in providers
    }


def check_model(
    registry: LLMRegistry,
    credentials: CredentialService,
    providers: ProviderRegistry,
    provider: str,
    model: str,
) -> str | None:
    """None if `model` (the Model ID the provider is called with) may be assigned to a client under
    `provider`; otherwise the reason it may not. Used wherever the Admin assigns a model, never when
    a client's configuration is loaded (an existing configuration is read as it always was).

    The deployment's allow-list comes first. Then, for a provider whose models the Admin keeps in
    Settings, the model must be one of them and switched on: with no model configured there, nothing
    can be assigned, and an unknown or switched-off model is refused for what it is. A provider
    with no list in Settings is bound by the allow-list alone. A missing API key is not checked
    here: the assignment is allowed and the client is simply not ready until it is set."""
    reason = registry.check(provider, model)
    if reason is not None:
        return reason
    descriptor = providers.get(provider)
    source = descriptor.model_list if descriptor is not None else None
    if source is None:
        return None
    models = models_from(
        lambda key: credentials.reveal(GLOBAL_SCOPE, key), source.key, source.legacy_keys
    )
    if not models:
        return (
            f"no model is configured for provider {provider!r} in Settings: add one there "
            "before assigning a model to a client"
        )
    chosen = next((m for m in models if m.id == model), None)
    if chosen is None:
        return f"llm.model {model!r} is not configured in Settings for provider {provider!r}"
    if not chosen.active:
        return (
            f"llm.model {model!r} is switched off in Settings for provider {provider!r}: "
            "switch it on, or choose another model"
        )
    return None
