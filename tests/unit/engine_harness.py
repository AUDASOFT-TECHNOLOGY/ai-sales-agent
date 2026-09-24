"""Shared scaffolding for the Engine tests: scripted Brain, spy channel, and a builder.

Everything here is generic and client-neutral; the Brain is a stub, never a real client Brain.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from factories import CLIENT, NOW, make_client_context, make_prospect

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionStatus,
    BrainDecision,
    BrainRequest,
    Channel,
    DecisionType,
    InboundMessage,
    Message,
    MessageDraft,
    OutboundMessage,
    Prospect,
    ProspectId,
    ProspectStatus,
    SalesAction,
    SalesActionType,
    Scheduling,
    SendResult,
)
from sales_core.engine import Engine, EngineConfig
from sales_core.ports import FactsReader
from sales_integrations.fakes import FakeClock, FakeMessagingChannel, FakeSalesRepository

FU = SalesActionType.FOLLOW_UP
REM = SalesActionType.REMINDER
CB = SalesActionType.CALLBACK
FIRST = SalesActionType.FIRST_CONTACT
REPLY = SalesActionType.REPLY


class Crash(BaseException):
    """Simulates the process dying (not an ordinary error, so the Engine must not catch it)."""


def phone_for(pid: str) -> str:
    return "+1555" + "".join(c for c in pid if c.isdigit()).zfill(7)


def prospect(pid: str, **overrides: object) -> Prospect:
    """A NEW prospect with a phone number derived from its id (ids must contain digits)."""
    fields: dict[str, object] = {"prospect_id": ProspectId(pid)}
    contact = make_prospect().contact
    fields["contact"] = replace(contact, phone=phone_for(pid))
    return make_prospect(**(fields | overrides))


def due(
    pid: str, kind: SalesActionType = FU, ago: timedelta = timedelta(hours=1), **kw
) -> Prospect:
    """An ACTIVE prospect whose `kind` action became due `ago` before NOW."""
    schedule = Scheduling(next_action_type=kind, next_action_due_at=NOW - ago)
    return prospect(pid, **({"status": ProspectStatus.ACTIVE, "schedule": schedule} | kw))


def send(
    text: str = "Hello",
    *,
    follow_up_in: timedelta | None = None,
    next_type: SalesActionType | None = None,
    stage: str | None = None,
) -> BrainDecision:
    return BrainDecision(
        decision_type=DecisionType.SEND_MESSAGE,
        message=MessageDraft(text=text),
        next_follow_up_at=NOW + follow_up_in if follow_up_in else None,
        next_action_type=next_type,
        new_stage=stage,
        rationale="test",
    )


def schedule(
    after: timedelta, *, next_type: SalesActionType | None = None, stage: str | None = None
) -> BrainDecision:
    return BrainDecision(
        decision_type=DecisionType.SCHEDULE_FOLLOW_UP,
        next_follow_up_at=NOW + after,
        next_action_type=next_type,
        new_stage=stage,
    )


def decide(kind: DecisionType, **kw: object) -> BrainDecision:
    return BrainDecision(decision_type=kind, **kw)  # type: ignore[arg-type]


class ScriptedBrain:
    """Stand-in Brain: records every request and answers via `fn` (default: send a greeting)."""

    def __init__(self, fn: Callable[[BrainRequest], BrainDecision] | None = None) -> None:
        self._fn = fn or (lambda request: send())
        self.calls: list[BrainRequest] = []

    def decide(self, request: BrainRequest) -> BrainDecision:
        self.calls.append(request)
        return self._fn(request)

    def called_for(self) -> list[str]:
        return [c.prospect.prospect_id for c in self.calls]


class SpyChannel(FakeMessagingChannel):
    """Records the state of the action in the repository at the moment of each send."""

    def __init__(self, repo: FakeSalesRepository, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self._repo = repo
        self.status_during_send: list[ActionStatus] = []

    def send(self, message: OutboundMessage) -> SendResult:
        action = self._repo.find_action_by_idempotency_key(message.idempotency_key)
        if action is not None:
            self.status_during_send.append(action.status)
        return super().send(message)


@dataclass
class Harness:
    engine: Engine
    repo: FakeSalesRepository
    channel: SpyChannel
    brain: ScriptedBrain
    clock: FakeClock
    mode: ApprovalMode
    config_kwargs: dict
    facts: FactsReader | None = None

    def new_engine(self, brain: ScriptedBrain | None = None) -> Engine:
        """A fresh Engine over the same repository, as after a process restart."""
        return Engine(
            client=make_client_context(),
            repository=self.repo,
            brain=brain or self.brain,
            channel=self.channel,
            clock=self.clock,
            config=EngineConfig(mode=self.mode, **self.config_kwargs),
            facts=self.facts,
        )

    # ---- inspection -----------------------------------------------------------------------
    def actions_for(self, pid: str) -> list[SalesAction]:
        return [a for a in self.repo.list_actions() if a.prospect_id == pid]

    def action(self, pid: str) -> SalesAction:
        (only,) = self.actions_for(pid)
        return only

    def status(self, pid: str) -> ActionStatus:
        return self.action(pid).status

    def prospect(self, pid: str) -> Prospect:
        found = self.repo.get_prospect(ProspectId(pid))
        assert found is not None
        return found

    def event_types(self) -> list[str]:
        return [e.event_type for e in self.repo.audit_events]

    def events(self, event_type: str):
        return [e for e in self.repo.audit_events if e.event_type == event_type]

    # ---- what a human does in the Sheet ---------------------------------------------------
    def human_edit(self, pid: str, **changes: object) -> None:
        self.repo.save_actions([replace(self.action(pid), **changes)])

    def approve(self, *pids: str) -> None:
        for pid in pids:
            self.human_edit(pid, status=ActionStatus.APPROVED)

    def reject(self, *pids: str) -> None:
        for pid in pids:
            self.human_edit(pid, status=ActionStatus.REJECTED)


def build(
    *,
    prospects: Sequence[Prospect] = (),
    actions: Sequence[SalesAction] = (),
    messages: Sequence[Message] = (),
    brain: ScriptedBrain | None = None,
    mode: ApprovalMode = ApprovalMode.APPROVAL,
    facts: FactsReader | None = None,
    **config: object,
) -> Harness:
    clock = FakeClock(NOW)
    repo = FakeSalesRepository(CLIENT, prospects=prospects, actions=actions, messages=messages)
    channel = SpyChannel(repo, clock=clock)
    scripted = brain or ScriptedBrain()
    harness = Harness(
        engine=None,  # type: ignore[arg-type]
        repo=repo,
        channel=channel,
        brain=scripted,
        clock=clock,
        mode=mode,
        config_kwargs=dict(config),
        facts=facts,
    )
    harness.engine = harness.new_engine()
    return harness


def inbound(
    text: str = "Hi, tell me more",
    *,
    sender: str | None = None,
    pid: str = "p1",
    external_id: str = "wamid.1",
    channel: Channel = Channel.WHATSAPP,
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender=sender or phone_for(pid),
        external_id=external_id,
        text=text,
        timestamp=NOW,
    )
