"""Configuration and result types of the Engine."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sales_core.approval import ApprovalMode
from sales_core.contracts import ActionId, ActionStatus, MessageId, ProspectId
from sales_core.planner import DEFAULT_DAILY_ACTION_LIMIT
from sales_core.safety import DEFAULT_OPT_OUT_KEYWORDS

DEFAULT_MAX_NEW_PER_DAY = 30


class UnknownSenderPolicy(StrEnum):
    IGNORE = "ignore"  # audit the message; create nothing, call no Brain
    ENGAGE = "engage"  # create a prospect from what the message carries and answer it


@dataclass(frozen=True, slots=True, kw_only=True)
class InboundPolicy:
    """What the Engine does with messages from people who are not prospects yet.

    Messages from known prospects are always handled; this only governs strangers. The default is
    the safe side: strangers are audited and ignored.
    """

    spontaneous_conversations: bool = False  # may the Engine answer a conversation they started?
    unknown_sender: UnknownSenderPolicy = UnknownSenderPolicy.IGNORE
    # Prospects the Engine may create from strangers per local day of the client. 0 creates none.
    max_new_per_day: int = DEFAULT_MAX_NEW_PER_DAY

    def __post_init__(self) -> None:
        if self.max_new_per_day < 0:
            raise ValueError("max_new_per_day must be >= 0")
        if self.unknown_sender is UnknownSenderPolicy.ENGAGE and not self.spontaneous_conversations:
            raise ValueError("unknown_sender 'engage' requires spontaneous_conversations")

    @property
    def engages_unknown_senders(self) -> bool:
        return self.spontaneous_conversations and self.unknown_sender is UnknownSenderPolicy.ENGAGE


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingProposal:
    """A message the Brain proposed and a person has yet to approve or reject (read-only view)."""

    action_id: ActionId
    prospect_id: ProspectId
    prospect_name: str | None  # None: the prospect is no longer in the source
    text: str
    language: str | None
    due_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineConfig:
    mode: ApprovalMode
    daily_action_limit: int = DEFAULT_DAILY_ACTION_LIMIT
    max_attempts: int = 3  # failed Brain calls / transient send failures before FAILED
    opt_out_keywords: frozenset[str] = DEFAULT_OPT_OUT_KEYWORDS
    inbound: InboundPolicy = field(default_factory=InboundPolicy)
    # Telegram from the client's own account: the prospect attribute (a source column) in which the
    # client states that this person expects to be contacted there. A person who carries it may be
    # written to first; without it, only a person who wrote first may be. None: nobody may.
    telegram_consent_attribute: str | None = None

    def __post_init__(self) -> None:
        if self.daily_action_limit < 0:
            raise ValueError("daily_action_limit must be >= 0")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        object.__setattr__(
            self, "opt_out_keywords", frozenset(k.strip().casefold() for k in self.opt_out_keywords)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UpcomingActions:
    """Prospects the Engine may still contact that have a next action recorded."""

    scheduled: int
    by_type: dict[str, int]  # only the types present, never a zero invented for an absent one
    next_due_at: datetime | None  # the earliest of them, None when nothing is scheduled


@dataclass(frozen=True, slots=True, kw_only=True)
class ProspectSummary:
    """Counts over one client's prospects, read in one pass (nothing is estimated)."""

    total: int
    by_status: dict[str, int]  # every status, 0 only when the client really has none
    upcoming: UpcomingActions


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionOutcome:
    action_id: ActionId
    status: ActionStatus | None  # None when nothing was persisted (dry run)
    detail: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunReport:
    """What one Engine call did. `errors` is never silently empty when something went wrong."""

    outcomes: tuple[ActionOutcome, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def statuses(self) -> dict[ActionId, ActionStatus | None]:
        """Final status per action (the last outcome recorded for it)."""
        return {o.action_id: o.status for o in self.outcomes}


class InboundStatus(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"  # already stored (webhook redelivery)
    UNKNOWN_SENDER = "unknown_sender"  # no prospect has this address; nothing stored
    DRY_RUN = "dry_run"  # dry-run engines persist nothing


@dataclass(frozen=True, slots=True, kw_only=True)
class InboundReceipt:
    """Result of the fast webhook path; feed it to `Engine.process_inbound`."""

    status: InboundStatus
    message_id: MessageId | None = None
    prospect_id: ProspectId | None = None
    errors: tuple[str, ...] = ()  # non-fatal problems, e.g. an audit write that must be retried


@dataclass(slots=True)
class RunCollector:
    """Mutable accumulator used inside one Engine call."""

    outcomes: list[ActionOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def outcome(
        self, action_id: ActionId, status: ActionStatus | None, detail: str | None = None
    ) -> None:
        self.outcomes.append(ActionOutcome(action_id=action_id, status=status, detail=detail))

    def error(self, message: str) -> None:
        self.errors.append(message)

    def report(self) -> RunReport:
        return RunReport(outcomes=tuple(self.outcomes), errors=tuple(self.errors))
