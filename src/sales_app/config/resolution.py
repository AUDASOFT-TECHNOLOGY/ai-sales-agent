"""Combining operator wiring (client.yaml) with a stored settings document into one client spec.

`ClientFile` stays the single schema and the single place where values are validated. A stored
document holds only the client-editable keys (`SETTINGS_KEYS`); everything else in `client.yaml`
(providers, secret variable names, WhatsApp accounts, the Brain package, poll intervals) is
operator wiring that a document may never set. Nothing here touches the filesystem except reading
`client.yaml`, and nothing is cached: callers get fresh, immutable results.
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

from sales_app.config.schema import ClientFile
from sales_app.config_store.models import freeze_document, thaw_document

CONFIG_FILENAME = "client.yaml"
SETTINGS_SCHEMA_VERSION = 1

# What a stored settings document may contain. Anything else is operator-owned or unknown.
SETTINGS_TOP_KEYS = ("display_name", "timezone", "mode", "daily_action_limit", "opt_out_keywords")
SETTINGS_SECTION_KEYS: Mapping[str, tuple[str, ...]] = {
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


def extract_document(spec: ClientFile) -> dict[str, Any]:
    """The settings-owned part of a validated spec, with every value explicit."""
    return {
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
        "llm": {"model": spec.llm.model},
        "schedule": {"daily_at": spec.schedule.daily_at, "days": list(spec.schedule.days)},
        "brain": {"settings": deepcopy(dict(spec.brain.settings))},
    }


def check_document(document: object) -> list[str]:
    """Structure only: known schema version, no operator-owned or unknown keys, JSON-compatible,
    no secrets. Values are validated by `ClientFile` afterwards."""
    if not isinstance(document, Mapping):
        return ["document must be a JSON object"]
    errors: list[str] = []
    version = document.get("schema_version")
    if version != SETTINGS_SCHEMA_VERSION or isinstance(version, bool):
        errors.append(f"schema_version: must be {SETTINGS_SCHEMA_VERSION}, got {version!r}")
    for key, value in document.items():
        if key == "schema_version":
            continue
        if key in SETTINGS_TOP_KEYS:
            continue
        if key in SETTINGS_SECTION_KEYS:
            if not isinstance(value, Mapping):
                errors.append(f"{key}: must be an object")
                continue
            errors.extend(
                f"{key}.{inner}: is not a client setting (operator-owned or unknown)"
                for inner in value
                if inner not in SETTINGS_SECTION_KEYS[key]
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


def validate_settings_document(
    client_id: str, wiring: Mapping[str, Any], document: object
) -> SettingsCheck:
    """Everything that must hold before a document may become a version. Collects every error."""
    errors = check_document(document)
    if errors:
        return SettingsCheck(None, None, tuple(errors))
    assert isinstance(document, Mapping)
    try:
        spec = ClientFile.model_validate(merge_document(strip_settings(wiring), document))
    except ValidationError as error:
        return SettingsCheck(None, None, tuple(where(p) for p in error.errors()))
    if spec.client_id != client_id:
        return SettingsCheck(
            None, None, (f"client_id {spec.client_id!r} must equal the directory name",)
        )
    return SettingsCheck(spec, extract_document(spec), ())


def import_document(client_id: str, raw: Mapping[str, Any]) -> SettingsCheck:
    """The settings a full `client.yaml` already contains, as a document (to seed version 1)."""
    try:
        spec = ClientFile.model_validate(dict(raw))
    except ValidationError as error:
        return SettingsCheck(None, None, tuple(where(p) for p in error.errors()))
    if spec.client_id != client_id:
        return SettingsCheck(
            None, None, (f"client_id {spec.client_id!r} must equal the directory name",)
        )
    return SettingsCheck(spec, extract_document(spec), ())
