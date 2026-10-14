"""The shape of clients/<client_id>/client.yaml. Unknown keys are rejected so typos fail loudly."""

import re
from datetime import time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from sales_app.brain_registry import BRAIN_KEY_PATTERN
from sales_app.config.email_settings import EmailChannelSettings
from sales_app.config.telegram_settings import TelegramChannelSettings
from sales_core.approval import ApprovalMode

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DEFAULT_DAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat")  # Sunday off

_PLACEHOLDERS = {"todo", "change_me", "changeme", "tbd", "xxx"}
_FACTORY = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_CLIENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _reject_placeholder(value: str) -> str:
    if value.strip().lower() in _PLACEHOLDERS or "CHANGE_ME" in value:
        raise ValueError("is still a placeholder")
    return value


class BrainSection(_Section):
    # Exactly one of the two. `factory` is the operator's legacy "package.module:callable". `agent`
    # is a catalogue key, found among the installed Brains and never turned into a path.
    factory: str | None = None
    agent: Annotated[str, StringConstraints(pattern=BRAIN_KEY_PATTERN)] | None = None
    settings: dict[str, Any] = Field(
        default_factory=dict
    )  # handed to the Brain (validated by its spec)
    settings_version: int = Field(default=1, ge=1)  # which version of the Brain's settings schema

    @field_validator("factory")
    @classmethod
    def _check_factory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _FACTORY.match(value):
            raise ValueError("must look like 'package.module:callable'")
        return _reject_placeholder(value)

    @model_serializer(mode="wrap")
    def _leave_out_the_unused_way(self, handler: Any) -> dict[str, Any]:
        """A legacy section dumps exactly as before: the way not used is not written."""
        data = handler(self)
        for name in ("factory", "agent"):
            if data.get(name) is None:
                data.pop(name, None)
        return data

    @model_validator(mode="after")
    def _exactly_one_way_to_name_the_brain(self) -> "BrainSection":
        if (self.factory is None) == (self.agent is None):
            raise ValueError("brain needs exactly one of `factory` and `agent`")
        return self


