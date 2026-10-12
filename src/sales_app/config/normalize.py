"""Turning every configuration that can be read today into the canonical (v3) shape.

A pure function, and inert: nothing in loading, the runtime or the APIs calls it yet. It reads no
file, no environment variable and no secret; the same input always gives the same output.

What it accepts, alone or together (`normalize(raw, document)`):

- a legacy `client.yaml` mapping (`sheets`, `llm`, `whatsapp`, `brain.factory`...), complete;
- a legacy wiring-only `client.yaml` plus a stored settings document of schema version 1 or 2
  (merged exactly as `resolve_client_config` merges them today);
- a canonical v3 mapping (returned as it is: normalising is idempotent);
- canonical or legacy wiring plus a v3 settings document.

What it returns (`Normalized`):

- `spec`: plain JSON data that `ClientSpec` validates. It holds no secret and no environment
  variable name;
- `env_fallbacks`: the legacy references to environment variables (`*_env`), kept apart from the
  business configuration, keyed by the logical credential key the runtime resolves today. These
  are wiring, not configuration: a later increment decides how they are used. A value is never read.

Nothing is dropped silently: an unknown key, an unknown `column_mapping` tab prefix or a missing
section is a `NormalizationError` that lists every problem. The `llm` section keeps the provider
and the upstream model id under `legacy`; turning them into a catalogue key needs the model
catalogue, which a pure function does not have, so `llm.model` is left unset.

Which Brain a factory path stands for is knowledge the caller passes in (`known_brains`); without
it the path is kept as `legacy_factory`. This module reads no installed package either.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from sales_app.config.resolution import (
    _secret_paths,
    check_document,
    merge_document,
    strip_settings,
    upgrade_document,
    where,
    with_legacy_inbound,
)
from sales_app.config.schema import BrainSection as LegacyBrainSection
from sales_app.config.schema import (
    ClientFile,
    InboundSection,
    LLMSection,
    ScheduleSection,
    SheetsSection,
    WhatsAppSection,
)
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    SHEETS_CREDENTIALS_FILE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
from sales_app.config_store.models import freeze_document, thaw_document

SCHEMA_VERSION = 3

# The tabs a legacy `column_mapping` key may name (`<tab>.<field>`), and which role keeps each. The
# source of truth is the Sheets layout; a test keeps this list equal to it.
SOURCE_TABS = ("prospects",)
STORE_TABS = ("actions", "messages", "log", "facts")
KNOWN_TABS = (*SOURCE_TABS, *STORE_TABS)

# What a stored v3 settings document may own (the rest is wiring, which a document never sets).
DOCUMENT_V3_KEYS = (
    "display_name",
    "timezone",
    "mode",
    "daily_action_limit",
    "opt_out_keywords",
    "source",
    "store",
    "channels",
    "llm",
    "schedule",
    "inbound",
)
DOCUMENT_V3_BRAIN_KEYS = ("settings", "settings_version")
SUPPORTED_DOCUMENT_VERSIONS = (1, 2, 3)

_LEGACY_SECTIONS: Mapping[str, type] = {
    "brain": LegacyBrainSection,
    "sheets": SheetsSection,
    "llm": LLMSection,
    "whatsapp": WhatsAppSection,
    "schedule": ScheduleSection,
    "inbound": InboundSection,
}
_POLLING_KEYS = ("prepare_every_minutes", "dispatch_every_minutes", "recover_every_minutes")
_WIRING_TOP_KEYS = ("client_id", "enabled", "max_attempts")


class NormalizationError(ValueError):
    """The configuration cannot be converted faithfully. `problems` lists every reason."""

    def __init__(self, problems: list[str] | tuple[str, ...]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True, slots=True)
class Normalized:
    spec: dict[str, Any]
    env_fallbacks: Mapping[str, str] = field(default_factory=dict)
    origin: str = "legacy"  # "legacy" or "canonical": what the input looked like
    document_version: int | None = None  # the stored document's schema version, if one was given


def normalize(
    raw: Mapping[str, Any],
    document: Mapping[str, Any] | None = None,
    *,
    known_brains: Mapping[str, str] | None = None,
) -> Normalized:
    """The canonical form of `raw` (a `client.yaml` mapping) with an optional stored `document`.

    `known_brains` maps a factory path (`package.module:callable`) to a Brain catalogue key.
    Raises `NormalizationError` listing every problem found.
    """
    if not isinstance(raw, Mapping):
        raise NormalizationError(["configuration must be a mapping"])
    brains = dict(known_brains or {})
    version = _document_version(document)

    if _is_canonical(raw):
        spec = _plain(raw)
        if document is None:
            return Normalized(spec, {}, "canonical", None)
        if version != SCHEMA_VERSION:
            raise NormalizationError(
                [
                    f"a canonical configuration can only be combined with a v3 document, not v{version}"
                ]
            )
        return Normalized(_overlay_v3(spec, document), {}, "canonical", version)

    if version == SCHEMA_VERSION:
        assert document is not None
        wiring, env = _legacy_wiring(raw, brains)
        return Normalized(_overlay_v3(wiring, document), env, "legacy", version)

    if document is None:
        candidate = with_legacy_inbound(raw)  # a client.yaml predates the inbound policy
    else:
        problems = check_document(document)
        if problems:
            raise NormalizationError(problems)
        candidate = merge_document(strip_settings(raw), upgrade_document(document))
    return _from_legacy(candidate, brains, version)


# ---- dispatch helpers --------------------------------------------------------------------------


def _is_canonical(raw: Mapping[str, Any]) -> bool:
    version = raw.get("schema_version")
    return not isinstance(version, bool) and version == SCHEMA_VERSION


def _document_version(document: Mapping[str, Any] | None) -> int | None:
    if document is None:
        return None
    if not isinstance(document, Mapping):
        raise NormalizationError(["document must be a JSON object"])
    version = document.get("schema_version")
    if isinstance(version, bool) or version not in SUPPORTED_DOCUMENT_VERSIONS:
        raise NormalizationError(
            [f"schema_version: must be one of {list(SUPPORTED_DOCUMENT_VERSIONS)}, got {version!r}"]
        )
    return version


def _plain(value: Mapping[str, Any]) -> dict[str, Any]:
    """A deep, plain (mutable) JSON copy: the caller owns what it gets."""
    try:
        return thaw_document(freeze_document(dict(value)))
    except (TypeError, ValueError) as error:
        raise NormalizationError([f"configuration: {error}"]) from error


# ---- v3 documents ------------------------------------------------------------------------------


def _overlay_v3(wiring: dict[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    """Wiring plus a v3 document: each key the document owns replaces the wiring's."""
    problems: list[str] = []
    for key, value in document.items():
        if key == "schema_version" or key in DOCUMENT_V3_KEYS:
            continue
        if key == "brain":
            if not isinstance(value, Mapping):
                problems.append("brain: must be an object")
                continue
            problems.extend(
                f"brain.{inner}: is not a client setting (operator-owned or unknown)"
                for inner in value
                if inner not in DOCUMENT_V3_BRAIN_KEYS
            )
            continue
        problems.append(f"{key}: is not a client setting (operator-owned or unknown)")
    try:
        plain = _plain(document)
    except NormalizationError as error:
        problems.extend(error.problems)
        plain = {}
    problems.extend(
        f"{path}: looks like a secret; keep secrets in the credential store"
        for path in _secret_paths(plain)
    )
    if problems:
        raise NormalizationError(problems)

    result = deepcopy(wiring)
    result["schema_version"] = SCHEMA_VERSION
    for key in DOCUMENT_V3_KEYS:
        if key in plain:
            result[key] = plain[key]
    if isinstance(plain.get("brain"), dict):
        brain = result.setdefault("brain", {})
        for key in DOCUMENT_V3_BRAIN_KEYS:
            if key in plain["brain"]:
                brain[key] = plain["brain"][key]
    return result


