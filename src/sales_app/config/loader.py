"""Discovering, validating and resolving client configuration.

Nothing here raises for a bad client: every problem is collected, so one run reports them all.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from sales_app.config.brain_settings import prepare_brain_settings
from sales_app.config.llm_registry import EMPTY_LLM_REGISTRY, LLMRegistry
from sales_app.config.resolution import (
    CONFIG_FILENAME,
    check_document,
    merge_document,
    read_wiring,
    strip_settings,
    upgrade_document,
    where,
    with_legacy_inbound,
)
from sales_app.config.schema import ClientFile
from sales_app.config.secrets import Secrets
from sales_app.config_store import ConfigStore, ConfigStoreError
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_core.approval import ApprovalMode

# Safest first: a client's effective mode is the safer of its own and the deployment ceiling.
MODE_ORDER = (ApprovalMode.DRY_RUN, ApprovalMode.APPROVAL, ApprovalMode.AUTONOMOUS)


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """A validated client: its file, the mode it will really run in, and its secrets."""

    spec: ClientFile
    directory: Path
    effective_mode: ApprovalMode
    secrets: Secrets
    config_version: int | None = None  # the stored version in force; None: settings from the file
    config_source: str = "file"  # "store" or "file"

    @property
    def client_id(self) -> str:
        return self.spec.client_id

    @property
    def mode_clamped(self) -> bool:
        return self.effective_mode is not self.spec.mode

    @property
    def phone_number_id(self) -> str | None:
        return self.secrets.get_optional(self.spec.whatsapp.phone_number_id_env)


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    clients: tuple[ClientConfig, ...]  # enabled and valid
    disabled: tuple[str, ...]  # directory names of clients with `enabled: false`
    errors: tuple[str, ...]  # everything wrong, across all clients


def clamp_mode(requested: ApprovalMode, ceiling: ApprovalMode) -> ApprovalMode:
    return MODE_ORDER[min(MODE_ORDER.index(requested), MODE_ORDER.index(ceiling))]


@dataclass(frozen=True, slots=True)
class Resolution:
    """What became of one client directory: a config, "disabled", or a list of problems."""

    config: ClientConfig | None = None
    disabled: bool = False
    errors: tuple[str, ...] = ()


def load_client_configs(
    clients_dir: Path,
    *,
    env: Mapping[str, str],
    mode_ceiling: ApprovalMode,
    store: ConfigStore | None = None,
    llm_registry: LLMRegistry = EMPTY_LLM_REGISTRY,
) -> LoadedConfig:
    if not clients_dir.is_dir():
        return LoadedConfig((), (), (f"clients directory not found: {clients_dir}",))

    clients: list[ClientConfig] = []
    disabled: list[str] = []
    errors: list[str] = []
    directories = sorted(
        p for p in clients_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    )
    for directory in directories:
        resolution = resolve_client_config(
            directory, env=env, mode_ceiling=mode_ceiling, store=store, llm_registry=llm_registry
        )
        errors.extend(resolution.errors)
        if resolution.disabled:
            disabled.append(directory.name)
        elif resolution.config is not None:
            clients.append(resolution.config)
    errors.extend(_cross_client_errors(clients))
    return LoadedConfig(tuple(clients), tuple(disabled), tuple(errors))


def resolve_client_config(
    directory: Path,
    *,
    env: Mapping[str, str],
    mode_ceiling: ApprovalMode,
    store: ConfigStore | None = None,
    llm_registry: LLMRegistry = EMPTY_LLM_REGISTRY,
) -> Resolution:
    """Build one client's immutable config from its `client.yaml` and, if a version was published,
    its stored settings. Pure and side-effect free: it never raises for bad configuration and never
    touches anything already running, so a caller can keep its current config when this fails."""
    name = directory.name
    raw, problem = read_wiring(directory)
    if problem is not None or raw is None:
        return Resolution(errors=(problem or f"{name}: cannot read {CONFIG_FILENAME}",))

    if raw.get("enabled", True) is False:
        return Resolution(disabled=True)  # deliberately excluded: not validated, not built

    version = None
    if store is not None and CLIENT_ID_PATTERN.match(name):
        try:
            version = store.current(name)
        except ConfigStoreError as error:
            return Resolution(errors=(f"{name}: configuration store: {error}",))

    if version is not None:
        document_errors = check_document(version.document)
        if document_errors:
            return Resolution(
                errors=tuple(
                    f"{name}: configuration version {version.version}: {e}" for e in document_errors
                )
            )
        candidate = merge_document(strip_settings(raw), upgrade_document(version.document))
    else:
        candidate = with_legacy_inbound(raw)  # a client.yaml predates the inbound policy

    try:
        spec = ClientFile.model_validate(candidate)
    except ValidationError as error:
        problems = [f"{name}: {where(p)}" for p in error.errors()]
        if version is not None:
            problems.append(
                f"{name}: settings in force come from configuration version {version.version}"
            )
        elif any(_is_missing_setting(p) for p in error.errors()):
            problems.append(
                f"{name}: no configuration version is published; run "
                f"'sales-agent config import {name}' or 'sales-agent config publish {name} FILE'"
            )
        return Resolution(errors=tuple(problems))

    problems = []
    if spec.client_id != name:
        problems.append(f"{name}: client_id {spec.client_id!r} must equal the directory name")

    # A Brain that declares settings rules gets its settings validated and normalised here. An
    # unimportable Brain module is left for the runtime build to report, with more context.
    spec, brain_problems = prepare_brain_settings(spec, require_module=False)
    problems.extend(f"{name}: {message}" for message in brain_problems)
    if brain_problems and version is not None:
        problems.append(
            f"{name}: settings in force come from configuration version {version.version}"
        )

    effective = clamp_mode(spec.mode, mode_ceiling)
    secrets, missing = _resolve_secrets(spec, effective, env)
    problems.extend(f"{name}: {message}" for message in missing)
    llm_reason = llm_registry.check(spec.llm.provider, spec.llm.model)
    if llm_reason is not None:
        problems.append(f"{name}: {llm_reason}")
    if problems:
        return Resolution(errors=tuple(problems))
    config = ClientConfig(
        spec,
        directory,
        effective,
        secrets,
        config_version=version.version if version is not None else None,
        config_source="store" if version is not None else "file",
    )
    return Resolution(config=config)


_SETTINGS_LOCATIONS = {
    ("display_name",),
    ("timezone",),
    ("sheets", "spreadsheet_id"),
    ("llm", "model"),
}


def _is_missing_setting(problem: Mapping[str, object]) -> bool:
    location = tuple(problem["loc"])  # type: ignore[arg-type]
    return problem["type"] == "missing" and location in _SETTINGS_LOCATIONS


def _resolve_secrets(
    spec: ClientFile, effective: ApprovalMode, env: Mapping[str, str]
) -> tuple[Secrets, list[str]]:
    live = effective is not ApprovalMode.DRY_RUN  # a dry run never talks to WhatsApp
    wanted = [
        ("sheets.credentials_env", spec.sheets.credentials_env, True),
        ("llm.api_key_env", spec.llm.api_key_env, True),
        ("whatsapp.token_env", spec.whatsapp.token_env, live),
        ("whatsapp.app_secret_env", spec.whatsapp.app_secret_env, live),
        ("whatsapp.phone_number_id_env", spec.whatsapp.phone_number_id_env, live),
    ]
    values: dict[str, str] = {}
    missing: list[str] = []
    for label, variable, required in wanted:
        value = env.get(variable, "").strip()
        if value:
            values[variable] = value
        elif required:
            missing.append(f"environment variable {variable} ({label}) is not set")
    return Secrets(values), missing


def _cross_client_errors(clients: list[ClientConfig]) -> list[str]:
    """Two clients sharing an account is almost always a copy-paste mistake."""
    checks: list[tuple[str, Callable[[ClientConfig], str | None]]] = [
        ("WhatsApp phone_number_id", lambda c: c.phone_number_id),
        ("spreadsheet_id", lambda c: c.spec.sheets.spreadsheet_id),
        ("whatsapp.token_env", lambda c: c.spec.whatsapp.token_env),
        ("whatsapp.phone_number_id_env", lambda c: c.spec.whatsapp.phone_number_id_env),
    ]
    errors: list[str] = []
    for label, getter in checks:
        owners: dict[str, list[str]] = defaultdict(list)
        for client in clients:
            value = getter(client)
            if value:
                owners[value].append(client.client_id)
        for value, ids in owners.items():
            if len(ids) > 1:
                errors.append(f"{label} {value!r} is used by several clients: {', '.join(ids)}")
    return errors
