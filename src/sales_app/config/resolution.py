"""Combining operator wiring (client.yaml) with a stored settings document into one client spec.

`ClientFile` stays the single schema and the single place where values are validated. A stored
document holds only the client-editable keys; everything else in `client.yaml` (sheets/WhatsApp
providers, secret variable names, WhatsApp accounts, the Brain package, poll intervals) is operator
wiring that a document may never set. `llm.provider` is the one exception: it is client-editable
alongside `llm.model`, gated the same way (`LLMRegistry` and `Application.llm_providers` -- see
`sales_app.api.admin_clients`), because the two are chosen together. `llm.api_key_env` stays
operator wiring regardless of which provider is selected. Nothing here touches the filesystem
except reading `client.yaml`, and nothing is cached: callers get fresh, immutable results.

Document schema versions:

- **1**: client settings and opaque Brain settings. `llm` holds only `model`.
- **2** (current): adds the `inbound` policy and `brain.settings_version`, and the Brain's settings
  are validated by the Brain's own `SETTINGS_SPEC` when it has one. `llm` also holds `provider`.

A version-1 document keeps loading: it is upgraded in memory, and, because it predates the inbound
policy, it keeps today's behaviour (replies handled, unknown senders ignored). A version-2
document that says nothing gets the safe defaults instead (spontaneous conversations off).
"""

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from sales_app.config.brain_settings import prepare_brain_settings
from sales_app.config.schema import ClientFile
from sales_app.config_store.models import freeze_document, thaw_document

CONFIG_FILENAME = "client.yaml"
SETTINGS_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

# What a stored settings document may contain. Anything else is operator-owned or unknown.
SETTINGS_TOP_KEYS = (
    "display_name",
    "timezone",
    "mode",
    "daily_action_limit",
    "opt_out_keywords",
    "sources",
    "lanes",
)
_SECTIONS_V1: Mapping[str, tuple[str, ...]] = {
    "sheets": (
        "spreadsheet_id",
        "prospects_tab",
        "actions_tab",
        "messages_tab",
        "log_tab",
        "column_mapping",
    ),
    "llm": ("model",),
    "schedule": ("daily_at", "days"),
    "brain": ("settings",),
}
SETTINGS_SECTION_KEYS: Mapping[str, tuple[str, ...]] = {
    **_SECTIONS_V1,
    "llm": ("provider", "model"),
    "brain": ("settings", "settings_version"),
    "inbound": ("spontaneous_conversations", "unknown_contacts", "limits"),
    "source": ("provider", "settings"),
    "channel": ("provider", "settings"),
}
_SECTIONS_BY_VERSION = {1: _SECTIONS_V1, 2: SETTINGS_SECTION_KEYS}

# Behaviour before the inbound policy existed: known prospects' messages are handled.
LEGACY_INBOUND: Mapping[str, Any] = {"spontaneous_conversations": {"enabled": True}}

# Whole words that mark a key as holding a secret (`max_tokens` is fine, `token` is not).
_SECRET_WORDS = {"password", "passwd", "secret", "token", "apikey", "credential", "credentials"}
_SECRET_PAIRS = {("api", "key"), ("private", "key"), ("access", "key")}


@dataclass(frozen=True, slots=True)
class SettingsCheck:
    """Outcome of validating a settings document against one client's wiring."""

    spec: ClientFile | None
    document: dict[str, Any] | None  # normalized: defaults written out, so history is explicit
    errors: tuple[str, ...]


