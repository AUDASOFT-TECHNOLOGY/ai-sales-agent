"""Which clients use which model, and what a change to the Admin's model list would do to them.

A model belongs to the clients whose settings name it: the model published for the client (or, with
nothing published, the one in its `client.yaml`), and the one its running configuration was built
with, which can differ for a moment while a change is being applied or after one failed. Disabled
clients count: they run on it the day they are activated.

Read-only. It decides nothing by itself: the Admin API refuses, or warns, from what it reports.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sales_app.config.offered_models import OfferedModel, models_from
from sales_app.config.published import published_view
from sales_app.config.resolution import read_wiring
from sales_app.config_store import ConfigStore
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.secret_store import GLOBAL_SCOPE

if TYPE_CHECKING:  # the Application is only read here
    from sales_app.bootstrap import Application
    from sales_app.wiring.providers import ProviderRegistry


@dataclass(frozen=True, slots=True)
class ModelImpact:
    """What a change would do to the clients that use the models it touches."""

    removed: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # gone, or its id changed
    deactivated: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # switched off

    @property
    def removed_clients(self) -> list[str]:
        return sorted({client for clients in self.removed.values() for client in clients})

    @property
    def deactivated_clients(self) -> list[str]:
        return sorted({client for clients in self.deactivated.values() for client in clients})


def list_owner(providers: "ProviderRegistry", list_key: str) -> str | None:
    """The provider whose models the Admin keeps under the credential `list_key`."""
    from sales_app.wiring.providers import ProviderKind

    return next(
        (
            d.key
            for d in providers.list(ProviderKind.LLM)
            if d.model_list is not None and d.model_list.key == list_key
        ),
        None,
    )


def models_in_use(
    clients_dir: Path,
    store: ConfigStore,
    running: Iterable[Any],
    provider: str,
) -> dict[str, list[str]]:
    """Model id -> the clients (sorted) that use it, for the models of `provider`. A client whose
    settings name no provider counts for any (the model id is what matters)."""
    used: dict[str, set[str]] = {}

    def note(client_id: str, llm: Any) -> None:
        if not isinstance(llm, Mapping):
            return
        model, named = llm.get("model"), llm.get("provider")
        if isinstance(model, str) and model and (named is None or named == provider):
            used.setdefault(model, set()).add(client_id)

    if clients_dir.is_dir():
        for directory in sorted(clients_dir.iterdir()):
            name = directory.name
            if not directory.is_dir() or name.startswith(("_", ".")):
                continue
            if not CLIENT_ID_PATTERN.match(name):
                continue
            raw, _problem = read_wiring(directory)
            if raw is not None:
                note(name, published_view(store, name, raw).get("llm"))
    for config in running:
        note(
            config.client_id, {"provider": config.spec.llm.provider, "model": config.spec.llm.model}
        )
    return {model: sorted(clients) for model, clients in used.items()}


def clients_using_models(application: "Application", provider: str) -> dict[str, list[str]]:
    return models_in_use(
        application.settings.clients_dir,
        application.config_store,
        application.registry.configs(),
        provider,
    )


def impact(
    before: Iterable[OfferedModel],
    after: Iterable[OfferedModel],
    in_use: Mapping[str, list[str]],
) -> ModelImpact:
    """What going from the list `before` to the list `after` does to the clients in `in_use`.

    A model of `before` that `after` no longer holds under the same id (deleted, or its id changed)
    is removed; one that was on and is now off is deactivated. A model that was already off and
    stays off costs its clients nothing new, and a name change costs nothing."""
    now = {m.id: m for m in after}
    removed: dict[str, tuple[str, ...]] = {}
    deactivated: dict[str, tuple[str, ...]] = {}
    for model in before:
        clients = in_use.get(model.id)
        if not clients:
            continue
        kept = now.get(model.id)
        if kept is None:
            removed[model.id] = tuple(clients)
        elif model.active and not kept.active:
            deactivated[model.id] = tuple(clients)
    return ModelImpact(removed, deactivated)


def impact_of_credential_write(
    application: "Application", key: str, value: str | None
) -> ModelImpact:
    """What storing `value` under the global credential `key` (None: removing it) would do to the
    clients that use a model, when `key` is where a provider's model list is kept (or one of the
    older credentials that stand in for it). Any other key has no effect on a model."""
    from sales_app.wiring.providers import ProviderKind

    def stored(name: str) -> str | None:
        return application.credentials.reveal(GLOBAL_SCOPE, name)

    removed: dict[str, tuple[str, ...]] = {}
    deactivated: dict[str, tuple[str, ...]] = {}
    for descriptor in application.provider_registry.list(ProviderKind.LLM):
        source = descriptor.model_list
        if source is None or (key != source.key and key not in source.legacy_keys):
            continue

        def after_write(name: str) -> str | None:
            return value if name == key else stored(name)

        before = models_from(stored, source.key, source.legacy_keys) or []
        after = models_from(after_write, source.key, source.legacy_keys) or []
        effect = impact(before, after, clients_using_models(application, descriptor.key))
        removed.update(effect.removed)
        deactivated.update(effect.deactivated)
    return ModelImpact(removed, deactivated)