def _legacy_wiring(
    raw: Mapping[str, Any], brains: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """The wiring-owned part of a legacy `client.yaml` in canonical form, plus its env references.

    Everything a stored document owns is ignored here (as it is when a document is merged today).
    """
    problems = _unknown_wiring_keys(raw)
    brain = raw.get("brain")
    wiring: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for key in _WIRING_TOP_KEYS:
        if key in raw:
            wiring[key] = raw[key]
    if isinstance(brain, Mapping) and brain.get("factory") is not None:
        wiring["brain"] = _brain_binding(brain["factory"], brains)
    schedule = raw.get("schedule")
    if isinstance(schedule, Mapping):
        polling = {key: schedule[key] for key in _POLLING_KEYS if key in schedule}
        if polling:
            wiring["polling"] = polling
    if problems:
        raise NormalizationError(problems)
    return _plain(wiring), _env_fallbacks(raw)


def _unknown_wiring_keys(raw: Mapping[str, Any]) -> list[str]:
    problems = [
        f"{key}: is not a known configuration key"
        for key in raw
        if key not in ClientFile.model_fields
    ]
    for name, model in _LEGACY_SECTIONS.items():
        section = raw.get(name)
        if isinstance(section, Mapping):
            problems.extend(
                f"{name}.{key}: is not a known configuration key"
                for key in section
                if key not in model.model_fields
            )
    return problems


# ---- legacy configurations ---------------------------------------------------------------------


def _from_legacy(
    candidate: Mapping[str, Any], brains: Mapping[str, str], document_version: int | None
) -> Normalized:
    """Convert a merged legacy mapping (what `ClientFile` is validated from today)."""
    problems: list[str] = []
    sheets = candidate.get("sheets")
    if not isinstance(sheets, Mapping):
        problems.append(
            "sheets: section missing; a legacy configuration needs `sheets` "
            "(spreadsheet_id and tab names) to build the source and the store"
        )
    else:
        mapping = sheets.get("column_mapping")
        if isinstance(mapping, Mapping):
            problems.extend(_unknown_prefixes(mapping))
    for name in ("llm", "brain"):
        if not isinstance(candidate.get(name), Mapping):
            problems.append(f"{name}: section missing; a legacy configuration needs `{name}`")
    try:
        legacy = ClientFile.model_validate(candidate)
    except ValidationError as error:
        problems.extend(where(p) for p in error.errors())
        legacy = None
    if legacy is not None and legacy.source_apart_from_store:
        problems.append(
            f"source.provider: {legacy.source_provider!r} apart from the store "
            f"({legacy.sheets.provider!r}) cannot be converted yet"
        )
    if problems or legacy is None:
        raise NormalizationError(_unique(problems))
    return Normalized(
        _canonical_from(legacy, brains),
        _env_fallbacks(legacy.model_dump(mode="json")),
        "legacy",
        document_version,
    )


def _unique(problems: list[str]) -> list[str]:
    return list(dict.fromkeys(problems))


def _unknown_prefixes(mapping: Mapping[str, Any]) -> list[str]:
    problems = []
    for key in mapping:
        tab, dot, name = str(key).partition(".")
        if not dot or not name or tab not in KNOWN_TABS:
            problems.append(
                f"sheets.column_mapping[{key!r}]: not a known '<tab>.<field>'; "
                f"the tab must be one of {', '.join(KNOWN_TABS)}"
            )
    return problems


def _split_mapping(mapping: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """The source's (`prospects.*`) and the store's (`actions.*`, `messages.*`, ...) entries, with
    their keys and values exactly as written."""
    source = {k: v for k, v in mapping.items() if k.partition(".")[0] in SOURCE_TABS}
    store = {k: v for k, v in mapping.items() if k.partition(".")[0] in STORE_TABS}
    return source, store


def _brain_binding(factory: str, brains: Mapping[str, str]) -> dict[str, Any]:
    agent = brains.get(factory)
    return {"agent": agent} if agent is not None else {"legacy_factory": factory}


def _canonical_from(legacy: ClientFile, brains: Mapping[str, str]) -> dict[str, Any]:
    sheets, llm, whatsapp = legacy.sheets, legacy.llm, legacy.whatsapp
    source_mapping, store_mapping = _split_mapping(sheets.column_mapping)
    schedule = legacy.schedule
    return {
        "schema_version": SCHEMA_VERSION,
        "client_id": legacy.client_id,
        "display_name": legacy.display_name,
        "enabled": legacy.enabled,
        "mode": legacy.mode.value,
        "timezone": legacy.timezone,
        "daily_action_limit": legacy.daily_action_limit,
        "max_attempts": legacy.max_attempts,
        "opt_out_keywords": list(legacy.opt_out_keywords),
        "brain": {
            **_brain_binding(legacy.brain.factory, brains),
            "settings_version": legacy.brain.settings_version,
            "settings": deepcopy(dict(legacy.brain.settings)),
        },
        "source": {
            "provider": sheets.provider,
            "settings": {
                "spreadsheet_id": sheets.spreadsheet_id,
                "prospects_tab": sheets.prospects_tab,
                "column_mapping": source_mapping,
            },
        },
        "store": {
            "provider": sheets.provider,
            "connection": "same_as_source",  # the same spreadsheet plays both roles for now
            "settings": {
                "actions_tab": sheets.actions_tab,
                "messages_tab": sheets.messages_tab,
                "log_tab": sheets.log_tab,
                "column_mapping": store_mapping,
            },
        },
        "channels": [
            {
                "id": whatsapp.provider,
                "provider": whatsapp.provider,
                "enabled": True,
                "priority": 1,
                "settings": {"api_version": whatsapp.api_version},
            }
        ],
        # The upstream id is kept whole under `legacy`; turning it into a catalogue key needs the
        # catalogue, which this function does not have, so `model` stays unset.
        "llm": {"legacy": {"provider": llm.provider, "model_id": llm.model}},
        "schedule": {"daily_at": schedule.daily_at, "days": list(schedule.days)},
        "polling": {key: getattr(schedule, key) for key in _POLLING_KEYS},
        "inbound": legacy.inbound.model_dump(mode="json"),
    }


# ---- environment references --------------------------------------------------------------------


def _default(model: type, name: str) -> str:
    return model.model_fields[name].default


def _env_fallbacks(source: Mapping[str, Any]) -> dict[str, str]:
    """The `*_env` names of a legacy configuration, by the logical credential key they stand for.

    Only names, never values. The keys are the ones the runtime resolves today (so the OpenAI key
    name belongs to `llm.api_key_env` whatever `llm.provider` says, as it does now). An absent
    optional name is absent here; a name with a default in the legacy schema gets that default.
    """
    sheets = source.get("sheets") if isinstance(source.get("sheets"), Mapping) else {}
    llm = source.get("llm") if isinstance(source.get("llm"), Mapping) else {}
    whatsapp = source.get("whatsapp") if isinstance(source.get("whatsapp"), Mapping) else {}
    names = {
        SHEETS_CREDENTIALS_FILE: sheets.get(
            "credentials_env", _default(SheetsSection, "credentials_env")
        ),
        OPENAI_API_KEY: llm.get("api_key_env", _default(LLMSection, "api_key_env")),
        WHATSAPP_ACCESS_TOKEN: whatsapp.get("token_env"),
        WHATSAPP_PHONE_NUMBER_ID: whatsapp.get("phone_number_id_env"),
        WHATSAPP_APP_SECRET: whatsapp.get("app_secret_env"),
    }
    return {key: name for key, name in names.items() if name}
