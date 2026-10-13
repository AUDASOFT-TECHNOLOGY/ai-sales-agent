"""What a Business Brain receives and returns. Contains nothing client-specific."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sales_core.contracts._validation import freeze_mapping, require_aware, require_text
from sales_core.contracts.action import SalesAction
from sales_core.contracts.conversation import Conversation, MessageDraft
from sales_core.contracts.enums import SCHEDULABLE_ACTION_TYPES, DecisionType, SalesActionType
from sales_core.contracts.fact import Fact, FactProposal
from sales_core.contracts.ids import ClientId
from sales_core.contracts.lane import LaneRoute
from sales_core.contracts.prospect import Prospect


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientContext:
    """Facts about the client a Brain is acting for, taken from client configuration."""

    client_id: ClientId
    display_name: str
    timezone: str = "UTC"  # IANA name
    settings: Mapping[str, object] = field(default_factory=dict)  # Brain-owned, opaque to the Core

    def __post_init__(self) -> None:
        require_text(self.client_id, "client_id")
        require_text(self.display_name, "display_name")
        object.__setattr__(self, "settings", freeze_mapping(self.settings))


MAX_PROPOSED_FACTS = 20  # per decision; more is a malfunction, not thoroughness


@dataclass(frozen=True, slots=True, kw_only=True)
class BrainRequest:
    client: ClientContext
    prospect: Prospect
    conversation: Conversation
    action: SalesAction  # why the Brain is being asked (first contact, follow-up, reply)
    now: datetime  # injected so decisions are reproducible
    facts: tuple[Fact, ...] = ()  # what is known about the prospect
    lane: LaneRoute | None = None  # the products in scope for this prospect; None: all of them

    def __post_init__(self) -> None:
        require_aware(self.now, "now")
        object.__setattr__(self, "facts", tuple(self.facts))
        client_id = self.client.client_id
        if (
            self.prospect.client_id != client_id
            or self.conversation.client_id != client_id
            or self.action.client_id != client_id
        ):
            raise ValueError("client, prospect, conversation and action must share one client_id")
        prospect_id = self.prospect.prospect_id
        if self.conversation.prospect_id != prospect_id or self.action.prospect_id != prospect_id:
            raise ValueError("prospect, conversation and action must refer to the same prospect")


@dataclass(frozen=True, slots=True, kw_only=True)
class BrainDecision:
    """A Brain's structured answer. The Core decides whether and how to act on it."""

    decision_type: DecisionType
    message: MessageDraft | None = None
    next_follow_up_at: datetime | None = None  # when the next action is due
    next_action_type: SalesActionType | None = None  # follow_up (default), reminder or callback
    new_stage: str | None = None  # client-specific label, stored verbatim on the prospect
    rationale: str = ""  # human-readable reason, written to the audit trail
    metadata: Mapping[str, str] = field(default_factory=dict)
    # What the Brain says it learned. Proposals only: the Core validates each one and records the
    # ones it accepts. A Brain never writes facts.
    proposed_facts: tuple[FactProposal, ...] = ()

    def __post_init__(self) -> None:
        if (self.decision_type is DecisionType.SEND_MESSAGE) != (self.message is not None):
            raise ValueError("a message is required for, and only allowed with, SEND_MESSAGE")
        if self.decision_type is DecisionType.SCHEDULE_FOLLOW_UP and self.next_follow_up_at is None:
            raise ValueError("SCHEDULE_FOLLOW_UP requires next_follow_up_at")
        require_aware(self.next_follow_up_at, "next_follow_up_at")
        if self.next_action_type is not None:
            if self.next_follow_up_at is None:
                raise ValueError("next_action_type requires next_follow_up_at")
            if self.next_action_type not in SCHEDULABLE_ACTION_TYPES:
                raise ValueError(f"{self.next_action_type} cannot be scheduled as a next action")
        elif self.next_follow_up_at is not None:
            object.__setattr__(self, "next_action_type", SalesActionType.FOLLOW_UP)
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        object.__setattr__(self, "proposed_facts", tuple(self.proposed_facts))
        if len(self.proposed_facts) > MAX_PROPOSED_FACTS:
            raise ValueError(f"at most {MAX_PROPOSED_FACTS} facts may be proposed at once")
