"""The Engine: orchestrates the sales cycle through ports.

It owns process (state transitions, persistence, approval, safety, idempotency, retries, audit)
and never decides commercial content or strategy: everything commercial comes from the Brain.
See docs/engine-design.md for the flow and the reasoning behind each rule.

Public entry points (all serialised by one lock, so a client has a single writer):

- `prepare()`   Phase A: plan, reserve quota, ask the Brain, apply decisions, approval gate.
- `dispatch()`  Phase B: send APPROVED actions after re-checking safety.
- `receive_inbound()` / `process_inbound()`  Phase C: store a reply fast, then handle it.

Inbound conversations (Phase C) are outside the daily outbound quota and are asked of the Brain
with `ContactOrigin.INBOUND`. What happens with a sender who is not a prospect is `InboundPolicy`.
"""

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sales_core.approval import ApprovalMode, status_after_proposal
from sales_core.contracts import (
    ActionId,
    ActionPriority,
    ActionStatus,
    BrainRequest,
    Channel,
    ClientContext,
    ContactInfo,
    ContactOrigin,
    Conversation,
    DecisionType,
    Direction,
    InboundMessage,
    Message,
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
from sales_core.engine import events as ev
from sales_core.engine.applier import (
    after_send,
    apply_without_message,
    clear_next_action,
    proposal,
    validate_decision,
)
from sales_core.engine.errors import describe
from sales_core.engine.models import (
    EngineConfig,
    InboundReceipt,
    InboundStatus,
    RunCollector,
    RunReport,
)
from sales_core.planner import plan_daily_actions
from sales_core.ports import Brain, Clock, FactsReader, MessagingChannel, SalesRepository
from sales_core.safety import (
    BlockReason,
    SafetyVerdict,
    check_contactable,
    check_ready_to_send,
    is_opt_out_message,
    recipient_for,
)

UNCONFIRMED = "unconfirmed send"  # prefix of `last_error` on actions stuck in SENDING
_ACTIVE_STATUSES = (ProspectStatus.NEW, ProspectStatus.ACTIVE)


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
        channel: MessagingChannel,
        clock: Clock,
        config: EngineConfig,
        facts: FactsReader | None = None,
    ) -> None:
        if repository.client_id != client.client_id:
            raise ValueError("repository is bound to a different client than the engine")
        self._client = client
        self._repo = repository
        self._brain = brain
        self._channel = channel
        self._clock = clock
        self._config = config
        self._facts = facts  # None: no facts are known, the Brain gets an empty collection
        self._lock = threading.RLock()
        self._events: list[AuditEvent] = []  # audit events not yet written

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
            prospects = list(self._repo.list_prospects())
            actions = list(self._repo.list_actions())
            plan = plan_daily_actions(
                client=self._client,
                prospects=prospects,
                existing_actions=actions,
                now=now,
                daily_limit=self._config.daily_action_limit,
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

    def _prepare_one(
        self, action: SalesAction, prospect: Prospect | None, now: datetime, run: RunCollector
    ) -> None:
        verdict = check_contactable(prospect, self._channel.channel)
        if prospect is None or not verdict.allowed:
            self._block(action, prospect, verdict, run)
            return
        decision = self._brain.decide(self._request_for(action, prospect, now))
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

        if self._dry_run:
            self._emit_for(
                ev.DRYRUN_DECISION,
                action,
                decision=decision.decision_type,
                message=decision.message.text if decision.message else None,
            )
            run.outcome(action.action_id, None, f"dry run: {decision.decision_type}")
            return

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
        elif decision.decision_type is DecisionType.CLOSE:
            self._emit_for(ev.PROSPECT_CLOSED, action, rationale=decision.rationale or None)
        run.outcome(action.action_id, ActionStatus.COMPLETED, str(decision.decision_type))

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

    def _dispatch_one(self, action: SalesAction, run: RunCollector) -> None:
        channel = self._channel.channel
        prospect = self._repo.get_prospect(action.prospect_id)  # fresh state, not a snapshot
        verdict = check_ready_to_send(prospect, action, channel)
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
        )
        sending = replace(action, status=ActionStatus.SENDING)
        # Persist SENDING before the channel call. If this write fails we raise: nothing was sent.
        self._repo.save_actions([sending])
        try:
            result = self._channel.send(outbound)
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
        self._step(run, sent, "append_message", self._record_outbound, sent, result)
        self._step(
            run,
            sent,
            "update_prospect",
            lambda: self._repo.update_prospects([after_send(prospect, sent, result.timestamp)]),
        )
        self._emit_for(ev.ACTION_SENT, sent, external_id=result.external_id)
        run.outcome(sent.action_id, ActionStatus.SENT if recorded else ActionStatus.SENDING)

    def _record_outbound(self, action: SalesAction, result: SendResult) -> None:
        draft = action.draft
        if draft is None:
            raise ValueError("cannot record a message without a draft")
        conversation = self._repo.get_conversation(action.prospect_id)
        metadata = {"action_id": action.action_id}
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
                    channel=self._channel.channel,
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
        attempts = action.attempts + 1
        retry = result.retryable and attempts < self._config.max_attempts
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
        prospect = self._find_sender(inbound) or self._engage_unknown_sender(inbound)
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
        by_email = inbound.channel is Channel.EMAIL
        address = inbound.sender.strip()
        digits = "".join(c for c in address if c.isdigit())
        key = address.casefold() if by_email or not digits else digits
        prospect = Prospect(
            client_id=self._client.client_id,
            # The local day is part of the id: that is what `_created_on` counts, and it survives
            # restarts. A redelivery never reaches here again: the prospect is found by contact.
            prospect_id=ProspectId(f"{_created_prefix(today)}{key}"),
            display_name=address,
            contact=ContactInfo(
                email=address if by_email else None,
                phone=None if by_email else address,
                preferred_channel=inbound.channel,
            ),
            status=ProspectStatus.ACTIVE,
            schedule=Scheduling(last_inbound_at=inbound.timestamp),
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

    def _find_sender(self, inbound: InboundMessage) -> Prospect | None:
        if inbound.channel is Channel.EMAIL:
            return self._repo.find_prospect_by_contact(email=inbound.sender)
        return self._repo.find_prospect_by_contact(phone=inbound.sender)

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

        if is_opt_out_message(message.text, self._config.opt_out_keywords):
            self._handle_opt_out(prospect, action, run)
            return
        verdict = check_contactable(prospect, self._channel.channel)
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
        self, prospect_id: ProspectId, *, keep: ActionId, run: RunCollector
    ) -> None:
        """Cancel every unsent action for the prospect: the reply makes them stale."""
        open_statuses = {ActionStatus.PLANNED, ActionStatus.PROPOSED, ActionStatus.APPROVED}
        stale = [
            a
            for a in self._repo.list_actions(open_statuses)
            if a.prospect_id == prospect_id and a.action_id != keep
        ]
        if not stale:
            return
        reason = "superseded by inbound reply"
        self._repo.save_actions(
            [replace(a, status=ActionStatus.CANCELLED, last_error=reason) for a in stale]
        )
        for action in stale:
            self._emit_for(ev.ACTION_CANCELLED, action, reason=reason)
            run.outcome(action.action_id, ActionStatus.CANCELLED, reason)

    def _reply_key(self, prospect_id: ProspectId, message_id: MessageId) -> str:
        return f"{self._client.client_id}:{prospect_id}:reply:{message_id}"

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
