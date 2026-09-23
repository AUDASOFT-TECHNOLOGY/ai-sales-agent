"""Configuration and result types of the Engine."""

from dataclasses import dataclass, field
from enum import StrEnum

from sales_core.approval import ApprovalMode
from sales_core.contracts import ActionId, ActionStatus, MessageId, ProspectId
from sales_core.planner import DEFAULT_DAILY_ACTION_LIMIT
from sales_core.safety import DEFAULT_OPT_OUT_KEYWORDS


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineConfig:
    mode: ApprovalMode
    daily_action_limit: int = DEFAULT_DAILY_ACTION_LIMIT
    max_attempts: int = 3  # failed Brain calls / transient send failures before FAILED
    opt_out_keywords: frozenset[str] = DEFAULT_OPT_OUT_KEYWORDS

    def __post_init__(self) -> None:
        if self.daily_action_limit < 0:
            raise ValueError("daily_action_limit must be >= 0")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        object.__setattr__(
            self, "opt_out_keywords", frozenset(k.strip().casefold() for k in self.opt_out_keywords)
        )


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
