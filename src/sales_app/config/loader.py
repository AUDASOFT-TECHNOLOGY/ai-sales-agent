"""Discovering, validating and resolving client configuration.

Nothing here raises for a bad client: every problem is collected, so one run reports them all.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from sales_app.config.schema import ClientFile
from sales_app.config.secrets import Secrets
from sales_core.approval import ApprovalMode

CONFIG_FILENAME = "client.yaml"

# Safest first: a client's effective mode is the safer of its own and the deployment ceiling.
MODE_ORDER = (ApprovalMode.DRY_RUN, ApprovalMode.APPROVAL, ApprovalMode.AUTONOMOUS)


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """A validated client: its file, the mode it will really run in, and its secrets."""

    spec: ClientFile
    directory: Path
    effective_mode: ApprovalMode
    secrets: Secrets

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


def load_client_configs(
    clients_dir: Path, *, env: Mapping[str, str], mode_ceiling: ApprovalMode
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
        config, is_disabled, problems = _load_one(directory, env, mode_ceiling)
        errors.extend(problems)
        if is_disabled:
            disabled.append(directory.name)
        elif config is not None:
            clients.append(config)
    errors.extend(_cross_client_errors(clients))
    return LoadedConfig(tuple(clients), tuple(disabled), tuple(errors))


def _load_one(
    directory: Path, env: Mapping[str, str], mode_ceiling: ApprovalMode
) -> tuple[ClientConfig | None, bool, list[str]]:
    name = directory.name
    path = directory / CONFIG_FILENAME
    if not path.is_file():
        return None, False, [f"{name}: {CONFIG_FILENAME} not found"]
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as error:
        return None, False, [f"{name}: cannot read {CONFIG_FILENAME}: {error}"]
    if not isinstance(raw, dict):
        return None, False, [f"{name}: {CONFIG_FILENAME} must contain a mapping"]

    if raw.get("enabled", True) is False:
        return None, True, []  # deliberately excluded: not validated, not built

    try:
        spec = ClientFile.model_validate(raw)
    except ValidationError as error:
        return None, False, [f"{name}: {_where(problem)}" for problem in error.errors()]

    problems: list[str] = []
    if spec.client_id != name:
        problems.append(f"{name}: client_id {spec.client_id!r} must equal the directory name")

    effective = clamp_mode(spec.mode, mode_ceiling)
    secrets, missing = _resolve_secrets(spec, effective, env)
    problems.extend(f"{name}: {message}" for message in missing)
    if problems:
        return None, False, problems
    return ClientConfig(spec, directory, effective, secrets), False, []


def _where(problem: Mapping[str, object]) -> str:
    location = ".".join(str(part) for part in problem["loc"])  # type: ignore[union-attr]
    return f"{location or 'file'}: {problem['msg']}"


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
