"""The Engine: orchestrates the sales cycle through ports.

It owns process (state transitions, persistence, approval, safety, idempotency, retries, audit)
and never decides commercial content or strategy: everything commercial comes from the Brain.
See docs/engine-design.md for the flow and the reasoning behind each rule.

Public entry points (all serialised by one lock, so a client has a single writer):

- `prepare()`   Phase A: plan, reserve quota, ask the Brain, apply decisions, approval gate.
- `dispatch()`  Phase B: send APPROVED actions after re-checking safety.
- `receive_inbound()` / `process_inbound()`  Phase C: store a reply fast, then handle it.

Facts a Brain proposes with its decision are validated and recorded here, through the `FactsWriter`
port, before the decision takes effect: a Brain never writes facts itself.

Inbound conversations (Phase C) are outside the daily outbound quota and are asked of the Brain
with `ContactOrigin.INBOUND`. What happens with a sender who is not a prospect is `InboundPolicy`.
"""

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sales_core.approval import ApprovalMode, status_after_proposal
from sales_core.contracts import (
    ActionId,
    ActionPriority,
    ActionStatus,
    BrainDecision,
    BrainRequest,
    Channel,
    ClientContext,
    ContactInfo,
    ContactOrigin,
    Conversation,
    DecisionType,
    Direction,
    Fact,
    InboundMessage,
    Message,
    MessageDraft,
    MessageId,
    OutboundMessage,
    Prospect,
    ProspectId,
    ProspectStatus,
    SalesAction,
    SalesActionType,
    Scheduling,
    SendResult,
)
from sales_core.contracts.audit import AuditEvent
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_core.engine import events as ev
from sales_core.engine.applier import (
    after_send,
    apply_without_message,
    clear_next_action,
    proposal,
    validate_decision,
    with_next_action,
)
from sales_core.engine.errors import (
    ControlUnavailableError,
    ManualSendError,
    ProposalNotApprovableError,
    ProposalNotFoundError,
    describe,
)
from sales_core.engine.models import (
    EngineConfig,
    InboundReceipt,
    InboundStatus,
    ManualSendReceipt,
    PendingProposal,
    ProspectSummary,
    ReleaseOutcome,
    RunCollector,
    RunReport,
    UpcomingActions,
)
from sales_core.engine.routing import ChannelRouter
from sales_core.facts import ProposalRejection, review_proposals
from sales_core.kpi import prospects_contacted
from sales_core.planner import plan_daily_actions
from sales_core.ports import (
    Brain,
    Clock,
    ConversationControl,
    ConversationControlStore,
    FactsReader,
    FactsWriter,
    MessagingChannel,
    SalesRepository,
)
from sales_core.safety import (
    ALLOWED,
    OPT_IN_CHANNELS,
    BlockReason,
    SafetyVerdict,
    awaits_opt_in,
    check_contactable,
    check_ready_to_send,
    has_initiated,
    is_opt_out_message,
    recipient_for,
)

_RELINK_AFTER = timedelta(days=1)  # a number Telegram did not know is not asked again before
UNCONFIRMED = "unconfirmed send"  # prefix of `last_error` on actions stuck in SENDING
_ACTIVE_STATUSES = (ProspectStatus.NEW, ProspectStatus.ACTIVE)
AUTHOR_HUMAN = "human"  # `Message.metadata["author"]` of a message a person wrote
_MANUAL_INFIX = ":manual:"  # in the idempotency key of an action that carries a person's message
_MAX_ERROR_TEXT = 300


def _is_manual(action: SalesAction) -> bool:
    return action.action_type is SalesActionType.REPLY and _MANUAL_INFIX in action.idempotency_key


_REFUSALS = {
    BlockReason.OPTED_OUT: (
        "opted_out",
        "This person asked not to be contacted. Nothing can be sent to them.",
    ),
    BlockReason.NO_CONTACT: ("no_contact", "There is no address for this person on the channel."),
    BlockReason.NOT_LINKED: (
        "not_linked",
        "This person has not started the conversation on this channel yet.",
    ),
    BlockReason.NO_LANE: ("no_channel", "This prospect is not reachable on a channel."),
}


def _refusal_of(reason: str) -> ManualSendError:
    """The error that tells a person why safety stopped their message."""
    for block, (code, sentence) in _REFUSALS.items():
        if block.value == reason:
            return ManualSendError(code, sentence)
    return ManualSendError("send_failed", "The message could not be sent.")


def _created_prefix(day: date) -> str:
    return f"inbound-{day.isoformat()}-"


