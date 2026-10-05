"""The canonical (v3) shape of a client's configuration.

Provider-neutral and inert: nothing in the runtime reads it yet. A source, a store and every
channel are the same envelope, `{provider, settings}`, where `provider` is a key of a catalogue
(never a Python path) and `settings` is a free, JSON-compatible bag that the provider's own
descriptor validates later. This module therefore knows no provider: it checks only the structure
that is true for every provider (key format, unique channel ids, one channel per provider, a
priority, a model key).

Everything is immutable: models are frozen, collections are tuples, and every `settings` mapping is
a read-only deep copy. A configuration holds no secret by construction of the rest of the
application (credentials live in the credential store); this module does not look for them in
`settings`, which stays free on purpose.
"""

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from sales_app.brain_registry import BRAIN_KEY_PATTERN
from sales_app.config.schema import DEFAULT_DAYS, InboundSection, Weekday
from sales_app.config_store.models import CLIENT_ID_PATTERN, freeze_document, thaw_document
from sales_app.model_catalog import MODEL_KEY_PATTERN
from sales_core.approval import ApprovalMode

SCHEMA_VERSION = 3

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# A catalogue key for a provider, an agent (Brain) or a model. Lowercase, starts with a letter; no
# dot, colon or slash, so a Python path or a URL can never be one.
ProviderKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,31}$")]
ChannelId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
ModelKey = Annotated[str, StringConstraints(pattern=MODEL_KEY_PATTERN)]
AgentKey = Annotated[str, StringConstraints(pattern=BRAIN_KEY_PATTERN)]

_FACTORY = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

SAME_AS_SOURCE = "same_as_source"


def _freeze_settings(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """A deep, read-only copy; only JSON-compatible values are accepted."""
    try:
        return freeze_document(dict(value))
    except TypeError as error:  # pydantic turns a ValueError into a validation error, not this
        raise ValueError(str(error)) from error


# A free, JSON-compatible bag. Frozen on the way in, plain dicts again on the way out.
Settings = Annotated[
    Mapping[str, Any],
    AfterValidator(_freeze_settings),
    PlainSerializer(thaw_document, return_type=dict[str, Any]),
]


def _no_settings() -> Mapping[str, Any]:
    return freeze_document({})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderBinding(_Model):
    """A source or any other provider-backed part: which provider, with which settings."""

    provider: ProviderKey
    settings: Settings = Field(default_factory=_no_settings)


class StoreConnection(_Model):
    """How a store reaches its backend when that is not the source's own connection."""

    settings: Settings

    @field_validator("settings")
    @classmethod
    def _not_empty(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        if not value:
            raise ValueError("a connection must carry at least one setting")
        return value


class StoreBinding(ProviderBinding):
    """Where Revansa keeps its own operational state (actions, messages, facts, audit)."""

    connection: StoreConnection | Literal["same_as_source"] = SAME_AS_SOURCE


class ChannelBinding(_Model):
    """One way of talking to prospects. `id` defaults to the provider key."""

    id: ChannelId
    provider: ProviderKey
    enabled: bool = True
    priority: StrictInt = Field(default=0, ge=0)  # the smallest is used first
    settings: Settings = Field(default_factory=_no_settings)

    @model_validator(mode="before")
    @classmethod
    def _default_id(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and "id" not in data and "provider" in data:
            return {**data, "id": data["provider"]}
        return data


class LegacyLLMRef(_Model):
    """What an older configuration said: a provider name and that provider's own model id. It is
    only a stepping stone to a catalogue key and carries no meaning of its own."""

    provider: ProviderKey
    model_id: NonEmpty


class LLMBinding(_Model):
    """The model that runs the Brain, by Revansa's stable key. The provider and the upstream id
    come from the model catalogue, never from here."""

    model: ModelKey | None = None
    legacy: LegacyLLMRef | None = None

    @model_validator(mode="after")
    def _one_way_to_name_the_model(self) -> "LLMBinding":
        if self.model is None and self.legacy is None:
            raise ValueError("llm needs a model key (or, from an older configuration, `legacy`)")
        return self


class BrainBinding(_Model):
    """Which Brain, by catalogue key. `legacy_factory` is the older `package:callable` form: it may
    only come from the operator's wiring, never from a client."""

    agent: AgentKey | None = None
    legacy_factory: StrictStr | None = None
    settings_version: int = Field(default=1, ge=1)
    settings: Settings = Field(default_factory=_no_settings)

    @field_validator("legacy_factory")
    @classmethod
    def _check_factory(cls, value: str | None) -> str | None:
        if value is not None and not _FACTORY.match(value):
            raise ValueError("must look like 'package.module:callable'")
        return value

    @model_validator(mode="after")
    def _exactly_one_way_to_name_the_brain(self) -> "BrainBinding":
        if (self.agent is None) == (self.legacy_factory is None):
            raise ValueError("brain needs exactly one of `agent` and `legacy_factory`")
        return self


class ScheduleSettings(_Model):
    # Quote the time in YAML: an unquoted 10:30 is read as the number 630.
    daily_at: StrictStr = "09:00"
    days: tuple[Weekday, ...] = DEFAULT_DAYS

    @field_validator("daily_at")
    @classmethod
    def _check_daily_at(cls, value: str) -> str:
        if not _CLOCK.match(value):
            raise ValueError("must be a quoted 'HH:MM' time, e.g. \"09:00\"")
        return value

    @field_validator("days")
    @classmethod
    def _check_days(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("must list at least one day")
        if len(set(value)) != len(value):
            raise ValueError("lists a day twice")
        return value


class PollingSettings(_Model):
    """How often the scheduler looks at the client (operator wiring)."""

    prepare_every_minutes: int = Field(default=60, ge=5, le=1440)
    dispatch_every_minutes: int = Field(default=5, ge=1, le=1440)
    recover_every_minutes: int = Field(default=15, ge=1, le=1440)


class ClientSpec(_Model):
    """A client's whole configuration in the canonical v3 shape."""

    schema_version: Literal[3]
    client_id: str = Field(pattern=CLIENT_ID_PATTERN.pattern)
    display_name: NonEmpty
    enabled: bool = True
    mode: ApprovalMode = ApprovalMode.DRY_RUN  # safest default
    timezone: NonEmpty  # IANA name; the planner's day boundary and the scheduler both use it
    daily_action_limit: int = Field(default=20, ge=1, le=1000)
    max_attempts: int = Field(default=3, ge=1, le=20)
    opt_out_keywords: tuple[NonEmpty, ...] = ("stop",)
    brain: BrainBinding
    source: ProviderBinding
    store: StoreBinding | None = None  # absent: kept where the source is (if it can host one)
    channels: tuple[ChannelBinding, ...] = ()  # none is valid; a runtime decides what that means
    llm: LLMBinding
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    polling: PollingSettings = Field(default_factory=PollingSettings)
    inbound: InboundSection = Field(default_factory=InboundSection)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as error:
            raise ValueError(f"unknown timezone {value!r}") from error
        return value

    @model_validator(mode="after")
    def _channels_are_distinct(self) -> "ClientSpec":
        providers = [channel.provider for channel in self.channels]
        for provider in sorted({p for p in providers if providers.count(p) > 1}):
            raise ValueError(f"more than one channel for provider {provider!r}")
        ids = [channel.id for channel in self.channels]
        for channel_id in sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"channel id {channel_id!r} is used more than once")
        return self
