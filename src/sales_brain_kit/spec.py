"""The consultative sales family's `BrainSettingsSpec`: validation, normalising and the JSON Schema.

A Brain package exposes it as its `SETTINGS_SPEC` module attribute. The application validates
settings with it before they are stored and when they are loaded; a future admin interface renders
forms from `json_schema()`.
"""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from sales_brain_kit.settings import ConsultativeSalesSettings

FAMILY = "consultative_sales"
SETTINGS_VERSION = 1
MAX_SETTINGS_BYTES = 1_000_000  # a hard ceiling on one client's Brain settings


class SettingsInvalidError(ValueError):
    """The settings do not satisfy the schema. `problems` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(problems))


class ConsultativeSalesSpec:
    family = FAMILY
    schema_version = SETTINGS_VERSION

    def json_schema(self) -> Mapping[str, object]:
        schema = ConsultativeSalesSettings.model_json_schema()
        return {**schema, "x-family": FAMILY, "x-settings-version": SETTINGS_VERSION}

    def validate(
        self, settings: Mapping[str, object], version: int = SETTINGS_VERSION
    ) -> list[str]:
        try:
            _parse(settings, version)
        except SettingsInvalidError as error:
            return list(error.problems)
        return []

    def prepare(
        self, settings: Mapping[str, object], version: int = SETTINGS_VERSION
    ) -> dict[str, Any]:
        parsed = _parse(settings, version)
        return parsed.model_dump(mode="json", exclude_none=True)


def parse_settings(settings: Mapping[str, object]) -> ConsultativeSalesSettings:
    """The typed settings a Brain of this family works with. Raises `SettingsInvalidError`."""
    return _parse(settings, SETTINGS_VERSION)


def _parse(settings: Mapping[str, object], version: int) -> ConsultativeSalesSettings:
    if version != SETTINGS_VERSION:
        raise SettingsInvalidError(
            [
                f"settings_version {version} is not supported (this Brain understands {SETTINGS_VERSION})"
            ]
        )
    plain = _plain(settings)
    try:
        size = len(json.dumps(plain, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise SettingsInvalidError([f"settings are not JSON-compatible: {error}"]) from error
    if size > MAX_SETTINGS_BYTES:
        raise SettingsInvalidError(
            [f"settings are {size} bytes; the limit is {MAX_SETTINGS_BYTES}"]
        )
    try:
        return ConsultativeSalesSettings.model_validate(plain)
    except ValidationError as error:
        raise SettingsInvalidError(_problems(error)) from None


def _plain(value: Any) -> Any:
    """Plain dicts and lists (settings may arrive as frozen mappings)."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def _problems(error: ValidationError) -> list[str]:
    problems: list[str] = []
    for item in error.errors():
        path = ".".join(str(part) for part in item["loc"])
        message = item["msg"].removeprefix("Value error, ")
        if path:
            problems.append(f"{path}: {message}")
        else:  # a cross-section problem: each line already starts with its own path
            problems.extend(message.splitlines())
    return problems