class Engine:
    """One instance per client. Construct it with that client's collaborators."""

    def __init__(
        self,
        *,
        client: ClientContext,
        repository: SalesRepository,
        brain: Brain,
        channel: MessagingChannel | None = None,
        router: ChannelRouter | None = None,
        clock: Clock,
        config: EngineConfig,
        facts: FactsReader | None = None,
        facts_writer: FactsWriter | None = None,
        control: ConversationControlStore | None = None,
    ) -> None:
        if repository.client_id != client.client_id:
            raise ValueError("repository is bound to a different client than the engine")
        if control is not None and control.client_id != client.client_id:
            raise ValueError("conversation control is bound to a different client than the engine")
        if (channel is None) == (router is None):
            raise ValueError("an engine needs exactly one of `channel` and `router`")
        if facts_writer is not None:
            if facts_writer.client_id != client.client_id:
                raise ValueError("facts writer is bound to a different client than the engine")
            if facts is None:
                # Proposals are checked against what is already known, and a fact nobody reads
                # back would never reach the Brain.
                raise ValueError("a facts writer needs a facts reader")
        self._client = client
        self._repo = repository
        self._brain = brain
        # `channel=` is the client with no lanes: one channel for everyone, as it always was.
        self._router = router if router is not None else ChannelRouter.single(channel)  # type: ignore[arg-type]
        self._clock = clock
        self._config = config
        self._facts = facts  # None: no facts are known, the Brain gets an empty collection
        self._facts_writer = facts_writer  # None: facts a Brain proposes are refused, not lost
        # None: no conversation can be taken over, and nothing is held back for a person.
        self._control = control
        self._manual_actors: dict[str, str] = {}  # who wrote the manual message being sent
        self._lock = threading.RLock()
        self._events: list[AuditEvent] = []  # audit events not yet written
        self._link_attempts: dict[str, datetime] = {}  # Telegram accounts looked for, by prospect

    @property
    def _channel(self) -> MessagingChannel:
        """The client's channel when it has one only (what `Engine(channel=...)` was given)."""
        adapter = self._router.adapter(self._router.any_channel)
        assert adapter is not None
        return adapter

    def prospects_contacted(self) -> int:
        """How many distinct prospects have been sent at least one message (read-only)."""
        with self._lock:
            return prospects_contacted(self._repo.list_actions({ActionStatus.SENT}))

    def prospects_awaiting_opt_in(self) -> tuple[Prospect, ...]:
        """The prospects who could be written to but have not started the conversation yet, on a
        channel where they must (read-only). Empty on any other channel. These are the people a
        client has to invite; none of them is lost or paused meanwhile."""
        with self._lock:
            if not any(c in OPT_IN_CHANNELS for c in self._router.channels):
                return ()
            waiting = []
            for p in self._repo.list_prospects():
                channel = self._router.channel_for(p)
                if (
                    channel in OPT_IN_CHANNELS
                    and p.status in _ACTIVE_STATUSES
                    and (awaits_opt_in(p, channel) or not self._initiated(p, channel))
                ):
                    waiting.append(p)
            return tuple(waiting)

    def list_prospects(self) -> tuple[Prospect, ...]:
        """The client's prospects, by name then id so that pages are stable (read-only)."""
        with self._lock:
            own = (p for p in self._repo.list_prospects() if p.client_id == self._client.client_id)
            return tuple(sorted(own, key=lambda p: (p.display_name.casefold(), p.prospect_id)))

    def summarize_prospects(self) -> ProspectSummary:
        """Counts over the client's prospects, from one read of `list_prospects` (read-only).

        `by_status` holds every status (0 only when there really is none). The upcoming actions are
        those of prospects the Engine may still contact (new or active) with a next action and a
        due date recorded: a paused, opted-out or closed prospect is never contacted, so its
        recorded next action is not an upcoming one."""
        with self._lock:
            found = self.list_prospects()
        by_status = {status.value: 0 for status in ProspectStatus}
        by_type: dict[str, int] = {}
        due: list[datetime] = []
        for p in found:
            by_status[p.status.value] += 1
            if (
                p.status in _ACTIVE_STATUSES
                and p.schedule.next_action_type is not None
                and p.schedule.next_action_due_at is not None
            ):
                kind = p.schedule.next_action_type.value
                by_type[kind] = by_type.get(kind, 0) + 1
                due.append(p.schedule.next_action_due_at)
        return ProspectSummary(
            total=len(found),
            by_status=by_status,
            upcoming=UpcomingActions(
                scheduled=len(due), by_type=by_type, next_due_at=min(due, default=None)
            ),
        )

    def conversation_of(self, prospect_id: ProspectId) -> Conversation | None:
        """The history of one of the client's prospects, oldest message first; None if the client
        has no such prospect (read-only). An empty history is a valid answer, not None."""
        with self._lock:
            prospect = self._repo.get_prospect(prospect_id)
            if prospect is None or prospect.client_id != self._client.client_id:
                return None
            return self._repo.get_conversation(prospect_id)

    @property
    def _dry_run(self) -> bool:
        return self._config.mode is ApprovalMode.DRY_RUN

    # ==== Phase A: prepare ===================================================================

    def prepare(self) -> RunReport:
        """Plan today's actions, reserve the quota, ask the Brain, apply its decisions.

        Failures of the run itself (cannot read or reserve the plan) are raised. Failures of a
        single action are isolated, recorded, and reported in `RunReport.errors`.
        """
        with self._lock:
            run = RunCollector()
            now = self._clock.now()
            prospects = self._assign_lanes(list(self._repo.list_prospects()), run)
            actions = list(self._repo.list_actions())
            explicit = self._router.explicit
            plan = plan_daily_actions(
                client=self._client,
                prospects=prospects,
                existing_actions=actions,
                now=now,
                daily_limit=self._config.daily_action_limit,
                channel=None if explicit else self._router.any_channel,
                route_of=self._router.lane_for if explicit else None,
                held_by_human=self._held_by_human(),
            )
            if plan.actions and not self._dry_run:
                # One batch, BEFORE any Brain call: the quota stays reserved if we crash.
                self._repo.save_actions(list(plan.actions))
                for action in plan.actions:
                    self._emit_for(ev.ACTION_PLANNED, action, type=action.action_type)
                self._flush(run)
            # PLANNED actions left over from an earlier run (crash, failed Brain call) are retried.
            leftovers = (
                [] if self._dry_run else [a for a in actions if a.status is ActionStatus.PLANNED]
            )
            by_id = {p.prospect_id: p for p in prospects}
            for action in (*leftovers, *plan.actions):
                self._guard(
                    run, action, self._prepare_one, action, by_id.get(action.prospect_id), now, run
                )
            self._flush(run)
            return run.report()

    def _channel_of(self, action: SalesAction, prospect: Prospect) -> Channel | None:
        """The channel this action goes out on: the one it was given when planned, if the client
        still has it, else the one of the prospect's lane. None: the prospect is in no lane."""
        if action.channel is not None and self._router.adapter(action.channel) is not None:
            return action.channel
        return self._router.channel_for(prospect)

    def _held_by_human(self) -> frozenset[str]:
        """The prospects a person is in charge of. A store that cannot be read stops the run (it is
        raised): planning blind could contact someone a person is talking to."""
        return self._control.humans() if self._control is not None else frozenset()

    def _is_human(self, prospect_id: ProspectId) -> bool:
        """Whether a person is in charge of this conversation. Any doubt counts as yes: a control
        store that fails never lets an automatic message through."""
        if self._control is None:
            return False
        try:
            found = self._control.get(prospect_id)
        except Exception:  # noqa: BLE001 - fail closed
            return True
        return found is not None and found.is_human

    def _contactable(self, action: SalesAction, prospect: Prospect | None) -> SafetyVerdict:
        if prospect is None:
            return check_contactable(None, self._router.any_channel)
        if self._is_human(prospect.prospect_id):
            return SafetyVerdict(BlockReason.HUMAN_CONTROL)
        channel = self._channel_of(action, prospect)
        if channel is None:
            return SafetyVerdict(BlockReason.NO_LANE)
        verdict = check_contactable(prospect, channel)
        return verdict if not verdict.allowed else self._initiation_verdict(prospect, channel)

    def _wrote_first(self, prospect: Prospect, channel: Channel) -> bool:
        """Whether the person has proved they started the conversation on a channel where they
        must (always True elsewhere). An address on the prospect is never the proof: only an
        inbound message this Engine stored, from that address, is."""
        if channel not in OPT_IN_CHANNELS:
            return True
        address = recipient_for(prospect, channel)
        if address is None:
            return True  # no address: `check_contactable` already says why
        conversation = self._repo.get_conversation(prospect.prospect_id)
        return has_initiated(conversation.messages, channel, address)

    def _initiated(self, prospect: Prospect, channel: Channel) -> bool:
        """Whether the prospect may be written to on `channel`: they wrote first, or (Telegram
        from the client's own account only) the client's account may start the conversation."""
        if channel is Channel.TELEGRAM and self._config.telegram_first_contact:
            return True
        return self._wrote_first(prospect, channel)

    def _initiation_verdict(self, prospect: Prospect, channel: Channel) -> SafetyVerdict:
        return (
            ALLOWED if self._initiated(prospect, channel) else SafetyVerdict(BlockReason.NOT_LINKED)
        )

    def _assign_lanes(self, prospects: list[Prospect], run: RunCollector) -> list[Prospect]:
        """Write down, once, the lane of every prospect that has none yet. A client with no lanes
        is left exactly as it is. A prospect is never moved: only the unattributed are touched, and
        if the write fails the attribution is simply recomputed (it is deterministic) next time."""
        if not self._router.explicit:
            return prospects
        attributed: list[Prospect] = []
        changed: list[Prospect] = []
        for prospect in prospects:
            if prospect.lane_id is None:
                lane = self._router.attribute(prospect)
                if lane is not None:
                    prospect = replace(prospect, lane_id=lane.lane_id)
                    changed.append(prospect)
            attributed.append(prospect)
        if changed and not self._dry_run:
            try:
                self._repo.update_prospects(changed)
            except Exception as error:  # noqa: BLE001 - not fatal: it is recomputed next run
                run.error(
                    f"could not record the lane of {len(changed)} prospects: {describe(error)}"
                )
                return attributed
            for prospect in changed:
                self._emit(
                    ev.PROSPECT_LANE_ASSIGNED,
                    prospect_id=prospect.prospect_id,
                    lane=prospect.lane_id,
                )
        return attributed

    def _prepare_one(
        self, action: SalesAction, prospect: Prospect | None, now: datetime, run: RunCollector
    ) -> None:
        verdict = self._contactable(action, prospect)
        if prospect is None or not verdict.allowed:
            self._block(action, prospect, verdict, run)
            return
        request = self._request_for(action, prospect, now)
        decision = self._brain.decide(request)
        validate_decision(decision, now)
        self._emit_for(
            ev.BRAIN_DECISION,
            action,
            actor="brain",
            decision=decision.decision_type,
            rationale=decision.rationale or None,
            next_action=decision.next_action_type,
            next_action_at=decision.next_follow_up_at,
            stage=decision.new_stage,
        )
        facts = self._review_proposed_facts(action, request, decision)

        if self._dry_run:
            self._emit_for(
                ev.DRYRUN_DECISION,
                action,
                decision=decision.decision_type,
                message=decision.message.text if decision.message else None,
                proposed_facts=len(decision.proposed_facts) or None,
            )
            run.outcome(action.action_id, None, f"dry run: {decision.decision_type}")
            return
        self._record_facts(action, prospect, facts)

        if decision.decision_type is DecisionType.SEND_MESSAGE:
            status = status_after_proposal(self._config.mode)
            self._repo.save_actions([replace(proposal(action, decision), status=status)])
            self._emit_for(ev.ACTION_PROPOSED, action)
            if status is ActionStatus.APPROVED:
                self._emit_for(ev.ACTION_APPROVED, action, actor="core:autonomous")
            run.outcome(action.action_id, status)
            return

        # Decisions that send nothing: write the prospect first, then close the action, so a
        # crash in between leaves the action PLANNED and it is simply retried.
        self._repo.update_prospects([apply_without_message(prospect, decision)])
        self._repo.save_actions([replace(action, status=ActionStatus.COMPLETED, last_error=None)])
        self._emit_for(ev.ACTION_COMPLETED, action, decision=decision.decision_type)
        if decision.decision_type is DecisionType.HANDOFF_TO_HUMAN:
            self._emit_for(ev.PROSPECT_HANDOFF, action, rationale=decision.rationale or None)
            self._await_human(action, decision.rationale or None, run)
        elif decision.decision_type is DecisionType.CLOSE:
            self._emit_for(ev.PROSPECT_CLOSED, action, rationale=decision.rationale or None)
        run.outcome(action.action_id, ActionStatus.COMPLETED, str(decision.decision_type))

    def _await_human(self, action: SalesAction, reason: str | None, run: RunCollector) -> None:
        """The Brain handed the conversation over: it now waits, visibly, for a person. The prospect
        is already paused, so a failure here only costs the visibility and is reported."""
        if self._control is None:
            return
        try:
            self._control.await_human(action.prospect_id, reason=reason, at=self._clock.now())
        except Exception as error:  # noqa: BLE001
            run.error(f"{action.action_id}: could not record the handover: {describe(error)}")
            return
        self._emit_for(ev.CONVERSATION_AWAITING_HUMAN, action, rationale=reason)

    def _review_proposed_facts(
        self, action: SalesAction, request: BrainRequest, decision: BrainDecision
    ) -> tuple[Fact, ...]:
        """The facts the Core accepts from a Brain's proposals. Refusals are audited, never raised:
        a bad proposal costs the Brain that fact, not the whole decision.

        The record is dated by the action (persisted before the Brain is asked, so a retry has the
        same date) and signed with its id: the same result processed twice is the same observation.
        """
        proposals = decision.proposed_facts
        if not proposals:
            return ()
        if self._facts_writer is None:
            for proposal in proposals:
                self._emit_for(
                    ev.FACT_PROPOSAL_REJECTED,
                    action,
                    actor="brain",
                    key=proposal.key,
                    reason=ProposalRejection.NO_WRITER,
                )
            return ()
        review = review_proposals(
            proposals,
            client_id=self._client.client_id,
            prospect=request.prospect,
            conversation=request.conversation,
            existing=request.facts,
            recorded_at=action.due_at,
            recorded_by=f"brain-proposal:{action.action_id}",
        )
        for rejected in review.rejected:
            self._emit_for(
                ev.FACT_PROPOSAL_REJECTED,
                action,
                actor="brain",
                key=rejected.proposal.key,
                reason=rejected.reason,
            )
        return review.accepted

    def _record_facts(
        self, action: SalesAction, prospect: Prospect, facts: tuple[Fact, ...]
    ) -> None:
        """Persist accepted facts through the writer before the decision takes effect. If this
        fails the action fails with it and is retried: nothing of the decision has been applied."""
        if not facts or self._facts_writer is None:
            return
        self._facts_writer.append_facts(prospect.prospect_id, facts)
        for fact in facts:
            evidence = fact.evidence
            self._emit_for(
                ev.FACT_PROPOSAL_ACCEPTED,
                action,
                actor="brain",
                key=fact.key,
                status=fact.status,
                source=evidence.source if evidence else None,
            )

    def _request_for(self, action: SalesAction, prospect: Prospect, now: datetime) -> BrainRequest:
        conversation = self._repo.get_conversation(action.prospect_id)
        origin = self._origin_of(action, conversation)
        facts = tuple(self._facts.get_facts(prospect.prospect_id)) if self._facts else ()
        return BrainRequest(
            client=self._client,
            prospect=prospect,
            conversation=replace(conversation, origin=origin),
            action=action,
            now=now,
            facts=facts,
            lane=self._router.lane_for(prospect),
        )

    @staticmethod
    def _origin_of(action: SalesAction, conversation: Conversation) -> ContactOrigin:
        """Who started the conversation, from what the Engine itself knows, never from content.

        A REPLY is the Engine handling an inbound message. A FIRST_CONTACT is the agent writing
        first. Other outreach keeps whatever the repository says: nothing persists the origin yet,
        so that is UNKNOWN, which is honest for a conversation someone else may have started.
        """
        if action.action_type is SalesActionType.REPLY:
            return ContactOrigin.INBOUND
        if conversation.origin is not ContactOrigin.UNKNOWN:
            return conversation.origin
        if action.action_type is SalesActionType.FIRST_CONTACT:
            return ContactOrigin.OUTBOUND
        return ContactOrigin.UNKNOWN

    # ==== Approval by a person ================================================================

    def pending_proposals(self) -> tuple[PendingProposal, ...]:
        """The proposals awaiting a person's decision, oldest first (read-only)."""
        with self._lock:
            proposed = sorted(
                (
                    a
                    for a in self._repo.list_actions({ActionStatus.PROPOSED})
                    if a.draft is not None
                ),
                key=lambda a: (a.due_at, a.action_id),
            )
            return tuple(self._pending(a) for a in proposed)

    def _pending(self, action: SalesAction) -> PendingProposal:
        assert action.draft is not None
        prospect = self._repo.get_prospect(action.prospect_id)
        return PendingProposal(
            action_id=action.action_id,
            prospect_id=action.prospect_id,
            prospect_name=prospect.display_name if prospect is not None else None,
            text=action.draft.text,
            language=action.draft.language,
            due_at=action.due_at,
        )

    def approve_proposal(self, action_id: ActionId, *, actor: str) -> None:
        """A person approves a proposal: it becomes APPROVED and `dispatch()` sends it, after the
        safety checks it always passes. Refused if the person can no longer be contacted."""
        with self._lock:
            action = self._proposed(action_id)
            prospect = self._repo.get_prospect(action.prospect_id)
            verdict = self._contactable(action, prospect)
            if prospect is None or not verdict.allowed:
                raise ProposalNotApprovableError("this prospect can no longer be contacted")
            self._repo.save_actions([replace(action, status=ActionStatus.APPROVED)])
            self._emit_for(ev.ACTION_APPROVED, action, actor=actor)
            self._flush(RunCollector())

    def reject_proposal(self, action_id: ActionId, *, actor: str) -> None:
        """A person rejects a proposal: nothing is sent, and the prospect can be planned again."""
        with self._lock:
            action = self._proposed(action_id)
            self._repo.save_actions([replace(action, status=ActionStatus.REJECTED)])
            self._emit_for(ev.ACTION_REJECTED, action, actor=actor)
            self._flush(RunCollector())

    # ==== Work that needs the Engine's collaborators and its single writer ===================

    def exclusive[T](
        self,
        work: Callable[[SalesRepository, FactsReader | None, FactsWriter | None, Clock], T],
    ) -> T:
        """Runs `work` with this client's repository, facts and clock while holding the Engine's lock,
        so that whatever it records has the same single writer as the Engine's own work. For surfaces
        that act for an authenticated person, never for a Brain."""
        with self._lock:
            return work(self._repo, self._facts, self._facts_writer, self._clock)

    def _proposed(self, action_id: ActionId) -> SalesAction:
        """The action as it is stored now, only if it still awaits a decision."""
        for action in self._repo.list_actions({ActionStatus.PROPOSED}):
            if action.action_id == action_id and action.draft is not None:
                return action
        raise ProposalNotFoundError(action_id)

    # ==== Phase B: dispatch ==================================================================

    def dispatch(self) -> RunReport:
        """Send APPROVED actions. Never resends an action stuck in SENDING."""
        with self._lock:
            run = RunCollector()
            if self._dry_run:
                return run.report()
            self._report_unconfirmed(run)
            approved = sorted(
                self._repo.list_actions({ActionStatus.APPROVED}),
                key=lambda a: (-a.priority, a.due_at, a.action_id),
            )
            for action in approved:
                self._guard(run, action, self._dispatch_one, action, run)
            self._flush(run)
            return run.report()

    def run_cycle(self) -> RunReport:
        """Convenience: `prepare()` then `dispatch()` (autonomous mode sends in one pass)."""
        with self._lock:
            first, second = self.prepare(), self.dispatch()
            return RunReport(
                outcomes=first.outcomes + second.outcomes, errors=first.errors + second.errors
            )

    def _dispatch_one(
        self, action: SalesAction, run: RunCollector, *, manual: bool = False
    ) -> None:
        """Send one APPROVED action. `manual` is a person's own message (`send_manual`): the human
        gate does not apply to it (it is what the gate leaves room for), a paused prospect is
        written to, and everything else (opt-out, address, channel, the first-contact rule) does."""
        prospect = self._repo.get_prospect(action.prospect_id)  # fresh state, not a snapshot
        if not manual and prospect is not None and self._is_human(prospect.prospect_id):
            self._block(action, prospect, SafetyVerdict(BlockReason.HUMAN_CONTROL), run)
            return
        channel = self._channel_of(action, prospect) if prospect is not None else None
        adapter = self._router.adapter(channel) if channel is not None else None
        if prospect is not None and adapter is None:
            self._block(action, prospect, SafetyVerdict(BlockReason.NO_LANE), run)
            return
        channel = channel or self._router.any_channel
        verdict = (
            self._manual_verdict(prospect, action, channel)
            if manual
            else check_ready_to_send(prospect, action, channel)
        )
        if verdict.allowed and prospect is not None:
            verdict = self._initiation_verdict(prospect, channel)  # final gate, fresh history
        recipient = recipient_for(prospect, channel) if prospect is not None else None
        if not verdict.allowed or prospect is None or recipient is None or action.draft is None:
            blocked = verdict if not verdict.allowed else SafetyVerdict(BlockReason.NO_CONTACT)
            self._block(action, prospect, blocked, run)
            return

        outbound = OutboundMessage(
            client_id=action.client_id,
            prospect_id=action.prospect_id,
            channel=channel,
            recipient=recipient,
            draft=action.draft,
            idempotency_key=action.idempotency_key,
            first_contact=not self._wrote_first(prospect, channel),
        )
        sending = replace(action, status=ActionStatus.SENDING)
        # Persist SENDING before the channel call. If this write fails we raise: nothing was sent.
        self._repo.save_actions([sending])
        try:
            result = adapter.send(outbound)  # type: ignore[union-attr]  # checked above
        except Exception as error:  # noqa: BLE001 - outcome unknown, must fail closed
            self._mark_unconfirmed(sending, f"channel raised {describe(error)}", run)
            return
        if result.ok:
            self._finish_sent(sending, prospect, result, run)
        else:
            self._finish_failed(action, sending, prospect, result, run)

    def _finish_sent(
        self, sending: SalesAction, prospect: Prospect, result: SendResult, run: RunCollector
    ) -> None:
        sent = replace(sending, status=ActionStatus.SENT, last_error=None)
        recorded = True
        try:
            self._repo.save_actions([sent])
        except Exception as error:  # noqa: BLE001
            recorded = False
            run.error(
                f"{sent.action_id}: message accepted (external id {result.external_id}) but SENT "
                f"could not be recorded, action stays SENDING: {describe(error)}"
            )
            self._emit_for(
                ev.ACTION_SEND_UNCONFIRMED,
                sent,
                external_id=result.external_id,
                error=describe(error),
            )
        # Bookkeeping: each step is independent so one failure cannot block the others.
        self._step(
            run,
            sent,
            "append_message",
            self._record_outbound,
            sent,
            result,
            self._channel_of(sent, prospect) or self._router.any_channel,
        )
        self._step(
            run,
            sent,
            "update_prospect",
            lambda: self._repo.update_prospects([after_send(prospect, sent, result.timestamp)]),
        )
        self._emit_for(ev.ACTION_SENT, sent, external_id=result.external_id)
        run.outcome(sent.action_id, ActionStatus.SENT if recorded else ActionStatus.SENDING)

    def _record_outbound(self, action: SalesAction, result: SendResult, channel: Channel) -> None:
        draft = action.draft
        if draft is None:
            raise ValueError("cannot record a message without a draft")
        conversation = self._repo.get_conversation(action.prospect_id)
        metadata = {"action_id": action.action_id}
        if _is_manual(action):
            metadata["author"] = AUTHOR_HUMAN
            actor = self._manual_actors.get(action.action_id)
            if actor:
                metadata["actor"] = actor
        if draft.template_id:
            metadata["template_id"] = draft.template_id
        self._repo.append_messages(
            [
                Message(
                    message_id=MessageId(f"out:{action.action_id}"),  # replay-safe: one per action
                    client_id=action.client_id,
                    prospect_id=action.prospect_id,
                    conversation_id=conversation.conversation_id,
                    direction=Direction.OUTBOUND,
                    channel=channel,
                    text=draft.text,
                    timestamp=result.timestamp,
                    external_id=result.external_id,
                    metadata=metadata,
                )
            ]
        )

    def _finish_failed(
        self,
        action: SalesAction,
        sending: SalesAction,
        prospect: Prospect,
        result: SendResult,
        run: RunCollector,
    ) -> None:
        detail = result.error_message or str(result.status)
        self._emit_for(
            ev.ACTION_SEND_FAILED,
            action,
            status=result.status,
            code=result.error_code,
            error=detail,
        )
        # A refusal that sent nothing and is not the message's fault (an allowance used up): the
        # action stays APPROVED with the reason visible, WITHOUT using one of its attempts, and is
        # offered again on the next dispatch (once per dispatch: no tight loop).
        deferred = result.retryable and result.metadata.get(DEFERRED_KEY) == DEFERRED_VALUE
        attempts = action.attempts if deferred else action.attempts + 1
        retry = deferred or (result.retryable and attempts < self._config.max_attempts)
        status = ActionStatus.APPROVED if retry else ActionStatus.FAILED
        try:
            self._repo.save_actions(
                [replace(sending, status=status, attempts=attempts, last_error=detail)]
            )
        except Exception as error:  # noqa: BLE001
            # The send definitely failed but we cannot record it: leave SENDING for a human.
            run.error(
                f"{action.action_id}: send failed but could not be recorded: {describe(error)}"
            )
            self._emit_for(ev.ACTION_SEND_UNCONFIRMED, action, error=describe(error))
            return
        if not result.retryable and prospect.status in _ACTIVE_STATUSES:
            # Permanent failure (e.g. invalid number): a human must fix the contact data.
            # Only the lifecycle status changes; stage and schedule are left untouched.
            self._step(
                run,
                action,
                "pause_prospect",
                lambda: self._repo.update_prospects(
                    [replace(prospect, status=ProspectStatus.PAUSED)]
                ),
            )
            self._emit_for(ev.PROSPECT_PAUSED, action, reason="permanent send failure")
        run.outcome(action.action_id, status, detail)

    def _mark_unconfirmed(self, sending: SalesAction, reason: str, run: RunCollector) -> None:
        detail = f"{UNCONFIRMED}: {reason}; a human must reconcile"
        run.error(f"{sending.action_id}: {detail}")
        self._emit_for(ev.ACTION_SEND_UNCONFIRMED, sending, reason=reason)
        try:
            self._repo.save_actions([replace(sending, last_error=detail)])
        except Exception as error:  # noqa: BLE001
            run.error(
                f"{sending.action_id}: could not record the unconfirmed send: {describe(error)}"
            )
        run.outcome(sending.action_id, ActionStatus.SENDING, detail)

    def _report_unconfirmed(self, run: RunCollector) -> None:
        """Actions found in SENDING are never resent. They are surfaced on every dispatch."""
        for action in self._repo.list_actions({ActionStatus.SENDING}):
            first_time = not (action.last_error or "").startswith(UNCONFIRMED)
            detail = f"{UNCONFIRMED}: found in SENDING; a human must reconcile"
            run.error(f"{action.action_id}: {detail}")
            run.outcome(action.action_id, ActionStatus.SENDING, detail)
            if first_time:
                self._emit_for(ev.ACTION_SEND_UNCONFIRMED, action)
                try:
                    self._repo.save_actions([replace(action, last_error=detail)])
                except Exception as error:  # noqa: BLE001
                    run.error(f"{action.action_id}: could not flag the action: {describe(error)}")

    # ==== Phase C: inbound replies ===========================================================

    def receive_inbound(self, inbound: InboundMessage) -> InboundReceipt:
        """Fast webhook path: identify the sender, de-duplicate, persist. Calls no Brain.

        Persistence failures are raised so the webhook can answer with an error and the provider
        redelivers. A redelivery is detected and repairs any half-written state.
        """
        with self._lock:
            run = RunCollector()
            receipt = self._receive(inbound)
            self._flush(run)
            return replace(receipt, errors=tuple(run.errors))

    def _receive(self, inbound: InboundMessage) -> InboundReceipt:
        if self._dry_run:
            self._emit(ev.DRYRUN_INBOUND, sender=inbound.sender, external_id=inbound.external_id)
            return InboundReceipt(status=InboundStatus.DRY_RUN)
        prospect = (
            self._find_sender(inbound)
            or self._link_sender(inbound)
            or self._engage_unknown_sender(inbound)
        )
        if prospect is None:
            return InboundReceipt(status=InboundStatus.UNKNOWN_SENDER)

        message_id = MessageId(f"in:{inbound.channel}:{inbound.external_id}")
        conversation = self._repo.get_conversation(prospect.prospect_id)
        stored = next((m for m in conversation.messages if m.message_id == message_id), None)
        if stored is not None:
            self._touch_last_inbound(prospect, stored.timestamp)  # repair an interrupted receive
            self._emit(
                ev.INBOUND_DUPLICATE, prospect_id=prospect.prospect_id, message_id=message_id
            )
            return InboundReceipt(
                status=InboundStatus.DUPLICATE,
                message_id=message_id,
                prospect_id=prospect.prospect_id,
            )
        self._repo.append_messages(
            [
                Message(
                    message_id=message_id,
                    client_id=self._client.client_id,
                    prospect_id=prospect.prospect_id,
                    conversation_id=conversation.conversation_id,
                    direction=Direction.INBOUND,
                    channel=inbound.channel,
                    text=inbound.text,
                    timestamp=inbound.timestamp,
                    external_id=inbound.external_id,
                    metadata=dict(inbound.metadata),
                )
            ]
        )
        self._touch_last_inbound(prospect, inbound.timestamp)
        self._emit(ev.INBOUND_RECEIVED, prospect_id=prospect.prospect_id, message_id=message_id)
        return InboundReceipt(
            status=InboundStatus.STORED, message_id=message_id, prospect_id=prospect.prospect_id
        )

    def _engage_unknown_sender(self, inbound: InboundMessage) -> Prospect | None:
        """A message from someone who is not a prospect: ignore it, or create the prospect.

        Nothing is inferred: the new prospect carries only the address, the channel and the time
        of the message. It is ACTIVE from the start (the conversation has begun), so the planner,
        which only opens NEW prospects, never sends it outreach nor spends the daily quota on it.
        """
        policy = self._config.inbound
        today = self._clock.now().astimezone(ZoneInfo(self._client.timezone)).date()
        refusal = None
        if not policy.engages_unknown_senders:
            refusal = {
                "policy": policy.unknown_sender if policy.spontaneous_conversations else "disabled"
            }
        elif self._created_on(today) >= policy.max_new_per_day:
            refusal = {
                "policy": policy.unknown_sender,
                "reason": "max_new_per_day reached",
                "max_new_per_day": policy.max_new_per_day,
            }
        if refusal is not None:
            self._emit(
                ev.INBOUND_UNKNOWN_SENDER,
                sender=inbound.sender,
                external_id=inbound.external_id,
                text=inbound.text,
                **refusal,
            )
            return None
        # A stranger is in the lane that is written to on the channel they used (none if no lanes).
        lane = self._router.lane_for_inbound(inbound.channel) if self._router.explicit else None
        by_email = inbound.channel is Channel.EMAIL
        by_telegram = inbound.channel is Channel.TELEGRAM
        address = inbound.sender.strip()
        digits = "".join(c for c in address if c.isdigit())
        if by_telegram:
            key = f"tg{address.casefold()}"  # a chat id is not a phone number
        else:
            key = address.casefold() if by_email or not digits else digits
        by_phone = not by_email and not by_telegram
        name = inbound.metadata.get("sender_name", "").strip() if by_telegram else ""
        prospect = Prospect(
            client_id=self._client.client_id,
            # The local day is part of the id: that is what `_created_on` counts, and it survives
            # restarts. A redelivery never reaches here again: the prospect is found by contact.
            prospect_id=ProspectId(f"{_created_prefix(today)}{key}"),
            display_name=name or address,
            contact=ContactInfo(
                email=address if by_email else None,
                phone=address if by_phone else None,
                telegram_chat_id=address if by_telegram else None,
                preferred_channel=inbound.channel,
            ),
            status=ProspectStatus.ACTIVE,
            schedule=Scheduling(last_inbound_at=inbound.timestamp),
            lane_id=lane.lane_id if lane is not None else None,
        )
        self._repo.create_prospects([prospect])
        self._emit(
            ev.PROSPECT_CREATED,
            prospect_id=prospect.prospect_id,
            sender=inbound.sender,
            external_id=inbound.external_id,
            origin=ContactOrigin.INBOUND,
        )
        return prospect

    def _created_on(self, day: date) -> int:
        """Prospects this Engine created from unknown senders on the client's local `day`."""
        prefix = _created_prefix(day)
        return sum(1 for p in self._repo.list_prospects() if p.prospect_id.startswith(prefix))

    def knows_inbound_sender(
        self, channel: Channel, sender: str, shared_phone: str | None = None
    ) -> bool:
        """Whether `sender` is one of this client's prospects on `channel`.

        A question, nothing more: it stores nothing and emits no event. It is for a reader that must
        not let anyone else's message through (a person's own mailbox): the answer does not depend on
        the policy for unknown senders, because a stranger is never to be engaged from there."""
        with self._lock:
            if self._find_prospect(channel, sender) is not None:
                return True
            # A Telegram account that is not linked yet may still be a prospect we hold the number of
            # (the number is the one Telegram itself reports for the sender).
            return (
                channel in OPT_IN_CHANNELS
                and bool(shared_phone)
                and self._repo.find_prospect_by_contact(phone=shared_phone) is not None
            )

    def _find_sender(self, inbound: InboundMessage) -> Prospect | None:
        return self._find_prospect(inbound.channel, inbound.sender)

    def _find_prospect(self, channel: Channel, sender: str) -> Prospect | None:
        if channel is Channel.EMAIL:
            return self._repo.find_prospect_by_contact(email=sender)
        if channel is Channel.TELEGRAM:
            return self._repo.find_prospect_by_contact(telegram_chat_id=sender)
        return self._repo.find_prospect_by_contact(phone=sender)

    def _link_sender(self, inbound: InboundMessage) -> Prospect | None:
        """Attach a sender we cannot find to the prospect they were invited as, when the adapter
        has authenticated who they are (an invitation only we could issue, or a number the
        platform confirmed is theirs).

        Only on a channel where the person must speak first, and only if that prospect is not
        already attached to someone else: a forwarded invitation must never take over a
        conversation, nor can a second person replace the first.
        """
        if inbound.channel not in OPT_IN_CHANNELS:
            return None
        claimed: Prospect | None = None
        via = ""
        if inbound.linked_prospect_id:
            via, claimed = (
                "invitation",
                self._repo.get_prospect(ProspectId(inbound.linked_prospect_id)),
            )
        elif inbound.shared_phone:
            via, claimed = "phone", self._repo.find_prospect_by_contact(phone=inbound.shared_phone)
        if claimed is None:
            if via:
                self._emit(ev.INBOUND_LINK_REFUSED, via=via, reason="no such prospect")
            return None
        if claimed.contact.telegram_chat_id:
            self._emit(
                ev.INBOUND_LINK_REFUSED,
                prospect_id=claimed.prospect_id,
                via=via,
                reason="already linked",
            )
            return None
        linked = replace(
            claimed, contact=replace(claimed.contact, telegram_chat_id=inbound.sender.strip())
        )
        self._repo.update_prospects([linked])
        self._emit(ev.PROSPECT_LINKED, prospect_id=claimed.prospect_id, via=via)
        return linked

    def link_telegram_by_phone(
        self, resolve: Callable[[str, str], str | None], *, limit: int
    ) -> int:
        """Find the Telegram account of the prospects the client's account may write to first.

        `resolve(phone, display_name)` asks Telegram who holds a number and returns the account id,
        or None. Only prospects that are active, on a Telegram lane, have a phone and no Telegram account
        yet are tried, at most `limit` per call, and one that
        could not be found is not tried again for a day. Returns how many were linked."""
        if not self._config.telegram_first_contact or limit < 1:
            return 0
        with self._lock:
            now = self._clock.now()
            linked = 0
            tried = 0
            for p in self._repo.list_prospects():
                if tried >= limit:
                    break
                if (
                    p.client_id != self._client.client_id
                    or p.status not in _ACTIVE_STATUSES
                    or p.contact.telegram_chat_id
                    or not (p.contact.phone or "").strip()
                    or self._router.channel_for(p) is not Channel.TELEGRAM
                ):
                    continue
                last = self._link_attempts.get(p.prospect_id)
                if last is not None and now - last < _RELINK_AFTER:
                    continue
                self._link_attempts[p.prospect_id] = now
                tried += 1
                account = resolve(p.contact.phone, p.display_name)  # type: ignore[arg-type]
                if not account:
                    self._emit(
                        ev.INBOUND_LINK_REFUSED,
                        prospect_id=p.prospect_id,
                        via="phone",
                        reason="no account",
                    )
                    continue
                if self._repo.find_prospect_by_contact(telegram_chat_id=account) is not None:
                    self._emit(
                        ev.INBOUND_LINK_REFUSED,
                        prospect_id=p.prospect_id,
                        via="phone",
                        reason="already linked",
                    )
                    continue
                self._repo.update_prospects(
                    [replace(p, contact=replace(p.contact, telegram_chat_id=account))]
                )
                self._emit(ev.PROSPECT_LINKED, prospect_id=p.prospect_id, via="consent")
                linked += 1
            run = RunCollector()
            self._flush(run)
            return linked

    def _touch_last_inbound(self, prospect: Prospect, at: datetime) -> None:
        current = prospect.schedule.last_inbound_at
        if current is None or current < at:
            schedule = replace(prospect.schedule, last_inbound_at=at)
            self._repo.update_prospects([replace(prospect, schedule=schedule)])

    def process_inbound(self, receipt: InboundReceipt) -> RunReport:
        """Handle a stored reply. Safe to call repeatedly: the REPLY action key is idempotent.

        A reply never uses the daily outreach quota: the planner ignores REPLY actions.
        """
        with self._lock:
            run = RunCollector()
            skip = self._dry_run or receipt.status in (
                InboundStatus.UNKNOWN_SENDER,
                InboundStatus.DRY_RUN,
            )
            if not skip and receipt.prospect_id is not None and receipt.message_id is not None:
                self._process_reply_safely(receipt.prospect_id, receipt.message_id, run)
            self._flush(run)
            return run.report()

    def recover_pending_replies(self) -> RunReport:
        """Process stored inbound messages that never got a REPLY action (e.g. after a crash).

        Meant to run at the start of each daily cycle. Only looks at prospects whose latest
        inbound message is newer than our last outbound one.
        """
        with self._lock:
            run = RunCollector()
            if self._dry_run:
                return run.report()
            for prospect in self._repo.list_prospects():
                schedule = prospect.schedule
                last_in, last_out = schedule.last_inbound_at, schedule.last_contacted_at
                if last_in is None or (last_out is not None and last_in <= last_out):
                    continue
                try:
                    conversation = self._repo.get_conversation(prospect.prospect_id)
                    for message in conversation.messages:
                        if message.direction is not Direction.INBOUND:
                            continue
                        if last_out is not None and message.timestamp <= last_out:
                            continue
                        key = self._reply_key(prospect.prospect_id, message.message_id)
                        if self._repo.find_action_by_idempotency_key(key) is None:
                            self._process_reply_safely(
                                prospect.prospect_id, message.message_id, run
                            )
                except Exception as error:  # noqa: BLE001
                    run.error(f"{prospect.prospect_id}: recovery failed: {describe(error)}")
                    self._emit(
                        ev.INBOUND_ERROR, prospect_id=prospect.prospect_id, error=describe(error)
                    )
                self._flush(run)
            return run.report()

    def _process_reply_safely(
        self, prospect_id: ProspectId, message_id: MessageId, run: RunCollector
    ) -> None:
        try:
            self._process_reply(prospect_id, message_id, run)
        except Exception as error:  # noqa: BLE001 - recorded; recover_pending_replies retries
            run.error(f"{prospect_id}/{message_id}: {describe(error)}")
            self._emit(
                ev.INBOUND_ERROR,
                prospect_id=prospect_id,
                message_id=message_id,
                error=describe(error),
            )

    def _process_reply(
        self, prospect_id: ProspectId, message_id: MessageId, run: RunCollector
    ) -> None:
        prospect = self._repo.get_prospect(prospect_id)
        if prospect is None:
            raise LookupError(f"unknown prospect {prospect_id!r}")
        conversation = self._repo.get_conversation(prospect_id)
        message = next((m for m in conversation.messages if m.message_id == message_id), None)
        if message is None or message.direction is not Direction.INBOUND:
            raise LookupError(f"no stored inbound message {message_id!r}")

        key = self._reply_key(prospect_id, message_id)
        action = self._repo.find_action_by_idempotency_key(key)
        if action is not None and action.status is not ActionStatus.PLANNED:
            run.outcome(action.action_id, action.status, "reply already handled")
            return
        now = self._clock.now()
        if action is None:
            action = SalesAction(
                action_id=ActionId(key),
                client_id=self._client.client_id,
                prospect_id=prospect_id,
                action_type=SalesActionType.REPLY,
                due_at=now,
                idempotency_key=key,
                priority=ActionPriority.HIGH,
            )

        if self._router.explicit and action.channel is None:
            # Answered where the person wrote if the client has that channel, else on their lane's.
            lane = self._router.lane_for(prospect)
            if lane is not None:
                chosen = message.channel if self._router.adapter(message.channel) else lane.channel
                action = replace(action, lane_id=lane.lane_id, channel=chosen)

        if is_opt_out_message(message.text, self._config.opt_out_keywords):
            # Before the human gate, so a stop request is honoured whoever is in charge.
            self._handle_opt_out(prospect, action, run)
            return
        if self._is_human(prospect_id):
            # A person is in charge: the message stays in the history for them, the agent answers
            # nothing, and the reply is settled so it is not picked up again when the
            # conversation is given back (the person has answered it, or chose not to).
            self._repo.save_actions(
                [replace(action, status=ActionStatus.COMPLETED, last_error=None)]
            )
            self._emit_for(ev.ACTION_COMPLETED, action, reason="human control")
            run.outcome(action.action_id, ActionStatus.COMPLETED, "human control")
            return
        verdict = self._contactable(action, prospect)
        if not verdict.allowed:
            self._block(action, prospect, verdict, run)  # the message stays in the history
            return

        self._supersede_open_actions(prospect_id, keep=action.action_id, run=run)
        status = ProspectStatus.ACTIVE if prospect.status is ProspectStatus.NEW else prospect.status
        prospect = replace(clear_next_action(prospect), status=status)  # the Brain sets what's next
        self._repo.update_prospects([prospect])
        self._repo.save_actions([action])
        self._guard(run, action, self._prepare_one, action, prospect, now, run)
        saved = self._repo.find_action_by_idempotency_key(key)
        if saved is not None and saved.status is ActionStatus.APPROVED:  # autonomous mode
            self._guard(run, saved, self._dispatch_one, saved, run)

    def _handle_opt_out(self, prospect: Prospect, action: SalesAction, run: RunCollector) -> None:
        if prospect.status is not ProspectStatus.OPTED_OUT:
            optedout = replace(clear_next_action(prospect), status=ProspectStatus.OPTED_OUT)
            self._repo.update_prospects([optedout])
            self._emit_for(ev.PROSPECT_OPTED_OUT, action)
        self._supersede_open_actions(prospect.prospect_id, keep=action.action_id, run=run)
        self._repo.save_actions([replace(action, status=ActionStatus.COMPLETED, last_error=None)])
        self._emit_for(ev.ACTION_COMPLETED, action, reason="opt-out")
        run.outcome(action.action_id, ActionStatus.COMPLETED, "opt-out")

    def _supersede_open_actions(
        self,
        prospect_id: ProspectId,
        *,
        keep: ActionId | None,
        run: RunCollector,
        reason: str = "superseded by inbound reply",
    ) -> None:
        """Cancel every unsent action for the prospect: the reply makes them stale (or a person
        took the conversation over)."""
        open_statuses = {ActionStatus.PLANNED, ActionStatus.PROPOSED, ActionStatus.APPROVED}
        stale = [
            a
            for a in self._repo.list_actions(open_statuses)
            if a.prospect_id == prospect_id and a.action_id != keep
        ]
        if not stale:
            return
        self._repo.save_actions(
            [replace(a, status=ActionStatus.CANCELLED, last_error=reason) for a in stale]
        )
        for action in stale:
            self._emit_for(ev.ACTION_CANCELLED, action, reason=reason)
            run.outcome(action.action_id, ActionStatus.CANCELLED, reason)

    def _reply_key(self, prospect_id: ProspectId, message_id: MessageId) -> str:
        return f"{self._client.client_id}:{prospect_id}:reply:{message_id}"

    # ==== A person in charge of a conversation ===============================================

    def control_of(self, prospect_id: ProspectId) -> ConversationControl | None:
        """Who is in charge of this conversation (None: the agent, always). Read-only."""
        if self._control is None:
            return None
        with self._lock:
            return self._control.get(prospect_id)

    def take_over(
        self, prospect_id: ProspectId, *, by: str, reason: str | None = None
    ) -> ConversationControl:
        """A person takes the conversation. From now on nothing automatic goes to this prospect:
        their open actions are cancelled, no follow-up is planned, and the prospect is paused so it
        shows as waiting for a person. Their schedule is kept; `release` decides what resumes."""
        with self._lock:
            store = self._require_control()
            prospect = self._known_prospect(prospect_id)
            now = self._clock.now()
            control = store.take(prospect_id, by=by, reason=reason, at=now)
            run = RunCollector()
            self._supersede_open_actions(prospect_id, keep=None, run=run, reason="taken over")
            if prospect.status in _ACTIVE_STATUSES:
                self._repo.update_prospects([replace(prospect, status=ProspectStatus.PAUSED)])
                self._emit(ev.PROSPECT_PAUSED, prospect_id=prospect_id, reason="taken over")
            self._emit(ev.CONVERSATION_TAKEN_OVER, actor=by, prospect_id=prospect_id)
            self._flush(run)
            return control

    def release(
        self,
        prospect_id: ProspectId,
        *,
        by: str,
        resume_after: timedelta | None = None,
    ) -> ReleaseOutcome:
        """Give the conversation back to the agent.

        What resumes, by the same rules as everywhere else: a prospect who opted out or was closed
        stays so (only a paused one is made contactable again); the follow-up count starts over,
        since a person spoke to them; and if nothing is due ahead, one follow-up is planned
        `resume_after` from now (None: none is, the agent waits for the prospect). A stop request is
        never undone by giving a conversation back."""
        with self._lock:
            store = self._require_control()
            prospect = self._known_prospect(prospect_id)
            now = self._clock.now()
            control = store.release(prospect_id, at=now)
            if control is None:
                return ReleaseOutcome(
                    released=False,
                    status=prospect.status.value,
                    next_follow_up_at=prospect.schedule.next_action_due_at,
                )
            updated = prospect
            if prospect.status is ProspectStatus.PAUSED:
                schedule = prospect.schedule
                in_touch = (
                    schedule.last_contacted_at is not None
                    or schedule.last_inbound_at is not None
                    or schedule.next_action_type is not None
                )
                resumed = ProspectStatus.ACTIVE if in_touch else ProspectStatus.NEW
                updated = replace(prospect, status=resumed)
            if updated.status in _ACTIVE_STATUSES:
                schedule = replace(updated.schedule, follow_up_count=0)
                updated = replace(updated, schedule=schedule)
                due = updated.schedule.next_action_due_at
                if (
                    resume_after is not None
                    and updated.status is ProspectStatus.ACTIVE  # a NEW one is first contacted
                    and (updated.schedule.next_action_type is None or due is None or due <= now)
                ):
                    updated = with_next_action(
                        updated, SalesActionType.FOLLOW_UP, now + resume_after
                    )
            if updated != prospect:
                self._repo.update_prospects([updated])
            self._emit(ev.CONVERSATION_RELEASED, actor=by, prospect_id=prospect_id)
            self._flush(RunCollector())
            return ReleaseOutcome(
                released=True,
                status=updated.status.value,
                next_follow_up_at=(
                    updated.schedule.next_action_due_at
                    if updated.status in _ACTIVE_STATUSES
                    else None
                ),
            )

    def send_manual(
        self, prospect_id: ProspectId, text: str, *, idempotency_key: str, actor: str
    ) -> ManualSendReceipt:
        """Send a message a person wrote, on the channel the prospect is reached on.

        Only while a person is in charge of the conversation. It goes out through the same channel
        as the agent's messages, so everything that channel enforces applies as it does for the
        agent (send limits, WhatsApp's 24-hour window and templates, the first-contact rule); a
        prospect who opted out cannot be written to. It is stored with the author `human`.
        Idempotent by `idempotency_key`: the same key never sends twice and returns the first
        outcome. Raises `ManualSendError`, with a stable code and a sentence to show."""
        with self._lock:
            try:
                return self._send_manual(prospect_id, text, idempotency_key, actor)
            except ManualSendError as refusal:
                self._emit(
                    ev.MESSAGE_MANUAL_REFUSED,
                    actor=actor,
                    prospect_id=prospect_id,
                    reason=refusal.code,
                )
                self._flush(RunCollector())
                raise

    def _send_manual(
        self, prospect_id: ProspectId, text: str, idempotency_key: str, actor: str
    ) -> ManualSendReceipt:
        content = text.strip()
        if not content:
            raise ManualSendError("empty", "Write a message first.")
        if self._dry_run:
            raise ManualSendError("dry_run", "This agent is in test mode: nothing is sent.")
        if self._control is None:
            raise ManualSendError("control_unavailable", "Manual messages are not available.")
        prospect = self._repo.get_prospect(prospect_id)
        if prospect is None or prospect.client_id != self._client.client_id:
            raise ManualSendError("unknown_prospect", "No such conversation.")
        key = f"{self._client.client_id}:{prospect_id}:manual:{idempotency_key}"
        earlier = self._repo.find_action_by_idempotency_key(key)
        if earlier is not None:
            return self._manual_outcome(earlier, replayed=True)
        control = self._control.get(prospect_id)
        if control is None or not control.is_human:
            raise ManualSendError(
                "not_in_human_mode", "Take over the conversation before writing to the prospect."
            )
        channel = self._manual_channel(prospect)
        if channel is None:
            raise ManualSendError("no_channel", "This prospect is not reachable on a channel.")
        now = self._clock.now()
        lane = self._router.lane_for(prospect)
        action = SalesAction(
            action_id=ActionId(key),
            client_id=self._client.client_id,
            prospect_id=prospect_id,
            action_type=SalesActionType.REPLY,
            due_at=now,
            idempotency_key=key,
            priority=ActionPriority.URGENT,
            status=ActionStatus.APPROVED,
            draft=MessageDraft(text=content),
            lane_id=lane.lane_id if lane is not None else None,
            channel=channel,
        )
        if control.awaiting_human:
            self._control.take(prospect_id, by=actor, reason=control.reason, at=now)
        self._manual_actors[key] = actor
        run = RunCollector()
        try:
            self._repo.save_actions([action])
            self._dispatch_one(action, run, manual=True)
        except Exception as error:
            self._withdraw_manual(key, describe(error))
            raise ManualSendError(
                "send_failed", "The message could not be sent. Nothing was sent; try again."
            ) from error
        finally:
            self._manual_actors.pop(key, None)
            self._flush(run)
        saved = self._repo.find_action_by_idempotency_key(key)
        if saved is None:  # cannot happen: it was saved above
            raise ManualSendError("send_failed", "The message could not be sent.")
        if saved.status is ActionStatus.APPROVED:
            # The channel said "not now" or "try again": a person's message is never left to be
            # sent later on its own. It is withdrawn and the person decides.
            self._withdraw_manual(key, saved.last_error or "not sent")
            saved = self._repo.find_action_by_idempotency_key(key) or saved
        outcome = self._manual_outcome(saved, replayed=False)
        self._emit(
            ev.MESSAGE_MANUAL_SENT,
            actor=actor,
            prospect_id=prospect_id,
            action_id=saved.action_id,
            channel=channel,
        )
        self._flush(run)
        return outcome

    def _manual_channel(self, prospect: Prospect) -> Channel | None:
        """Where the person is written to: the channel they last wrote on if the client has it,
        else the one of their lane (the only one for a client with no lanes)."""
        conversation = self._repo.get_conversation(prospect.prospect_id)
        for message in reversed(conversation.messages):
            if message.direction is Direction.INBOUND and self._router.adapter(message.channel):
                return message.channel
        return self._router.channel_for(prospect)

    def _manual_verdict(
        self, prospect: Prospect | None, action: SalesAction, channel: Channel
    ) -> SafetyVerdict:
        """What stops a person's message. Not the pause (a conversation in human hands is paused);
        an opt-out, a missing address, another client's prospect, an empty draft."""
        if prospect is None:
            return SafetyVerdict(BlockReason.UNKNOWN_PROSPECT)
        if prospect.client_id != action.client_id:
            return SafetyVerdict(BlockReason.CLIENT_MISMATCH)
        if prospect.status is ProspectStatus.OPTED_OUT:
            return SafetyVerdict(BlockReason.OPTED_OUT)
        if recipient_for(prospect, channel) is None:
            return SafetyVerdict(
                BlockReason.NOT_LINKED if channel in OPT_IN_CHANNELS else BlockReason.NO_CONTACT
            )
        if action.draft is None:
            return SafetyVerdict(BlockReason.NO_DRAFT)
        return ALLOWED

    def _manual_outcome(self, action: SalesAction, *, replayed: bool) -> ManualSendReceipt:
        """The result of a manual message from the action as stored (also for a replayed key)."""
        if action.status is ActionStatus.SENT:
            sent = next(
                (
                    m
                    for m in self._repo.get_conversation(action.prospect_id).messages
                    if m.message_id == f"out:{action.action_id}"
                ),
                None,
            )
            where = sent.channel if sent is not None else action.channel
            return ManualSendReceipt(
                message_id=MessageId(f"out:{action.action_id}"),
                channel=where.value if where is not None else "",
                sent_at=sent.timestamp if sent is not None else None,
                replayed=replayed,
            )
        reason = (action.last_error or "")[:_MAX_ERROR_TEXT]
        if action.status is ActionStatus.SENDING:
            raise ManualSendError(
                "unconfirmed",
                "We could not confirm whether this message was sent. Check the conversation "
                "before writing again.",
            )
        if action.status is ActionStatus.CANCELLED and reason.startswith("safety: "):
            raise _refusal_of(reason.removeprefix("safety: "))
        if action.status is ActionStatus.APPROVED or (
            action.status is ActionStatus.CANCELLED and reason
        ):
            why = (reason or "the channel asked to wait").rstrip(".")
            raise ManualSendError("not_now", f"The message was not sent: {why}.")
        why = (reason or "the channel refused it").rstrip(".")
        raise ManualSendError("send_failed", f"The message was not sent: {why}.")

    def _withdraw_manual(self, key: str, reason: str) -> None:
        """Make sure a manual message that did not go out cannot be sent later by the dispatcher."""
        try:
            action = self._repo.find_action_by_idempotency_key(key)
            if action is not None and action.status in (
                ActionStatus.APPROVED,
                ActionStatus.PLANNED,
            ):
                self._repo.save_actions(
                    [replace(action, status=ActionStatus.CANCELLED, last_error=reason[:300])]
                )
        except Exception:  # noqa: BLE001 - the human gate still holds the dispatcher back
            return

    def _require_control(self) -> ConversationControlStore:
        if self._control is None:
            raise ControlUnavailableError("this agent has no conversation control")
        return self._control

    def _known_prospect(self, prospect_id: ProspectId) -> Prospect:
        prospect = self._repo.get_prospect(prospect_id)
        if prospect is None or prospect.client_id != self._client.client_id:
            raise LookupError(f"unknown prospect {prospect_id!r}")
        return prospect

    # ==== shared helpers =====================================================================

    def _block(
        self,
        action: SalesAction,
        prospect: Prospect | None,
        verdict: SafetyVerdict,
        run: RunCollector,
    ) -> None:
        """Safety said no: never contact. The action is cancelled with the reason."""
        reason = verdict.reason
        detail = f"safety: {reason}"
        self._emit_for(ev.SAFETY_BLOCKED, action, reason=reason)
        if self._dry_run:
            run.outcome(action.action_id, None, detail)
            return
        if (
            reason is BlockReason.NO_CONTACT
            and prospect is not None
            and prospect.status in _ACTIVE_STATUSES
        ):
            self._repo.update_prospects([replace(prospect, status=ProspectStatus.PAUSED)])
            self._emit_for(ev.PROSPECT_PAUSED, action, reason="no contact address")
        self._repo.save_actions([replace(action, status=ActionStatus.CANCELLED, last_error=detail)])
        self._emit_for(ev.ACTION_CANCELLED, action, reason=detail)
        run.outcome(action.action_id, ActionStatus.CANCELLED, detail)

    def _guard(
        self,
        run: RunCollector,
        action: SalesAction,
        step: Callable[..., None],
        *args: object,
    ) -> None:
        """Run one action's step in isolation: a failure is recorded, never propagated."""
        try:
            step(*args)
        except Exception as error:  # noqa: BLE001
            self._record_action_error(run, action, error)
        finally:
            self._flush(run)

    def _record_action_error(
        self, run: RunCollector, action: SalesAction, error: BaseException
    ) -> None:
        detail = describe(error)
        attempts = action.attempts + 1
        run.error(f"{action.action_id}: {detail}")
        self._emit_for(ev.ACTION_ERROR, action, error=detail, attempts=attempts)
        if self._dry_run:
            run.outcome(action.action_id, None, detail)
            return
        status = ActionStatus.FAILED if attempts >= self._config.max_attempts else action.status
        try:
            self._repo.save_actions(
                [replace(action, attempts=attempts, last_error=detail, status=status)]
            )
            run.outcome(action.action_id, status, detail)
        except Exception as save_error:  # noqa: BLE001
            run.error(f"{action.action_id}: could not record the failure: {describe(save_error)}")

    def _step(
        self,
        run: RunCollector,
        action: SalesAction,
        name: str,
        step: Callable[..., None],
        *args: object,
    ) -> None:
        """A post-send bookkeeping step. The message is already out, so failures are only reported."""
        try:
            step(*args)
        except Exception as error:  # noqa: BLE001
            detail = describe(error)
            run.error(f"{action.action_id}: {name} failed after send: {detail}")
            self._emit_for(ev.ACTION_BOOKKEEPING_FAILED, action, step=name, error=detail)

    def _emit(
        self,
        event_type: str,
        *,
        actor: str = "core",
        prospect_id: ProspectId | None = None,
        action_id: ActionId | None = None,
        **details: object,
    ) -> None:
        self._events.append(
            AuditEvent(
                timestamp=self._clock.now(),
                client_id=self._client.client_id,
                event_type=event_type,
                actor=actor,
                prospect_id=prospect_id,
                action_id=action_id,
                details={k: str(v) for k, v in details.items() if v is not None},
            )
        )

    def _emit_for(
        self, event_type: str, action: SalesAction, *, actor: str = "core", **details: object
    ) -> None:
        self._emit(
            event_type,
            actor=actor,
            prospect_id=action.prospect_id,
            action_id=action.action_id,
            **details,
        )

    def _flush(self, run: RunCollector) -> None:
        """Write buffered audit events. On failure they are kept and retried at the next flush."""
        if not self._events:
            return
        batch = list(self._events)
        try:
            self._repo.append_audit_events(batch)
        except Exception as error:  # noqa: BLE001
            run.error(f"audit write failed, {len(batch)} events kept for retry: {describe(error)}")
            return
        del self._events[: len(batch)]