class SheetsSection(_Section):
    provider: NonEmpty = "google_sheets"
    spreadsheet_id: NonEmpty
    prospects_tab: NonEmpty = "Prospects"
    actions_tab: NonEmpty = "Actions"
    messages_tab: NonEmpty = "Messages"
    log_tab: NonEmpty = "Log"
    column_mapping: dict[str, str] = Field(default_factory=dict)  # '<tab>.<field>' -> sheet header
    credentials_env: EnvName = "GOOGLE_APPLICATION_CREDENTIALS"

    @field_validator("spreadsheet_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _reject_placeholder(value)


DEFAULT_SOURCE = "google_sheets"


class SourceSection(_Section):
    """Which provider the client's prospects come from. Exactly one is active at a time.

    It is a section of its own, apart from `sheets`, so that a source and a store can be told apart
    later. Without it, the source is the store's provider (`sheets.provider`), as it has always been;
    with it, `sheets` still describes the spreadsheet that holds Revansa's own state (the store).
    A source other than the store's provider is run only if that provider can be (the runtime
    refuses the others, and never replaces it by the store's); which providers exist, and what
    their `settings` mean, is the provider registry's business, not the schema's. `settings` holds
    the operator's choices for that provider (never a secret) and is left out of a dump when empty.
    """

    provider: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,31}$")]
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _leave_out_empty_settings(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not data.get("settings"):
            data.pop("settings", None)
        return data


class ChannelSection(_Section):
    """Which provider the client talks to its prospects through, when it chose one itself.

    Without it the channel is the operator's (`whatsapp.provider`). `settings` holds the client's
    non-secret choices for that provider (never a password) and is left out of a dump when empty.
    Whether the client may use that channel at all is the Admin's decision, judged when the
    configuration is resolved (see `sales_app.config.channel_access`), not here.
    """

    provider: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,31}$")]
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_settings(self) -> "ChannelSection":
        if self.provider == "email" and self.settings:
            EmailChannelSettings.model_validate(self.settings)  # a half-written mailbox is refused
        if self.provider == "telegram" and self.settings:
            TelegramChannelSettings.model_validate(self.settings)
        return self

    @model_serializer(mode="wrap")
    def _leave_out_empty_settings(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not data.get("settings"):
            data.pop("settings", None)
        return data


_INSTANCE_ID = r"^[a-z][a-z0-9_]{1,31}$"  # also the prefix of the ids its prospects get
_LANE_ID = r"^[a-z][a-z0-9_-]{0,31}$"


class SourceInstance(_Section):
    """One configured source of prospects. A client may have several, of the same provider or not.

    `id` names the instance and is what its prospects' ids are prefixed with (`<id>:<native id>`),
    so it can never change once prospects exist. `settings` holds the operator's non-secret choices
    for that provider (never a secret), validated by the provider's own descriptor.
    """

    id: Annotated[str, StringConstraints(pattern=_INSTANCE_ID)]
    provider: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,31}$")]
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _leave_out_empty_settings(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not data.get("settings"):
            data.pop("settings", None)
        return data


class LaneSection(_Section):
    """One commercial lane: the people of one source, the channel they are written to on, and the
    products offered to them (ids of the Brain's catalog). Several lanes may share a channel; a
    source belongs to at most one lane, so a person is never in two."""

    id: Annotated[str, StringConstraints(pattern=_LANE_ID)]
    source: Annotated[str, StringConstraints(pattern=_INSTANCE_ID)]  # the id of a `SourceInstance`
    channel: Annotated[
        str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    ]  # a channel provider
    products: tuple[NonEmpty, ...] = Field(
        min_length=1
    )  # explicit: a lane with no product sells nothing

    @field_validator("products")
    @classmethod
    def _distinct_products(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("lists a product twice")
        return value


class LLMSection(_Section):
    provider: NonEmpty = "openai"
    api_key_env: EnvName = "OPENAI_API_KEY"
    model: NonEmpty

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        return _reject_placeholder(value)


class WhatsAppSection(_Section):
    provider: NonEmpty = "whatsapp"
    # Environment variable NAMES, used only when the credential store holds no value (see
    # sales_app.config.loader). Optional: credentials normally come from the store, set in Admin.
    token_env: EnvName | None = None
    phone_number_id_env: EnvName | None = None
    app_secret_env: EnvName | None = None  # a client may point at a Meta app of its own
    api_version: NonEmpty = "v21.0"  # the default; a global setting in the store wins

    @field_validator("token_env", "phone_number_id_env", "app_secret_env")
    @classmethod
    def _check_env(cls, value: str | None) -> str | None:
        return None if value is None else _reject_placeholder(value)


class ScheduleSection(_Section):
    # Quote the time in YAML: an unquoted 10:30 is read as the number 630.
    daily_at: StrictStr = "09:00"
    days: tuple[Weekday, ...] = DEFAULT_DAYS
    # Extra planning passes after the daily cycle: pick up prospects added to the sheet, and
    # follow-ups or callbacks that fall due later in the day, while the daily quota lasts.
    prepare_every_minutes: int = Field(default=60, ge=5, le=1440)
    dispatch_every_minutes: int = Field(default=5, ge=1, le=1440)
    recover_every_minutes: int = Field(default=15, ge=1, le=1440)

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

    @property
    def daily_time(self) -> time:
        hours, minutes = self.daily_at.split(":")
        return time(int(hours), int(minutes))

    @property
    def weekdays(self) -> frozenset[int]:
        return frozenset(WEEKDAY_INDEX[d] for d in self.days)


# What the agent may decide in a conversation the prospect started (enforced by the Core later).
InboundDecision = Literal[
    "send_message", "schedule_follow_up", "handoff_to_human", "close", "no_action"
]
ALL_INBOUND_DECISIONS: tuple[InboundDecision, ...] = (
    "send_message",
    "schedule_follow_up",
    "handoff_to_human",
    "close",
    "no_action",
)


class SpontaneousSection(_Section):
    """Conversations a prospect starts. Off unless the client turns it on."""

    enabled: bool = False
    when_disabled: Literal["store_and_flag", "handoff"] = "store_and_flag"
    allowed_decisions: tuple[InboundDecision, ...] = ALL_INBOUND_DECISIONS

    @field_validator("allowed_decisions")
    @classmethod
    def _check_decisions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("must allow at least one decision")
        if len(set(value)) != len(value):
            raise ValueError("lists a decision twice")
        return value


class UnknownContactsSection(_Section):
    """People who write and are not in the Sheet. A prospect is created only for `engage`."""

    policy: Literal["ignore", "engage"] = "ignore"
    max_new_per_day: int = Field(default=30, ge=0, le=1000)


class InboundLimitsSection(_Section):
    max_replies_per_prospect_per_day: int = Field(default=20, ge=1, le=200)


class InboundSection(_Section):
    """Policy for inbound conversations. Stored and validated now; the Engine enforces it in a
    later block, so today it changes nothing at runtime."""

    spontaneous_conversations: SpontaneousSection = Field(default_factory=SpontaneousSection)
    unknown_contacts: UnknownContactsSection = Field(default_factory=UnknownContactsSection)
    limits: InboundLimitsSection = Field(default_factory=InboundLimitsSection)

    @model_validator(mode="after")
    def _engage_needs_spontaneous(self) -> "InboundSection":
        if self.unknown_contacts.policy == "engage" and not self.spontaneous_conversations.enabled:
            raise ValueError(
                "unknown_contacts.policy 'engage' requires spontaneous_conversations.enabled"
            )
        return self


class ClientFile(_Section):
    client_id: str
    display_name: NonEmpty
    enabled: bool = True
    mode: ApprovalMode = ApprovalMode.DRY_RUN  # safest default
    timezone: NonEmpty  # IANA name; the planner's day boundary and the scheduler both use it
    daily_action_limit: int = Field(default=20, ge=1, le=1000)
    max_attempts: int = Field(default=3, ge=1, le=20)
    opt_out_keywords: tuple[NonEmpty, ...] = ("stop",)
    brain: BrainSection
    sheets: SheetsSection
    source: SourceSection | None = None  # None: the source is the store's provider
    llm: LLMSection
    whatsapp: WhatsAppSection
    channel: ChannelSection | None = None  # None: the channel is `whatsapp.provider`
    # Several sources, each with its own channel and products. Both or neither: a client with
    # neither has the single source, channel and catalog it has always had (see `lanes.py`).
    sources: tuple[SourceInstance, ...] | None = None
    lanes: tuple[LaneSection, ...] | None = None
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)
    inbound: InboundSection = Field(default_factory=InboundSection)

    @model_serializer(mode="wrap")
    def _leave_out_what_was_never_chosen(self, handler: Any) -> dict[str, Any]:
        """A configuration that never chose a source or a channel dumps exactly as before."""
        data = handler(self)
        for key in ("source", "channel", "sources", "lanes"):
            if data.get(key) is None:
                data.pop(key, None)
        return data

    @property
    def channel_provider(self) -> str:
        """The provider prospects are talked to through: the chosen one, else the operator's."""
        return self.channel.provider if self.channel is not None else self.whatsapp.provider

    @property
    def channel_settings(self) -> dict[str, Any]:
        return dict(self.channel.settings) if self.channel is not None else {}

    @property
    def channel_providers(self) -> tuple[str, ...]:
        """Every channel provider the client talks through: each lane's, once (in lane order), or
        the one channel of a client with no lanes."""
        if self.lanes is None:
            return (self.channel_provider,)
        return tuple(dict.fromkeys(lane.channel for lane in self.lanes))

    def channel_settings_for(self, provider: str) -> dict[str, Any]:
        """The client's non-secret settings for one channel provider. With lanes the `channel`
        section only carries the settings of the one channel it names (a mailbox's servers); any
        other channel of the client has none and runs on its defaults."""
        if self.channel is not None and self.channel.provider == provider:
            return dict(self.channel.settings)
        return {}

    @property
    def source_provider(self) -> str:
        """The provider the prospects come from: the chosen one, else the store's own."""
        return self.source.provider if self.source is not None else self.sheets.provider

    @property
    def source_settings(self) -> dict[str, Any]:
        """The operator's settings for the chosen source provider (empty if there are none)."""
        return dict(self.source.settings) if self.source is not None else {}

    @property
    def source_apart_from_store(self) -> bool:
        """Whether a source other than the store's provider was chosen."""
        return self.source_provider != self.sheets.provider

    @model_validator(mode="after")
    def _lanes_are_coherent(self) -> "ClientFile":
        if (self.sources is None) != (self.lanes is None):
            raise ValueError("`sources` and `lanes` go together: give both or neither")
        if self.lanes is None or self.sources is None:
            return self
        if not self.lanes:
            raise ValueError("lanes: list at least one lane, or leave `sources` and `lanes` out")
        if self.source is not None:
            raise ValueError("lanes replace `source`: a client has either lanes or a source")
        if self.channel is not None and self.channel.provider not in {
            lane.channel for lane in self.lanes
        }:
            raise ValueError(
                "channel: with lanes it only holds the settings of a channel a lane uses "
                f"({self.channel.provider!r} is used by none)"
            )
        source_ids = [s.id for s in self.sources]
        for duplicate in sorted({i for i in source_ids if source_ids.count(i) > 1}):
            raise ValueError(f"sources: id {duplicate!r} is used more than once")
        lane_ids = [lane.id for lane in self.lanes]
        for duplicate in sorted({i for i in lane_ids if lane_ids.count(i) > 1}):
            raise ValueError(f"lanes: id {duplicate!r} is used more than once")
        used: dict[str, str] = {}
        for lane in self.lanes:
            if lane.source not in source_ids:
                raise ValueError(f"lanes: {lane.id!r} uses the unknown source {lane.source!r}")
            if lane.source in used:
                raise ValueError(
                    f"lanes: {lane.id!r} and {used[lane.source]!r} share the source "
                    f"{lane.source!r}; give a lane several products instead"
                )
            used[lane.source] = lane.id
        return self

    @field_validator("client_id")
    @classmethod
    def _check_client_id(cls, value: str) -> str:
        if not _CLIENT_ID.match(value):
            raise ValueError("must be lowercase letters, digits, '-' or '_'")
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as error:
            raise ValueError(f"unknown timezone {value!r}") from error
        return value
