"""The models Revansa knows, by stable key, and which provider serves each.

A model has a `key` (Revansa's own stable name, the one a client names in `llm.model`), the
`provider` that serves it, a `label`, and an `upstream_id`: the technical identifier that provider's
API wants. Providers declare their models in their descriptors, so adding a model means editing a
descriptor (or registering another one); the Core and the Brains never learn a model or a provider.

The upstream id is deployment data, never code. A descriptor declares none; the catalog is built
with a table of them (`ModelCatalog.from_registry(registry, upstream_ids)`). A model without one is
*known but not configured*: it exists, and it cannot run. No id is ever invented or deduced.

OpenAI and Meta stay two providers even if their APIs are alike: a model belongs to exactly one.
This module imports nothing else of `sales_app`, so descriptors and configuration can both use it.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

MODEL_KEY_PATTERN = r"^[a-z][a-z0-9-]{1,63}$"
UPSTREAM_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
_KEY = re.compile(MODEL_KEY_PATTERN)
_UPSTREAM_ID = re.compile(UPSTREAM_ID_PATTERN)
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ModelCatalogError(ValueError):
    """The catalog cannot be built as asked."""


class UnknownModelError(ModelCatalogError):
    """No model has this key."""


class ModelNotConfiguredError(ModelCatalogError):
    """The model is known, but no upstream id is configured for it: it cannot run."""


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    key: str
    provider: str  # the provider's key in the provider registry
    label: str
    upstream_id: str | None = None  # deployment data; a descriptor in code declares none
    # Whether the Admin keeps it switched on (Settings data). A descriptor in code declares it on.
    active: bool = True

    def __post_init__(self) -> None:
        if not _KEY.match(self.key):
            raise ModelCatalogError(f"model key {self.key!r} must match {MODEL_KEY_PATTERN}")
        if not _PROVIDER.match(self.provider):
            raise ModelCatalogError(f"{self.key}: {self.provider!r} is not a provider key")
        if not self.label.strip():
            raise ModelCatalogError(f"{self.key}: a model needs a label")
        if self.upstream_id is not None and not _UPSTREAM_ID.match(self.upstream_id):
            raise ModelCatalogError(f"{self.key}: the upstream id is not a valid identifier")

    @property
    def configured(self) -> bool:
        return self.upstream_id is not None


class ModelCatalog:
    def __init__(self, models: Iterable[ModelDescriptor] = ()) -> None:
        self._by_key: dict[str, ModelDescriptor] = {}
        for model in models:
            if model.key in self._by_key:
                raise ModelCatalogError(f"model {model.key!r} is declared twice")
            self._by_key[model.key] = model

    @classmethod
    def from_registry(
        cls, registry: Iterable[Any], upstream_ids: Mapping[str, str] | None = None
    ) -> "ModelCatalog":
        """The models the providers of `registry` declare, with the upstream ids given.

        `upstream_ids` maps a model key to its provider's identifier. A key no provider declares is
        an error (a typo would otherwise leave a model silently unconfigured)."""
        declared = [model for descriptor in registry for model in descriptor.models]
        return cls(declared).with_upstream_ids(upstream_ids or {})

    def with_upstream_ids(self, upstream_ids: Mapping[str, str]) -> "ModelCatalog":
        unknown = sorted(set(upstream_ids) - set(self._by_key))
        if unknown:
            raise ModelCatalogError(f"upstream ids given for unknown models: {unknown}")
        return ModelCatalog(
            replace(model, upstream_id=upstream_ids.get(model.key, model.upstream_id))
            for model in self._by_key.values()
        )

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_key))

    def find(self, key: str) -> ModelDescriptor | None:
        return self._by_key.get(key)

    def get(self, key: str) -> ModelDescriptor:
        model = self._by_key.get(key)
        if model is None:
            installed = ", ".join(self.keys()) or "none"
            raise UnknownModelError(f"unknown model {key!r} (known: {installed})")
        return model

    def provider_of(self, key: str) -> str:
        """The provider that serves the model. Raises `UnknownModelError`."""
        return self.get(key).provider

    def models_of(self, provider: str) -> tuple[ModelDescriptor, ...]:
        return tuple(m for m in self._by_key.values() if m.provider == provider)

    def runnable(self, key: str) -> ModelDescriptor:
        """The model with its upstream id, or `UnknownModelError` / `ModelNotConfiguredError`."""
        model = self.get(key)
        if model.upstream_id is None:
            raise ModelNotConfiguredError(
                f"model {key!r} ({model.provider}) has no upstream id configured, so it cannot run"
            )
        return model

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __len__(self) -> int:
        return len(self._by_key)
