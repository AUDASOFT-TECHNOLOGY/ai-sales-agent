"""From a canonical `ClientSpec` to what the runtime runs on, with the legacy behaviour kept exactly.

The runtime (`build_runtime`, the registry, the webhook parsers) reads a `ClientConfig`. This module
is the boundary that lets a canonical spec become one, so a client described in the canonical (v3)
shape runs through the very same wiring as a legacy one:

    resolve_client_spec  ->  client_config_from_spec  ->  ClientConfig  ->  build_runtime

- The spec is mapped to the legacy `ClientFile` the wiring reads (`legacy_file_from_spec`, the
  inverse of the normaliser), and then judged by the same last steps as a legacy client
  (`complete_client_config`: the Brain's own rules, the mode, the credentials, the model allow-list).
  Nothing of that resolution is written twice.
- What the runtime cannot run yet is refused, in words, instead of being half-run: a store kept
  apart from the source, a model named only by a catalogue key (there is no catalogue yet), settings
  a provider's runtime does not understand yet.
- Exactly one enabled channel is run. None: the client is not ready. Several: the configuration is
  refused. Which provider the channel is comes from the spec, never from a legacy section name
  (`channel_provider_key`).

The Core knows nothing of this: the mapping to the objects the Core expects stays in `sales_app`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sales_app.brain_registry import BrainRegistry, installed_brains
from sales_app.config.canonical import ClientSpec
from sales_app.config.llm_registry import EMPTY_LLM_REGISTRY, LLMRegistry
from sales_app.config.loader import ClientConfig, Resolution, complete_client_config
from sales_app.config.resolution import where
from sales_app.config.schema import ClientFile
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    SHEETS_CREDENTIALS_FILE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
from sales_app.config.spec_resolution import SpecResolution, resolve_client_spec
from sales_app.secret_store import SecretStore
from sales_core.approval import ApprovalMode

NO_CHANNEL = "no_channel"  # the `whatsapp` section a client without any enabled channel carries
NO_CHANNEL_REASON = "no channel is enabled"

_SOURCE_SETTINGS = ("spreadsheet_id", "prospects_tab", "column_mapping")
_STORE_SETTINGS = ("actions_tab", "messages_tab", "log_tab", "column_mapping")
_CHANNEL_SETTINGS = ("api_version",)


class NotRunnableError(ValueError):
    """The spec describes something the runtime cannot run yet. `problems` says what, in words."""

    def __init__(self, problems: list[str] | tuple[str, ...]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


class MultipleChannelsError(ValueError):
    """More than one channel is enabled: this version runs exactly one."""


@dataclass(frozen=True, slots=True)
class CanonicalClientConfig(ClientConfig):
    """A `ClientConfig` that came from a canonical spec; it keeps the spec it came from.

    It is a `ClientConfig` in every respect (the registry, the runtime and the Admin read it as one),
    and `spec` is the legacy-shaped view the wiring reads.
    """

    canonical: ClientSpec | None = None
    env_fallbacks: Mapping[str, str] = field(default_factory=dict)  # names only, never values


def active_channels(spec: ClientSpec) -> tuple[Any, ...]:
    """The enabled channels of a spec, in the order it lists them."""
    return tuple(channel for channel in spec.channels if channel.enabled)


def channel_provider_key(config: ClientConfig) -> str | None:
    """The provider of the channel this client runs on; `None` if it has none.

    A canonical client has exactly one enabled channel, or none; a legacy one always has its
    `whatsapp` section's provider. Several enabled channels is an error: it is refused earlier, and
    this refuses to guess if one gets here anyway.
    """
    if isinstance(config, CanonicalClientConfig) and config.canonical is not None:
        enabled = active_channels(config.canonical)
        if len(enabled) > 1:
            ids = ", ".join(channel.id for channel in enabled)
            raise MultipleChannelsError(f"more than one channel is enabled ({ids})")
        return enabled[0].provider if enabled else None
    return config.spec.whatsapp.provider


# ---- ClientSpec -> the legacy view the wiring reads ----------------------------------------------


def legacy_file_from_spec(
    spec: ClientSpec,
    env_fallbacks: Mapping[str, str] | None = None,
    brains: BrainRegistry | None = None,
    *,
    allow_legacy_factory: bool = False,
) -> ClientFile:
    """The `ClientFile` a canonical spec stands for, or `NotRunnableError`.

    `env_fallbacks` are the legacy `*_env` names (they never lived in the spec). A Brain named by
    `agent` stays a key: it is checked against `brains` (default: those installed) and handed on
    as a key, never turned into a path. A `legacy_factory` is a Python path, so it is only carried
    over when the caller says it comes from the operator's own legacy wiring.
    """
    names = env_fallbacks or {}
    data = spec.model_dump(mode="json")
    problems: list[str] = []

    source, store = data["source"], data["store"]
    source_settings, store_settings = (
        dict(source["settings"]),
        dict((store or {}).get("settings", {})),
    )
    problems += _unknown("source.settings", source_settings, _SOURCE_SETTINGS)
    problems += _unknown("store.settings", store_settings, _STORE_SETTINGS)
    if store is not None:
        if store["connection"] != "same_as_source":
            problems.append("store: a store kept apart from the source cannot run yet")
        if store["provider"] != source["provider"]:
            problems.append(
                f"store: the store of {store['provider']!r} cannot be kept with the source of "
                f"{source['provider']!r} yet"
            )
    if "spreadsheet_id" not in source_settings:
        problems.append("source.settings.spreadsheet_id: required by the runtime")
    shared = set(source_settings.get("column_mapping", {})) & set(
        store_settings.get("column_mapping", {})
    )
    if shared:
        problems.append(
            f"column_mapping: {sorted(shared)} is mapped by both the source and the store"
        )

    llm = data["llm"]
    if llm["legacy"] is None:
        problems.append(
            f"llm.model: {llm['model']!r} is a catalogue key, and the model catalogue is not "
            "available to the runtime yet"
        )

    brain = data["brain"]
    if brain["legacy_factory"] is not None:
        if not allow_legacy_factory:
            problems.append(
                "brain.legacy_factory: a Python path is not accepted from a canonical "
                "configuration; name the Brain with `brain.agent`"
            )
    else:
        reason = (brains or installed_brains()).problem_for(brain["agent"])
        if reason is not None:
            problems.append(f"brain.agent: {brain['agent']!r} is not a Brain this runtime can find")

    channels = [c for c in data["channels"] if c["enabled"]]
    channel = channels[0] if len(channels) == 1 else None
    if len(channels) > 1:
        problems.append("channels: more than one channel is enabled")
    if channel is not None:
        problems += _unknown(
            f"channels[{channel['id']}].settings", channel["settings"], _CHANNEL_SETTINGS
        )

    if problems:
        raise NotRunnableError(problems)

    whatsapp: dict[str, Any] = {
        "provider": channel["provider"] if channel is not None else NO_CHANNEL,
        **(
            {"api_version": channel["settings"]["api_version"]}
            if channel and "api_version" in channel["settings"]
            else {}
        ),
        **_present(
            token_env=names.get(WHATSAPP_ACCESS_TOKEN),
            phone_number_id_env=names.get(WHATSAPP_PHONE_NUMBER_ID),
            app_secret_env=names.get(WHATSAPP_APP_SECRET),
        ),
    }
    candidate = {
        "client_id": data["client_id"],
        "display_name": data["display_name"],
        "enabled": data["enabled"],
        "mode": data["mode"],
        "timezone": data["timezone"],
        "daily_action_limit": data["daily_action_limit"],
        "max_attempts": data["max_attempts"],
        "opt_out_keywords": data["opt_out_keywords"],
        "brain": {
            "factory": brain["legacy_factory"],
            "agent": brain["agent"],
            "settings": brain["settings"],
            "settings_version": brain["settings_version"],
        },
        "sheets": {
            "provider": source["provider"],
            **{k: v for k, v in source_settings.items() if k != "column_mapping"},
            **{k: v for k, v in store_settings.items() if k != "column_mapping"},
            "column_mapping": {
                **source_settings.get("column_mapping", {}),
                **store_settings.get("column_mapping", {}),
            },
            **_present(
                credentials_env=names.get(SHEETS_CREDENTIALS_FILE),
            ),
        },
        "llm": {
            "provider": llm["legacy"]["provider"],
            "model": llm["legacy"]["model_id"],
            **_present(api_key_env=names.get(OPENAI_API_KEY)),
        },
        "whatsapp": whatsapp,
        "schedule": {**data["schedule"], **data["polling"]},
        "inbound": data["inbound"],
    }
    try:
        return ClientFile.model_validate(candidate)
    except ValidationError as error:
        raise NotRunnableError([where(p) for p in error.errors()]) from error


def _unknown(path: str, settings: Mapping[str, Any], understood: tuple[str, ...]) -> list[str]:
    return [
        f"{path}.{key}: not understood by the runtime yet (it reads {', '.join(understood)})"
        for key in sorted(set(settings) - set(understood))
    ]


def _present(**values: str | None) -> dict[str, str]:
    return {key: value for key, value in values.items() if value}


# ---- a canonical spec becomes a ClientConfig -----------------------------------------------------


def client_config_from_spec(
    resolution: SpecResolution,
    directory: Path,
    *,
    env: Mapping[str, str],
    mode_ceiling: ApprovalMode,
    llm_registry: LLMRegistry = EMPTY_LLM_REGISTRY,
    secret_store: SecretStore | None = None,
    brains: BrainRegistry | None = None,
) -> Resolution:
    """The `Resolution` (a config, "disabled", or problems) for a resolved canonical spec.

    A disabled client and a client whose spec has errors come out as `resolve_client_config` would
    report them. Otherwise the spec is mapped to what the wiring reads and judged by the same last
    steps as any legacy client. Exactly one enabled channel runs; none leaves the client not ready;
    several are refused.
    """
    name = directory.name
    if resolution.disabled:
        return Resolution(disabled=True)
    spec = resolution.spec
    if spec is None or resolution.errors:
        return Resolution(errors=resolution.errors)

    enabled = active_channels(spec)
    if len(enabled) > 1:
        ids = ", ".join(channel.id for channel in enabled)
        return Resolution(
            errors=(
                f"{name}: more than one channel is enabled ({ids}); this version runs exactly one",
            )
        )
    try:
        legacy = legacy_file_from_spec(
            spec,
            resolution.env_fallbacks,
            brains,
            # A client.yaml written in the canonical shape is the client's: it may not name a path.
            allow_legacy_factory=resolution.origin == "legacy",
        )
    except NotRunnableError as error:
        return Resolution(errors=tuple(f"{name}: {problem}" for problem in error.problems))

    done = complete_client_config(
        legacy,
        name=name,
        directory=directory,
        config_version=resolution.config_version,
        env=env,
        mode_ceiling=mode_ceiling,
        llm_registry=llm_registry,
        secret_store=secret_store,
        extra_missing=() if enabled else (NO_CHANNEL_REASON,),
        brains=brains,
    )
    config = done.config
    if config is None:
        return done
    return Resolution(
        config=CanonicalClientConfig(
            config.spec,
            config.directory,
            config.effective_mode,
            config.secrets,
            config_version=config.config_version,
            config_source=config.config_source,
            missing_credentials=config.missing_credentials,
            canonical=spec,
            env_fallbacks=dict(resolution.env_fallbacks),
        )
    )


def resolve_canonical_client_config(
    directory: Path,
    *,
    env: Mapping[str, str],
    mode_ceiling: ApprovalMode,
    store: Any = None,
    registry: Any = None,
    known_brains: Mapping[str, str] | None = None,
    brains: BrainRegistry | None = None,
    llm_registry: LLMRegistry = EMPTY_LLM_REGISTRY,
    secret_store: SecretStore | None = None,
) -> Resolution:
    """`resolve_client_spec` and `client_config_from_spec` in one: a client directory to what the
    runtime runs on, through the canonical spec. `known_brains` (factory path -> catalogue key) is
    only for the legacy normaliser, which then names a Brain by its key; the Brain itself is found
    in `brains` (default: the installed ones), never by a path."""
    resolved = resolve_client_spec(
        directory, store=store, registry=registry, known_brains=known_brains
    )
    return client_config_from_spec(
        resolved,
        directory,
        env=env,
        mode_ceiling=mode_ceiling,
        llm_registry=llm_registry,
        secret_store=secret_store,
        brains=brains,
    )
