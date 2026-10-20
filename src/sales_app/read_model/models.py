"""What a client's lists of prospects and conversations are made of. Pure data: no SQL, no Engine.

These objects are the contract between the Client API and whatever reads the data (the SQL read
model, or, when the database holds nothing yet, the Engine's own view turned into the same shape).
Nothing here names a client: the reader is bound to one client and cannot be asked for another.

Absence is told as it is: a value the platform does not hold is `None`, never a zero or a guess.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# The most a page may hold. Documented in docs/read-model-and-metrics.md; asking for more is refused
# by the API (422), never silently cut.
MAX_PAGE = 100
DEFAULT_PAGE = 50
MAX_MESSAGE_PAGE = 500
DEFAULT_MESSAGE_PAGE = 200


class ProspectSort(StrEnum):
    NAME = "name"  # by name then id: the historical order of `/client/v1/prospects`
    RECENT = "recent"  # latest activity first; people with none last, then by name


class LifecycleStatus(StrEnum):
    """The values a prospect's status takes (the Core's lifecycle), as the lists filter on them."""

    NEW = "new"
    ACTIVE = "active"
    PAUSED = "paused"  # waiting for a person
    OPTED_OUT = "opted_out"
    CLOSED = "closed"


class MessageOrder(StrEnum):
    ASC = "asc"  # oldest first
    DESC = "desc"  # newest first


@dataclass(frozen=True, slots=True, kw_only=True)
class ProspectFilter:
    """Every field narrows; an empty filter keeps everyone.

    `lane_ids` is the resolved "circuit" (a product is turned into the lanes that sell it by the
    caller): None means no restriction, an empty tuple means no lane qualifies, so nobody does.
    """

    statuses: tuple[str, ...] = ()
    lane_ids: tuple[str, ...] | None = None
    channel: str | None = None
    source_id: str | None = None
    needs_human: bool | None = None
    has_error: bool | None = None
    active_since: datetime | None = None  # last activity at or after
    active_until: datetime | None = None  # last activity before
    search: str | None = None  # name, e-mail address or phone number

    def __post_init__(self) -> None:
        for moment in (self.active_since, self.active_until):
            if moment is not None and moment.tzinfo is None:
                raise ValueError("a period bound must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProspectListQuery:
    filter: ProspectFilter = field(default_factory=ProspectFilter)
    sort: ProspectSort = ProspectSort.NAME
    limit: int = DEFAULT_PAGE
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE}")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProspectItem:
    prospect_id: str
    display_name: str
    status: str
    stage: str | None = None
    locale: str | None = None
    lane_id: str | None = None
    source_id: str | None = None
    phone: str | None = None
    email: str | None = None
    channel: str | None = None
    next_action_type: str | None = None
    next_action_at: datetime | None = None
    last_contacted_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_activity_at: datetime | None = None
    follow_up_count: int = 0
    needs_human: bool = False
    status_reason: str | None = None
    conversation_id: str | None = None
    # None: not known (the Engine's view cannot say); False/True: read from the actions.
    has_error: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationItem:
    """A prospect with whom messages were exchanged, and the latest of them."""

    prospect: ProspectItem
    message_count: int | None  # None: not known from the Engine view
    last_message_at: datetime | None
    last_message_direction: str | None  # inbound | outbound


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageItem:
    message_id: str
    direction: str
    channel: str
    text: str
    timestamp: datetime
    external_id: str | None = None  # the channel provider's id; for the API's own use, not shown


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionError:
    """An action that failed: what was attempted, when, and the reason the platform recorded."""

    action_id: str
    action_type: str
    due_at: datetime | None
    attempts: int
    reason: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationDetail:
    prospect: ProspectItem
    conversation_id: str
    messages: tuple[MessageItem, ...]
    total_messages: int
    limit: int
    offset: int
    order: MessageOrder
    errors: tuple[ActionError, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Page[T]:
    items: tuple[T, ...]
    total: int
    limit: int
    offset: int