def read_wiring(directory: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The parsed client.yaml, or an error message prefixed with the directory name."""
    name = directory.name
    path = directory / CONFIG_FILENAME
    if not path.is_file():
        return None, f"{name}: {CONFIG_FILENAME} not found"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as error:
        return None, f"{name}: cannot read {CONFIG_FILENAME}: {error}"
    if not isinstance(raw, dict):
        return None, f"{name}: {CONFIG_FILENAME} must contain a mapping"
    return raw, None


def where(problem: Mapping[str, object]) -> str:
    location = ".".join(str(part) for part in problem["loc"])  # type: ignore[union-attr]
    return f"{location or 'file'}: {problem['msg']}"


def with_legacy_inbound(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A `client.yaml` from before the inbound policy keeps today's behaviour."""
    data = dict(raw)
    data.setdefault("inbound", deepcopy(dict(LEGACY_INBOUND)))
    return data


def strip_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The operator wiring alone: `client.yaml` without the keys a stored document owns."""
    wiring = deepcopy(dict(raw))
    for key in SETTINGS_TOP_KEYS:
        wiring.pop(key, None)
    for section, keys in SETTINGS_SECTION_KEYS.items():
        if isinstance(wiring.get(section), dict):
            for key in keys:
                wiring[section].pop(key, None)
    return wiring


def merge_document(wiring: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    """Wiring plus document, as the raw input `ClientFile` validates. Keys the document leaves out
    get `ClientFile`'s defaults, never a value from `client.yaml`."""
    merged = deepcopy(dict(wiring))
    for key in SETTINGS_TOP_KEYS:
        if key in document:
            merged[key] = thaw_document(document[key])
    for section, keys in SETTINGS_SECTION_KEYS.items():
        values = document.get(section)
        if not isinstance(values, Mapping):
            continue
        target = merged.setdefault(section, {})
        if isinstance(target, dict):
            for key in keys:
                if key in values:
                    target[key] = thaw_document(values[key])
    return merged


def upgrade_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """The current-schema form of a structurally valid document (see the module docstring)."""
    upgraded = thaw_document(document)
    if upgraded.get("schema_version") == 1:
        upgraded["schema_version"] = SETTINGS_SCHEMA_VERSION
        upgraded["inbound"] = deepcopy(dict(LEGACY_INBOUND))
        if isinstance(upgraded.get("brain"), dict):
            upgraded["brain"].setdefault("settings_version", 1)
    return upgraded


def extract_document(spec: ClientFile) -> dict[str, Any]:
    """The settings-owned part of a validated spec, in the current schema, every value explicit."""
    document: dict[str, Any] = {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "display_name": spec.display_name,
        "timezone": spec.timezone,
        "mode": spec.mode.value,
        "daily_action_limit": spec.daily_action_limit,
        "opt_out_keywords": list(spec.opt_out_keywords),
        "sheets": {
            "spreadsheet_id": spec.sheets.spreadsheet_id,
            "prospects_tab": spec.sheets.prospects_tab,
            "actions_tab": spec.sheets.actions_tab,
            "messages_tab": spec.sheets.messages_tab,
            "log_tab": spec.sheets.log_tab,
            "column_mapping": dict(spec.sheets.column_mapping),
        },
        "llm": {"provider": spec.llm.provider, "model": spec.llm.model},
        "schedule": {"daily_at": spec.schedule.daily_at, "days": list(spec.schedule.days)},
        "inbound": spec.inbound.model_dump(mode="json"),
        "brain": {
            "settings_version": spec.brain.settings_version,
            "settings": deepcopy(dict(spec.brain.settings)),
        },
    }
    if spec.source_apart_from_store:
        # Written only when a source other than the store's provider is chosen, so every document
        # of a client that never chose one stays exactly what it was; leaving it out means "the
        # store's provider".
        document["source"] = {"provider": spec.source_provider}
        if spec.source_settings:
            document["source"]["settings"] = deepcopy(spec.source_settings)
    if spec.lanes is not None and spec.sources is not None:
        # Written only for a client that runs lanes: every other document stays what it was.
        document["sources"] = [s.model_dump(mode="json") for s in spec.sources]
        document["lanes"] = [lane.model_dump(mode="json") for lane in spec.lanes]
    if spec.channel is not None:
        # Written only when the client chose its own channel, for the same reason as `source`.
        document["channel"] = {"provider": spec.channel.provider}
        if spec.channel.settings:
            document["channel"]["settings"] = deepcopy(spec.channel.settings)
    return document


def check_document(document: object) -> list[str]:
    """Structure only: known schema version, no operator-owned or unknown keys, JSON-compatible,
    no secrets. Values are validated by `ClientFile` afterwards."""
    if not isinstance(document, Mapping):
        return ["document must be a JSON object"]
    errors: list[str] = []
    version = document.get("schema_version")
    if isinstance(version, bool) or version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"schema_version: must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}, got {version!r}"
        )
    sections = _SECTIONS_BY_VERSION.get(version, SETTINGS_SECTION_KEYS)  # type: ignore[arg-type]
    for key, value in document.items():
        if key == "schema_version" or key in SETTINGS_TOP_KEYS:
            continue
        if key in sections:
            if not isinstance(value, Mapping):
                errors.append(f"{key}: must be an object")
                continue
            errors.extend(
                f"{key}.{inner}: is not a client setting (operator-owned or unknown)"
                for inner in value
                if inner not in sections[key]
            )
            continue
        errors.append(f"{key}: is not a client setting (operator-owned or unknown)")
    try:
        freeze_document(dict(document))
    except (TypeError, ValueError) as error:
        errors.append(f"document: {error}")
    else:
        errors.extend(
            f"{path}: looks like a secret; keep secrets in environment variables and reference "
            "them by name"
            for path in _secret_paths(document)
        )
    return errors


def _secret_paths(value: object, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if _looks_secret(str(key)):
                found.append(path)
            found.extend(_secret_paths(item, path))
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            found.extend(_secret_paths(item, f"{prefix}[{index}]"))
    return found


def _looks_secret(key: str) -> bool:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower()
    words = [w for w in re.split(r"[^a-z0-9]+", spaced) if w]
    return bool(_SECRET_WORDS & set(words)) or any(
        pair in _SECRET_PAIRS for pair in pairwise(words)
    )


def _finish(client_id: str, candidate: ClientFile) -> SettingsCheck:
    """Last steps shared by publishing and importing: identity, the Brain's own rules, extraction."""
    if candidate.client_id != client_id:
        return SettingsCheck(
            None, None, (f"client_id {candidate.client_id!r} must equal the directory name",)
        )
    prepared, problems = prepare_brain_settings(candidate, require_module=True)
    if problems:
        return SettingsCheck(None, None, problems)
    from sales_app.config.lanes import lane_problems  # here: it needs the provider registry

    lanes = lane_problems(prepared)
    if lanes:
        return SettingsCheck(None, None, tuple(lanes))
    return SettingsCheck(prepared, extract_document(prepared), ())


def validate_settings_document(
    client_id: str, wiring: Mapping[str, Any], document: object
) -> SettingsCheck:
    """Everything that must hold before a document may become a version. Collects every error."""
    errors = check_document(document)
    if errors:
        return SettingsCheck(None, None, tuple(errors))
    assert isinstance(document, Mapping)
    try:
        candidate = ClientFile.model_validate(
            merge_document(strip_settings(wiring), upgrade_document(document))
        )
    except ValidationError as error:
        return SettingsCheck(None, None, tuple(where(p) for p in error.errors()))
    return _finish(client_id, candidate)


def import_document(client_id: str, raw: Mapping[str, Any]) -> SettingsCheck:
    """The settings a full `client.yaml` already contains, as a document (to seed version 1)."""
    try:
        candidate = ClientFile.model_validate(with_legacy_inbound(raw))
    except ValidationError as error:
        return SettingsCheck(None, None, tuple(where(p) for p in error.errors()))
    return _finish(client_id, candidate)
