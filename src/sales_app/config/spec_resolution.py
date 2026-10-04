"""A client's canonical (v3) spec, resolved beside the pipeline the runtime uses.

`resolve_client_spec` follows the same first steps as `resolve_client_config` and stops where the
two differ in purpose:

    read client.yaml  ->  enabled?  ->  stored version  ->  normalize  ->  ClientSpec  ->  providers

It is read-only and parallel: nothing in the runtime, the registry or the APIs calls it yet, and its
result is not what `build_runtime` consumes. What it never does:

- read an environment variable or a secret (it has no parameter for either: the legacy `*_env`
  names are returned, never their values);
- build or call a provider (the registry is only asked what a provider declares);
- import a Brain: the Brains it may name are handed in (`known_brains`), never discovered, and a
  Brain's own settings rules (`SETTINGS_SPEC`) are not applied here, so `spec.brain.settings` is
  what the configuration says (that rule stays in the legacy pipeline until Brains are resolved by
  catalogue key);
- apply the deployment's ceilings (mode, allowed models): those are about running, not reading.

The order is the legacy one, on purpose: a client with `enabled: false` is excluded before anything
is validated, so a disabled client full of placeholders stays accepted.

Findings about providers (unknown, wrong role, not runnable yet, settings a provider refuses) are
returned apart from `errors`: today a provider the runtime cannot build is reported when the runtime
is built, not when the configuration is read, and the two must not be mixed yet.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from sales_app.config.canonical import SAME_AS_SOURCE, ClientSpec
from sales_app.config.normalize import NormalizationError, normalize
from sales_app.config.resolution import CONFIG_FILENAME, check_document, read_wiring, where
from sales_app.config_store import ConfigStore, ConfigStoreError
from sales_app.config_store.models import CLIENT_ID_PATTERN

if TYPE_CHECKING:  # the registry is only imported when a check needs it
    from sales_app.wiring.providers import ProviderRegistry

# Where a missing setting is a hint to publish a version, as in the legacy pipeline.
_SETTINGS_LOCATIONS = ("display_name", "timezone", "sheets.spreadsheet_id", "llm.model")


@dataclass(frozen=True, slots=True)
class SpecResolution:
    """What became of one client directory: a canonical spec, "disabled", or a list of problems."""

    spec: ClientSpec | None = None
    env_fallbacks: Mapping[str, str] = field(default_factory=dict)  # names only, never values
    disabled: bool = False
    errors: tuple[str, ...] = ()
    provider_problems: tuple[str, ...] = ()  # what the registry says; the spec is still returned
    config_source: str = "file"  # "store" or "file"
    config_version: int | None = None  # the stored version in force; None: from the file
    origin: str = "legacy"  # "legacy" or "canonical": what the input looked like

    @property
    def problems(self) -> tuple[str, ...]:
        return (*self.errors, *self.provider_problems)


def resolve_client_spec(
    directory: Path,
    *,
    store: ConfigStore | None = None,
    registry: "ProviderRegistry | None" = None,
    known_brains: Mapping[str, str] | None = None,
) -> SpecResolution:
    """The canonical spec of the client in `directory`; pure of effects beyond reading its files.

    `registry` is the providers to check against (default: those Revansa knows). `known_brains`
    maps a factory path to a Brain catalogue key; without it every Brain stays `legacy_factory`.
    Never raises for a bad client: every problem is in the result.
    """
    name = directory.name
    raw, problem = read_wiring(directory)
    if problem is not None or raw is None:
        return SpecResolution(errors=(problem or f"{name}: cannot read {CONFIG_FILENAME}",))

    if raw.get("enabled", True) is False:
        return SpecResolution(disabled=True)  # deliberately excluded: not validated, not built

    version = None
    if store is not None and CLIENT_ID_PATTERN.match(name):
        try:
            version = store.current(name)
        except ConfigStoreError as error:
            return SpecResolution(errors=(f"{name}: configuration store: {error}",))
    document = version.document if version is not None else None
    stored = f"configuration version {version.version}" if version is not None else None

    if document is not None and document.get("schema_version") in (1, 2):
        document_errors = check_document(document)
        if document_errors:
            return SpecResolution(errors=tuple(f"{name}: {stored}: {e}" for e in document_errors))

    try:
        normalized = normalize(raw, document, known_brains=known_brains)
    except NormalizationError as error:
        return SpecResolution(errors=_normalization_errors(name, error.problems, stored))

    try:
        spec = ClientSpec.model_validate(normalized.spec)
    except ValidationError as error:
        problems = tuple(f"{name}: {where(p)}" for p in error.errors())
        return SpecResolution(errors=(*problems, *_stored_note(name, stored)))

    if spec.client_id != name:
        return SpecResolution(
            errors=(f"{name}: client_id {spec.client_id!r} must equal the directory name",)
        )
    return SpecResolution(
        spec=spec,
        env_fallbacks=dict(normalized.env_fallbacks),
        provider_problems=tuple(f"{name}: {p}" for p in provider_problems(spec, registry)),
        config_source="store" if version is not None else "file",
        config_version=version.version if version is not None else None,
        origin=normalized.origin,
    )


def _stored_note(name: str, stored: str | None) -> tuple[str, ...]:
    return (f"{name}: settings in force come from {stored}",) if stored is not None else ()


def _normalization_errors(
    name: str, problems: tuple[str, ...], stored: str | None
) -> tuple[str, ...]:
    errors = tuple(f"{name}: {problem}" for problem in problems)
    if stored is not None:
        return (*errors, *_stored_note(name, stored))
    if any(
        problem.startswith(f"{loc}: Field required")
        for problem in problems
        for loc in _SETTINGS_LOCATIONS
    ):
        hint = (
            f"{name}: no configuration version is published; run "
            f"'sales-agent config import {name}' or 'sales-agent config publish {name} FILE'"
        )
        return (*errors, hint)
    return errors


# ---- providers ---------------------------------------------------------------------------------


def provider_problems(spec: ClientSpec, registry: "ProviderRegistry | None" = None) -> list[str]:
    """What the provider registry says about the providers a spec names.

    Nothing is built and nothing is called: only what each descriptor declares is read. A provider
    the registry does not know is reported (and its settings are left unchecked).
    """
    from sales_app.wiring.providers import ProviderKind, default_registry

    registry = registry if registry is not None else default_registry()
    problems: list[str] = []
    data = spec.model_dump(mode="json")

    def provider(path: str, key: str, role: ProviderKind, settings: Any, *, runnable: bool = True):
        descriptor = registry.get(key)
        if descriptor is None:
            problems.append(f"{path}.provider: unknown provider {key!r}")
            return None
        if not descriptor.plays(role):
            problems.append(f"{path}.provider: {key!r} cannot be used as a {role.value}")
            return descriptor
        model = descriptor.settings_models.get(role)
        if model is not None and settings is not None:
            try:
                model.model_validate(settings)
            except ValidationError as error:
                problems.extend(f"{path}.settings.{where(p)}" for p in error.errors())
        if runnable and not descriptor.runnable:
            problems.append(f"{path}.provider: {key!r} is known but cannot be run yet")
        return descriptor

    source = provider(
        "source", spec.source.provider, ProviderKind.SOURCE, data["source"]["settings"]
    )

    if spec.store is not None:
        store = provider(
            "store",
            spec.store.provider,
            ProviderKind.STORE,
            data["store"]["settings"],
            runnable=False,
        )
        shared = (
            spec.store.connection == SAME_AS_SOURCE and store is not None and source is not None
        )
        if shared and (spec.store.provider != spec.source.provider or not source.can_host_store):
            problems.append(
                f"store.connection: {SAME_AS_SOURCE!r} needs a source whose provider hosts "
                f"the store ({spec.source.provider!r} does not host {spec.store.provider!r})"
            )

    for index, channel in enumerate(spec.channels):
        provider(
            f"channels[{index}]",
            channel.provider,
            ProviderKind.CHANNEL,
            data["channels"][index]["settings"],
            runnable=channel.enabled,
        )

    if spec.llm.legacy is not None:
        provider("llm.legacy", spec.llm.legacy.provider, ProviderKind.LLM, None)
    return problems


__all__ = ["SpecResolution", "provider_problems", "resolve_client_spec"]
