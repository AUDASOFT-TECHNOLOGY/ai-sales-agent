"""The agent learns from its results: tying traces to finished conversations, reviewing them, and
keeping the rules that hold. One run per client, off the conversation path.

`LearningService.run` does, in order:

1. forgets what is past its retention;
2. for each conversation that has traces and no verdict yet, tells its result (`sales_core.learning`).
   Only a *final* result (achieved, declined, uneasy) is reviewed: a silence is not a refusal, a
   conversation a person took over is not the agent's, a rehearsal never teaches;
3. has the Brain review it (this may call the client's model: counted like every other call, skipped
   when the credits are used up, and bounded to `MAX_REVIEWS_PER_RUN` so a run stays short), keeps the
   verdict and deletes the conversation's traces;
4. asks the Brain which rules hold from all the verdicts, and keeps them.

The run never touches a conversation in progress. It stores no name, contact or message.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from sales_app.learning_store import LearningStore, StoredNote, StoredReview, StoredTrace
from sales_core.contracts import (
    Conversation,
    Direction,
    LearnedNote,
    ProspectId,
)
from sales_core.learning import (
    CaseReview,
    CaseStep,
    CaseText,
    ConversationSignals,
    DecisionTrace,
    LearningCase,
    Reflector,
    Result,
    anonymous_ref,
    is_rehearsal,
    resolve_result,
)
from sales_core.ports import LLMAuthError, LLMError, LLMRateLimitError

log = logging.getLogger(__name__)

MAX_REVIEWS_PER_RUN = 12
MAX_TRANSCRIPT = 40
MAX_ACTIVE_FOR_PROMPT = 8


class Person(Protocol):
    """What a run needs to know of a person in a conversation (the Core's prospect has it)."""

    @property
    def prospect_id(self) -> ProspectId: ...

    @property
    def display_name(self) -> str: ...


class LearningSource(Protocol):
    """What a run reads of the Engine: prospects, their history, how each conversation stands."""

    def list_prospects(self) -> Sequence[Person]: ...

    def conversation_of(self, prospect_id: ProspectId) -> Conversation | None: ...

    def outcome_signals(self, prospect_id: ProspectId) -> ConversationSignals | None: ...

    def reflector(self) -> Reflector | None: ...


@dataclass(frozen=True, slots=True)
class LearningRun:
    reviewed: int = 0
    skipped_for_credits: bool = False
    failed: int = 0
    notes_active: int = 0


class StoreTraceSink:
    """The `TraceSink` of a client: hands each trace to its store."""

    def __init__(self, store: LearningStore) -> None:
        self._store = store

    def record(self, trace: DecisionTrace) -> None:
        self._store.record_trace(
            StoredTrace(
                case_ref=trace.case_ref,
                action_ref=trace.action_ref,
                at=trace.at,
                decision=trace.decision,
                action_type=trace.action_type,
                channel=trace.channel,
                length=trace.length,
                questions=trace.questions,
                gap_hours=trace.gap_hours,
                unanswered=trace.unanswered,
                answers_reply=trace.answers_reply,
                labels=dict(trace.labels),
            )
        )


class StoreNotes:
    """The `NotesProvider` of a client: the active notes, a few, the best supported first."""

    def __init__(self, store: LearningStore) -> None:
        self._store = store

    def active_notes(self) -> list[LearnedNote]:
        active = [n for n in self._store.notes() if n.state == "active"]
        active.sort(key=lambda n: (not n.confirmed_by_client, -n.cases, n.note_id))
        return [to_note(n) for n in active[:MAX_ACTIVE_FOR_PROMPT]]


def to_note(row: StoredNote) -> LearnedNote:
    return LearnedNote(
        note_id=row.note_id,
        code=row.code,
        kind=row.kind,  # type: ignore[arg-type]
        theme=row.theme,
        state=row.state,  # type: ignore[arg-type]
        product=row.product,
        evidence=row.evidence,  # type: ignore[arg-type]
        cases=row.cases,
        against=row.against,
        reason=row.reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
        active_since=row.active_since,
        off_by=row.off_by,  # type: ignore[arg-type]
        confirmed_by_client=row.confirmed_by_client,
    )


def to_row(note: LearnedNote) -> StoredNote:
    return StoredNote(
        note_id=note.note_id,
        code=note.code,
        kind=note.kind,
        theme=note.theme,
        state=note.state,
        product=note.product,
        evidence=note.evidence,
        cases=note.cases,
        against=note.against,
        reason=note.reason,
        created_at=note.created_at,
        updated_at=note.updated_at,
        active_since=note.active_since,
        off_by=note.off_by,
        confirmed_by_client=note.confirmed_by_client,
    )


def _case_review(row: StoredReview) -> CaseReview:
    return CaseReview(
        case_ref=row.case_ref,
        result=Result(row.result),
        ended_at=row.ended_at,
        reviewed_at=row.reviewed_at,
        causes=row.causes,
        practices=row.practices,
        confidence=row.confidence,  # type: ignore[arg-type]
        turning_step=row.turning_step,
        unknown=row.unknown,
        product=row.product,
        applied_notes=frozenset(row.applied_notes),
        steps_seen=row.steps_seen,
    )


def _review_row(review: CaseReview) -> StoredReview:
    return StoredReview(
        case_ref=review.case_ref,
        result=review.result.value,
        ended_at=review.ended_at,
        reviewed_at=review.reviewed_at,
        causes=review.causes,
        practices=review.practices,
        confidence=review.confidence,
        turning_step=review.turning_step,
        unknown=review.unknown,
        product=review.product,
        applied_notes=tuple(sorted(review.applied_notes)),
        steps_seen=review.steps_seen,
    )


class LearningService:
    def __init__(
        self,
        client_id: str,
        store: LearningStore,
        reflector: Reflector | None = None,
        *,
        may_spend: Callable[[], bool] = lambda: True,
    ) -> None:
        self._client_id = client_id
        self._store = store
        self._reflector = reflector
        self._may_spend = may_spend

    def __repr__(self) -> str:
        return f"LearningService(client={self._client_id!r})"

    # ---- the run -------------------------------------------------------------------------

    def run(self, source: LearningSource, now: datetime) -> LearningRun:
        store = self._store
        store.purge(now)
        reflector = self._reflector or source.reflector()
        if reflector is None:
            return LearningRun()  # this Brain cannot learn from results
        pending = set(store.case_refs()) - store.reviewed_refs()
        reviewed = failed = 0
        skipped = False
        achieved = reflector.achieved_stages()
        if pending:
            by_ref = {
                anonymous_ref(self._client_id, p.prospect_id): p
                for p in source.list_prospects()
                if not is_rehearsal(p.prospect_id)
            }
            for ref in sorted(pending):
                prospect = by_ref.get(ref)
                if prospect is None:
                    continue
                traces = store.traces_of(ref)
                case = self._case(source, prospect, traces, achieved, now)
                if case is None or not case.result.is_final:
                    continue
                if reviewed >= MAX_REVIEWS_PER_RUN:
                    break
                if not self._may_spend():
                    skipped = True
                    break
                try:
                    verdict = reflector.review(case_with_words(case, source, prospect), now=now)
                except (LLMAuthError, LLMRateLimitError):
                    failed += 1
                    break  # the account cannot answer now: try again on the next run
                except (LLMError, ValueError) as error:
                    failed += 1
                    log.warning("%s: a review failed (%s)", self._client_id, type(error).__name__)
                    continue
                store.save_review(_review_row(verdict))
                reviewed += 1
        notes = self._consolidate(reflector, now)
        return LearningRun(
            reviewed=reviewed,
            skipped_for_credits=skipped,
            failed=failed,
            notes_active=sum(1 for n in notes if n.state == "active"),
        )

    def _consolidate(self, reflector: Reflector, now: datetime) -> list[LearnedNote]:
        reviews = [_case_review(r) for r in self._store.reviews()]
        current = [to_note(n) for n in self._store.notes()]
        notes = list(reflector.consolidate(reviews, current, now=now))
        self._store.replace_notes([to_row(n) for n in notes])
        return notes

    # ---- one conversation ------------------------------------------------------------------

    def _case(
        self,
        source: LearningSource,
        prospect: Person,
        traces: list[StoredTrace],
        achieved: frozenset[str],
        now: datetime,
    ) -> LearningCase | None:
        conversation = source.conversation_of(prospect.prospect_id)
        signals = source.outcome_signals(prospect.prospect_id)
        if conversation is None or signals is None or not traces:
            return None
        last_reply = next((t for t in reversed(traces) if t.answers_reply), None)
        quiet_after_it = last_reply is not None and not any(
            m.direction is Direction.OUTBOUND and m.timestamp > last_reply.at
            for m in conversation.messages
        )
        # The latest answer's own signals count only while the agent has not written since.
        signals = replace(
            signals,
            impatient=quiet_after_it and "impatience" in _stop(last_reply),
            refused=quiet_after_it
            and last_reply is not None
            and last_reply.labels.get("refusal") == "true",
        )
        result = resolve_result(
            prospect.prospect_id, conversation, signals, achieved_stages=achieved, now=now
        )
        if result is None:
            return None
        steps = _steps(traces, conversation, self._client_id)
        if not steps:
            return None
        products = [s.labels["selected_product"] for s in steps if "selected_product" in s.labels]
        applied = {
            note for s in steps for note in s.labels.get("learned_notes", "").split(",") if note
        }
        return LearningCase(
            case_ref=anonymous_ref(self._client_id, prospect.prospect_id),
            result=result,
            ended_at=conversation.messages[-1].timestamp,
            steps=steps,
            product=max(set(products), key=products.count) if products else None,
            applied_notes=frozenset(applied),
        )

    # ---- what the client sees and decides -------------------------------------------------

    def notes(self) -> list[LearnedNote]:
        return [to_note(n) for n in self._store.notes()]

    def counts(self) -> dict[str, int]:
        results = [r.result for r in self._store.reviews()]
        return {
            "reviewed": len(results),
            "achieved": results.count(Result.ACHIEVED.value),
            "unfavourable": sum(r in (Result.DECLINED.value, Result.UNEASY.value) for r in results),
        }

    def decide(self, note_id: str, action: str, now: datetime) -> LearnedNote | None:
        """The client's choice about one note: `off` (never apply it), `confirm` (apply it, keep it),
        `on` (let the agent decide again). None for an unknown note."""
        row = next((n for n in self._store.notes() if n.note_id == note_id), None)
        if row is None:
            return None
        if action == "off":
            row = replace(
                row, state="off", off_by="client", confirmed_by_client=False, active_since=None
            )
        elif action == "confirm":
            row = replace(
                row,
                state="active",
                off_by=None,
                confirmed_by_client=True,
                active_since=row.active_since or now,
                reason="client_confirmed",
            )
        elif action == "on":
            row = replace(row, state="watching", off_by=None, confirmed_by_client=False)
        else:
            raise ValueError("unknown choice")
        row = replace(row, updated_at=now)
        self._store.set_note(row)
        return to_note(row)


def _stop(trace: StoredTrace | None) -> set[str]:
    if trace is None:
        return set()
    return {s for s in trace.labels.get("stop", "").split(",") if s}


def _steps(
    traces: list[StoredTrace], conversation: Conversation, client_id: str
) -> tuple[CaseStep, ...]:
    """The decisions that really happened, each with the reaction that followed."""
    sent = {
        anonymous_ref(client_id, m.metadata["action_id"]): m
        for m in conversation.messages
        if m.direction is Direction.OUTBOUND and m.metadata.get("action_id")
    }
    inbound = [m for m in conversation.messages if m.direction is Direction.INBOUND]
    steps: list[CaseStep] = []
    kept = [t for t in traces if t.decision != "send_message" or _is_sent(t, sent)]
    for position, trace in enumerate(kept):
        message = _message_of(trace, sent)
        reaction = "not_sent"
        reply_after: float | None = None
        reply_labels: dict[str, str] = {}
        if message is not None:
            later = next((m for m in inbound if m.timestamp > message.timestamp), None)
            nxt = kept[position + 1] if position + 1 < len(kept) else None
            if later is not None:
                reaction = "replied"
                reply_after = round((later.timestamp - message.timestamp).total_seconds() / 3600, 1)
                answer = next(
                    (
                        t
                        for t in kept[position + 1 :]
                        if t.answers_reply and t.at >= later.timestamp
                    ),
                    None,
                )
                if answer is not None:
                    reply_labels = {
                        k: v
                        for k, v in answer.labels.items()
                        if k in ("intent", "objection", "refusal", "stop")
                    }
            elif nxt is not None:
                reaction = "no_reply"
            else:
                reaction = "pending"
        steps.append(
            CaseStep(
                index=position,
                at=trace.at,
                decision=trace.decision,
                action_type=trace.action_type,
                channel=trace.channel,
                length=trace.length,
                questions=trace.questions,
                gap_hours=trace.gap_hours,
                unanswered=trace.unanswered,
                answers_reply=trace.answers_reply,
                labels=trace.labels,
                reaction=reaction,  # type: ignore[arg-type]
                reply_after_hours=reply_after,
                reply_labels=reply_labels,
            )
        )
    return tuple(steps)


def _is_sent(trace: StoredTrace, sent: dict[str, object]) -> bool:
    return trace.action_ref in sent


def _message_of(trace: StoredTrace, sent: dict[str, object]):
    return sent.get(trace.action_ref)


def case_with_words(case: LearningCase, source: LearningSource, prospect: Person) -> LearningCase:
    """The case with the exchange attached, for the review only. The words are not stored."""
    conversation = source.conversation_of(prospect.prospect_id)
    lines: tuple[CaseText, ...] = ()
    if conversation is not None:
        lines = tuple(
            CaseText(
                role="agent" if m.direction is Direction.OUTBOUND else "person",
                text=m.text,
            )
            for m in conversation.messages[-MAX_TRANSCRIPT:]
            if m.text
        )
    return replace(case, transcript=lines, hide=(prospect.display_name,))
