"""Discovering, validating and resolving client configuration.

Nothing here raises for a bad client: every problem is collected, so one run reports them all.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from sales_app.brain_registry import BrainRegistry
from sales_app.client_records import ClientRecordStore
from sales_app.config.brain_settings import prepare_brain_settings
from sales_app.config.channel_access import GOVERNED, effective_channels
from sales_app.config.client_wiring import known_client_ids, wiring_for
from sales_app.config.email_activation import blocks_sending, parse_record
from sales_app.config.email_settings import (
    GMAIL_MODE,
    PLATFORM_MODE,
    email_mode_of,
    mailbox_key_of,
    parse_email_settings,
    platform_mailbox_key,
)
from sales_app.config.llm_registry import EMPTY_LLM_REGISTRY, LLMRegistry
from sales_app.config.offered_models import inactive_model_reason, models_from
from sales_app.config.resolution import (
    CONFIG_FILENAME,
    check_document,
    merge_document,
    strip_settings,
    upgrade_document,
    where,
    with_legacy_inbound,
)
from sales_app.config.schema import PLACEHOLDERS, ClientFile
from sales_app.config.secrets import (
    CHANNELS_ACCESS,
    CHANNELS_ALLOWED,
    EMAIL_ACTIVATION,
    EMAIL_PASSWORD,
    EMAIL_PLATFORM_MAILBOX,
    EMAIL_PLATFORM_PROFILE_KEYS,
    GMAIL_ACCOUNT_ADDRESS,
    GMAIL_OAUTH_CLIENT_ID,
    GMAIL_OAUTH_CLIENT_SECRET,
    GMAIL_OAUTH_STATE_KEY,
    GMAIL_REAUTH_REQUIRED,
    GMAIL_REFRESH_TOKEN,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_ORGANIZATION,
    OPENAI_PROJECT,
    SHEETS_CREDENTIALS_FILE,
    SHEETS_OAUTH_GRANT,
    SHEETS_REAUTH_REQUIRED,
    SHEETS_REFRESH_TOKEN,
    SOURCE_CREDENTIALS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WEBHOOK_SECRET,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    Secrets,
)
from sales_app.config.telegram_settings import TelegramChannelSettings, bot_id_of
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

    @property
    def telegram_bot_id(self) -> str | None:
        """The bot this client talks through, when its channel is Telegram and it has one."""
        if "telegram" not in self.spec.channel_providers:
            return None
        return bot_id_of(self.secrets.get_optional(TELEGRAM_BOT_TOKEN))

    @property
    def mailbox_key(self) -> str | None:
        """The mailbox this client reads, when its channel is e-mail and it has one set up."""
        if "email" not in self.spec.channel_providers:
            return None
        settings = self.spec.channel_settings_for("email")
        if email_mode_of(settings) == PLATFORM_MODE:
            # The assigned address is a credential of the client's own, not a setting: nothing a
            # client writes can name a mailbox that is not its own.
            address = self.secrets.get_optional(EMAIL_PLATFORM_MAILBOX)
            return platform_mailbox_key(address) if address else None
        if email_mode_of(settings) == GMAIL_MODE:
            # The connected Gmail address (not a secret, unique across clients): set by the OAuth
            # callback, never by a setting.
            address = self.secrets.get_optional(GMAIL_ACCOUNT_ADDRESS)
            return f"gmail|{address.lower()}" if address else None
        return mailbox_key_of(settings)


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
    clients_dir: Path | None,
    *,
    env: Mapping[str, str],
    mode_ceiling: ApprovalMode,
    store: ConfigStore | None = None,
    llm_registry: LLMRegistry = EMPTY_LLM_REGISTRY,
    secret_store: SecretStore | None = None,
    records: ClientRecordStore | None = None,
) -> LoadedConfig:
    from_records = records is not None and bool(known_client_ids(clients_dir, records))
    if clients_dir is not None and not clients_dir.is_dir() and not from_records:
        return LoadedConfig((), (), (f"clients directory not found: {clients_dir}",))

    clients: list[ClientConfig] = []
    disabled: list[str] = []
    errors: list[str] = []
    # The historical directories (as before), plus the clients that exist only as a record.
    names = {
        p.name
        for p in (clients_dir.iterdir() if clients_dir is not None and clients_dir.is_dir() else ())
        if p.is_dir() and not p.name.startswith(("_", "."))
    } | set(known_client_ids(clients_dir, records))
    for name in sorted(names):
        directory = (clients_dir or Path()) / name
        resolution = resolve_client(
            directory,
            files=clients_dir is not None,
            records=records,
            env=env,
            mode_ceiling=mode_ceiling,
            store=store,
            llm_registry=llm_registry,
            secret_store=secret_store,
        )
        errors.extend(resolution.errors)
        if resolution.disabled:
            disabled.append(name)
        elif resolution.config is not None:
            clients.append(resolution.config)
    errors.extend(_cross_client_errors(clients))
    return LoadedConfig(tuple(clients), tuple(disabled), tuple(errors))


def brain_unassigned(brain: object) -> bool:
    """Whether the Admin has not assigned this client a Brain yet (its factory is a placeholder)."""
    if not isinstance(brain, Mapping) or brain.get("agent"):
        return not isinstance(brain, Mapping)
    factory = brain.get("factory")
    return isinstance(factory, str) and factory.strip().lower() in PLACEHOLDERS


def awaiting_configuration(candidate: Mapping[str, object]) -> bool:
    """Whether a client still holds a placeholder where its model, its Brain or its Sheet goes.

    This is the state of a new account: it is judged on the settings in force (the client.yaml with
    the published version laid over), so a Sheet the client has entered since counts."""

    def value(section: str, key: str) -> object:
        body = candidate.get(section)
        return body.get(key) if isinstance(body, Mapping) else None

    def placeholder(item: object) -> bool:
        return isinstance(item, str) and item.strip().lower() in PLACEHOLDERS

    return (
        brain_unassigned(candidate.get("brain"))
        or placeholder(value("sheets", "spreadsheet_id"))
        or placeholder(value("llm", "model"))
    )


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
    return resolve_client(
        directory,
        records=None,
        env=env,
        mode_ceiling=mode_ceiling,
        store=store,
        llm_registry=llm_registry,
        secret_store=secret_store,
    )


def resolve_client(
    directory: Path,
    *,
    records: ClientRecordStore | None,
    files: bool = True,
    env: Mapping[str, str],
    mode_ceiling: ApprovalMode,
    store: ConfigStore | None = None,
    llm_registry: LLMRegistry = EMPTY_LLM_REGISTRY,
    secret_store: SecretStore | None = None,
) -> Resolution:
    """`resolve_client_config` for a client whose wiring comes from its database record first and
    its `client.yaml` as the fallback (see `sales_app.config.client_wiring`). With no record, it is
    exactly `resolve_client_config`. `directory` need not exist when the client has a record."""
    name = directory.name
    raw, problem = wiring_for(directory.parent if files else None, name, records)
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

    if awaiting_configuration(candidate):
        # An account the Admin enabled before its model, Brain or Sheet exist: it can sign in and
        # configure itself, but has no agent to build yet. Not an error, or one such account would
        # stop the whole application.
        return Resolution(disabled=True)

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

    return complete_client_config(
        spec,
        name=name,
        directory=directory,
        config_version=version.version if version is not None else None,
        env=env,
        mode_ceiling=mode_ceiling,
        llm_registry=llm_registry,
        secret_store=secret_store,
    )


def complete_client_config(
    spec: ClientFile,
    *,
    name: str,
    directory: Path,
    config_version: int | None,
    env: Mapping[str, str],
    mode_ceiling: ApprovalMode,
    llm_registry: LLMRegistry = EMPTY_LLM_REGISTRY,
    secret_store: SecretStore | None = None,
    extra_missing: tuple[str, ...] = (),
    brains: BrainRegistry | None = None,
) -> Resolution:
    """The last steps of resolving a client, once its settings have become a `ClientFile`: the
    Brain's own rules, the effective mode, the credentials, the model allow-list.

    Shared by `resolve_client_config` and by anything else that arrives at a `ClientFile` another
    way, so a client is judged by the same rules however its configuration was read. `extra_missing`
    adds reasons the client is not ready, after the credentials'."""
    version_number = config_version
    problems = []
    if spec.client_id != name:
        problems.append(f"{name}: client_id {spec.client_id!r} must equal the directory name")

    # A Brain that declares settings rules gets its settings validated and normalised here. An
    # unimportable Brain module is left for the runtime build to report, with more context.
    unwritten = not spec.brain.settings  # the client has not written its commercial settings yet
    spec, brain_problems = prepare_brain_settings(spec, require_module=False, brains=brains)
    if brain_problems and unwritten and spec.client_id == name:
        return Resolution(disabled=True)  # awaiting the client's data, like a missing model
    problems.extend(f"{name}: {message}" for message in brain_problems)
    if brain_problems and version_number is not None:
        problems.append(
            f"{name}: settings in force come from configuration version {version_number}"
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
        config_version=version_number,
        config_source="store" if version_number is not None else "file",
        missing_credentials=(*missing, *extra_missing),
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


def _gmail_missing(
    secret_store: SecretStore | None, client: str, values: dict[str, str], missing: list[str]
) -> None:
    """A client in Gmail mode is ready to run only with the platform's Google application set and
    its own valid grant. Tokens are NOT resolved into the client's secrets (the token source reads
    the store when it needs one); only the connected address is, for the mailbox key."""
    if secret_store is None:
        missing.append("Email Gmail is not connected yet")
        return
    if not all(
        secret_store.entry(GLOBAL_SCOPE, key) is not None
        for key in (GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET, GMAIL_OAUTH_STATE_KEY)
    ):
        missing.append("Email Google connection is not configured by the platform yet")
    if secret_store.entry(client, GMAIL_REFRESH_TOKEN) is None:
        missing.append("Email Gmail is not connected yet")
        return
    if secret_store.get(client, GMAIL_REAUTH_REQUIRED) == "1":
        missing.append("Email Gmail must be connected again")
    address = secret_store.get(client, GMAIL_ACCOUNT_ADDRESS)
    if address:
        values[GMAIL_ACCOUNT_ADDRESS] = address


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
    channels = spec.channel_providers  # one for a client with no lanes, else each lane's
    # A client on e-mail asks for its mailbox's credentials and none of Meta's; every other channel
    # keeps asking for what it always did. A client with lanes asks for what each of its channels
    # needs, and nothing for a channel none of its lanes uses.
    whatsapp_wanted = (
        [
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
        if any(c not in ("email", "telegram") for c in channels)
        else []
    )
    email_gmail = "email" in channels and (
        email_mode_of(spec.channel_settings_for("email")) == GMAIL_MODE
    )
    # A Gmail mailbox has no password at all: its grant is the client's own OAuth consent.
    email_wanted = (
        [(EMAIL_PASSWORD, "Email password", client, None, live)]
        if "email" in channels and not email_gmail
        else []
    )
    # A mailbox Revansa provides: its address is the client's own, its servers are the platform's.
    # The servers are only collected here; whether they are all set is said once, below.
    email_platform = "email" in channels and (
        email_mode_of(spec.channel_settings_for("email")) == PLATFORM_MODE
    )
    if email_platform:
        email_wanted += [
            (EMAIL_PLATFORM_MAILBOX, "Email mailbox", client, None, False),
            *((key, key, GLOBAL_SCOPE, None, False) for key in EMAIL_PLATFORM_PROFILE_KEYS),
        ]
    telegram_wanted = (
        [
            (TELEGRAM_BOT_TOKEN, "Telegram bot token", client, None, live),
            (TELEGRAM_WEBHOOK_SECRET, "Telegram webhook secret", client, None, live),
        ]
        if "telegram" in channels
        else []
    )
    # A client whose prospects live elsewhere does not use the spreadsheet, so it needs no Google
    # access; it needs the credential its own source declares, from the credential store only.
    if spec.sources is not None:  # lanes: every source says what it needs, once per provider
        providers = tuple(dict.fromkeys(source.provider for source in spec.sources))
        sheets_used = spec.sheets.provider in providers
        source_credentials = [SOURCE_CREDENTIALS[p] for p in providers if p in SOURCE_CREDENTIALS]
    else:
        sheets_used = not spec.source_apart_from_store
        source_credentials = (
            [SOURCE_CREDENTIALS[spec.source_provider]]
            if not sheets_used and spec.source_provider in SOURCE_CREDENTIALS
            else []
        )
    wanted = [
        (OPENAI_API_KEY, "OpenAI API key", GLOBAL_SCOPE, spec.llm.api_key_env, True),
        (
            SHEETS_CREDENTIALS_FILE,
            "Google Sheets credentials file",
            None,
            spec.sheets.credentials_env,
            sheets_used,
        ),
        *((key, label, client, None, True) for key, label in source_credentials),
        *whatsapp_wanted,
        *email_wanted,
        *telegram_wanted,
    ]
    values: dict[str, str] = {}
    missing: list[str] = []
    # The client's own Google authorisation (OAuth) is how a client connects Sheets; the file of a
    # service account (environment) is the older way and still works where it is set.
    sheets_grant = _sheets_grant_state(secret_store, client) if sheets_used else None
    if sheets_grant == "usable":
        values[SHEETS_OAUTH_GRANT] = "1"
    for key, label, scope, variable, required in wanted:
        if key == SHEETS_CREDENTIALS_FILE and sheets_grant is not None:
            value = lookup(scope, key, variable)
            if value:
                values[key] = value
            elif sheets_grant == "reauth":
                missing.append("Google Sheets must be authorised again")
            continue
        value = lookup(scope, key, variable)
        if value:
            values[key] = value
        elif required:
            missing.append(f"{label} is not configured")
    if "email" in channels and live:
        try:
            parse_email_settings(spec.channel_settings_for("email"))
        except ValueError:
            missing.append("Email settings are incomplete")
    if email_gmail and live:
        _gmail_missing(secret_store, client, values, missing)
    if email_platform and live:
        if EMAIL_PLATFORM_MAILBOX not in values:
            missing.append("Email mailbox has not been assigned to this account yet")
        if any(key not in values for key in EMAIL_PLATFORM_PROFILE_KEYS):
            missing.append("The platform mail servers are not configured")
        # The client confirms its Sender Name before the first message leaves under it: saving the
        # name is the confirmation, so none stored means none confirmed.
        sender = spec.channel_settings_for("email").get("from_name")
        if not (isinstance(sender, str) and sender.strip()):
            missing.append("Email sender name has not been confirmed yet")
        # A client that asked for its mailbox to be activated sends nothing until the Admin has
        # activated it. One that never asked is left as it was.
        if secret_store is not None and blocks_sending(
            parse_record(secret_store.get(client, EMAIL_ACTIVATION))
        ):
            missing.append("Email activation has not been approved yet")
    if "telegram" in channels and live:
        try:
            TelegramChannelSettings.model_validate(spec.channel_settings_for("telegram"))
        except ValueError:
            missing.append("Telegram settings are incomplete")
    granted = effective_channels(
        secret_store.get(GLOBAL_SCOPE, CHANNELS_ALLOWED) if secret_store is not None else None,
        secret_store.get(client, CHANNELS_ACCESS) if secret_store is not None else None,
    )
    for channel in channels:
        if channel in GOVERNED and channel not in granted:
            # Judged on every resolution, so a channel the Admin forbids afterwards stops a client
            # that had already set it up. Even a dry run is refused: the channel is not this
            # client's.
            missing.append(f"The {channel} channel is not authorized for this account")
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


def _sheets_grant_state(
    secret_store: SecretStore | None, client: str
) -> Literal["usable", "reauth"] | None:
    """`None`: the client has not connected Google Sheets with its own authorisation."""
    if secret_store is None or secret_store.entry(client, SHEETS_REFRESH_TOKEN) is None:
        return None
    return "reauth" if secret_store.get(client, SHEETS_REAUTH_REQUIRED) == "1" else "usable"


def _spreadsheets(client: ClientConfig) -> list[str]:
    """The spreadsheets a client reads: its store's own, unless its prospects live elsewhere, or
    each Google Sheets source of a client with lanes (its `sheets` section is then unused)."""
    spec = client.spec
    if spec.sources is not None:
        return [
            str(source.settings["spreadsheet_id"])
            for source in spec.sources
            if source.provider == spec.sheets.provider and source.settings.get("spreadsheet_id")
        ]
    return [] if spec.source_apart_from_store else [spec.sheets.spreadsheet_id]


def _cross_client_errors(clients: list[ClientConfig]) -> list[str]:
    """Two clients sharing an account is almost always a copy-paste mistake."""
    checks: list[tuple[str, Callable[[ClientConfig], Iterable[str | None]]]] = [
        ("WhatsApp phone_number_id", lambda c: [c.phone_number_id]),
        ("spreadsheet_id", _spreadsheets),
        ("email mailbox", lambda c: [c.mailbox_key]),
        ("Telegram bot", lambda c: [c.telegram_bot_id]),
        ("whatsapp.token_env", lambda c: [c.spec.whatsapp.token_env]),
        ("whatsapp.phone_number_id_env", lambda c: [c.spec.whatsapp.phone_number_id_env]),
    ]
    errors: list[str] = []
    for label, getter in checks:
        owners: dict[str, list[str]] = defaultdict(list)
        for client in clients:
            for value in dict.fromkeys(getter(client)):  # once per client, however often it says it
                if value:
                    owners[value].append(client.client_id)
        for value, ids in owners.items():
            if len(ids) > 1:
                errors.append(f"{label} {value!r} is used by several clients: {', '.join(ids)}")
    return errors
