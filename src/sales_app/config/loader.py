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
from sales_app.config.offered_models import inactive_model_reason, models_from
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
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_ORGANIZATION,
    OPENAI_PROJECT,
    SHEETS_CREDENTIALS_FILE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    Secrets,
)
from sales_app.config_store import ConfigStore, ConfigStoreError
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.secret_store import GLOBAL_SCOPE, SecretStore, SecretStoreError, client_scope
from sales_core.approval import ApprovalMode

# Safest first: a client's effective mode is the safer of its own and the deployment ceiling.
MODE_ORDER = (ApprovalMode.DRY_RUN, ApprovalMode.APPROVAL, ApprovalMode.AUTONOMOUS)


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """A validated client: its file, the mode it will really run in, and its secrets.

    `missing_credentials` lists what this client still needs before it can run (empty: ready). A
    client with something missing is valid, just not ready: it is never built or run.
    """

    spec: ClientFile
    directory: Path
    effective_mode: ApprovalMode
    secrets: Secrets
    config_version: int | None = None  # the stored version in force; None: settings from the file
    config_source: str = "file"  # "store" or "file"
    missing_credentials: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.missing_credentials

    @property
    def client_id(self) -> str:
        return self.spec.client_id

    @property
    def mode_clamped(self) -> bool:
        return self.effective_mode is not self.spec.mode

    @property
    def phone_number_id(self) -> str | None:
        return self.secrets.get_optional(WHATSAPP_PHONE_NUMBER_ID)


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
    secret_store: SecretStore | None = None,
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
            directory,
            env=env,
            mode_ceiling=mode_ceiling,
            store=store,
            llm_registry=llm_registry,
            secret_store=secret_store,
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
    secret_store: SecretStore | None = None,
) -> Resolution:
    """Build one client's immutable config from its `client.yaml` and, if a version was published,
    its stored settings. Pure and side-effect free: it never raises for bad configuration and never
    touches anything already running, so a caller can keep its current config when this fails.

    Credentials that are not set do not make the configuration invalid: the client comes back with
    `missing_credentials` filled in (not ready). Structural problems stay errors."""
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
    try:
        secrets, missing = _resolve_secrets(spec, effective, env, secret_store)
    except SecretStoreError as error:  # the store itself is unusable: not a missing credential
        problems.append(f"{name}: credential store: {error}")
        return Resolution(errors=tuple(problems))
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
        missing_credentials=tuple(missing),
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
    spec: ClientFile,
    effective: ApprovalMode,
    env: Mapping[str, str],
    secret_store: SecretStore | None,
) -> tuple[Secrets, list[str]]:
    """Each credential from the store (the client's own scope, or Revansa's) and, failing that,
    from the environment variable the configuration names. Returns what is still missing (a model
    that Settings no longer offers is listed there too: the client is not ready either way)."""
    live = effective is not ApprovalMode.DRY_RUN  # a dry run never talks to WhatsApp
    client = client_scope(spec.client_id)

    def lookup(scope: str | None, key: str, variable: str | None) -> str | None:
        if secret_store is not None and scope is not None:
            stored = secret_store.get(scope, key)
            if stored:
                return stored
        return (env.get(variable, "").strip() or None) if variable else None

    whatsapp = spec.whatsapp
    wanted = [
        (OPENAI_API_KEY, "OpenAI API key", GLOBAL_SCOPE, spec.llm.api_key_env, True),
        (
            SHEETS_CREDENTIALS_FILE,
            "Google Sheets credentials file",
            None,
            spec.sheets.credentials_env,
            True,
        ),
        (WHATSAPP_ACCESS_TOKEN, "WhatsApp access token", client, whatsapp.token_env, live),
        (
            WHATSAPP_PHONE_NUMBER_ID,
            "WhatsApp phone number id",
            client,
            whatsapp.phone_number_id_env,
            live,
        ),
        (WHATSAPP_APP_SECRET, "Meta app secret", GLOBAL_SCOPE, whatsapp.app_secret_env, live),
    ]
    values: dict[str, str] = {}
    missing: list[str] = []
    for key, label, scope, variable, required in wanted:
        value = lookup(scope, key, variable)
        if value:
            values[key] = value
        elif required:
            missing.append(f"{label} is not configured")
    # Not a secret: the platform-wide API version, else the one this client's file names.
    stored_version = secret_store.get(GLOBAL_SCOPE, WHATSAPP_API_VERSION) if secret_store else None
    values[WHATSAPP_API_VERSION] = stored_version or whatsapp.api_version
    # Optional OpenAI account settings (not secrets): used only when the Admin has set them.
    if secret_store is not None:
        for optional in (OPENAI_ORGANIZATION, OPENAI_PROJECT, OPENAI_BASE_URL):
            stored = secret_store.get(GLOBAL_SCOPE, optional)
            if stored:
                values[optional] = stored
        # Not ready, never run: the model was deactivated (or removed) in Admin Settings. Nothing
        # is said when Settings holds no list, so deployments that never used it are unchanged.
        reason = inactive_model_reason(
            models_from(lambda key: secret_store.get(GLOBAL_SCOPE, key)),
            spec.llm.provider,
            spec.llm.model,
        )
        if reason is not None:
            missing.append(reason)
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
