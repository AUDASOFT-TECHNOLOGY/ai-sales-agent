"""The SQL registers an agent learns from (`sales_app.learning_store`).

Bound to one client: every statement filters on it. A database that cannot be reached is
`StoreUnavailableError`, a fixed sentence.
"""

import json
from datetime import UTC, datetime

from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import SQLAlchemyError

from sales_app.learning_store import (
    REVIEW_KEEP,
    TRACE_KEEP,
    StoredNote,
    StoredReview,
    StoredTrace,
)
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import op_learning_notes as notes
from sales_app.persistence.tables import op_learning_reviews as reviews
from sales_app.persistence.tables import op_learning_traces as traces
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "what the agent learned is unavailable"


def _split(text: str) -> tuple[str, ...]:
    return tuple(part for part in text.split(",") if part)


class SqlLearningStore:
    def __init__(self, engine: Engine, client_id: str) -> None:
        if not client_id:
            raise ValueError("a learning store needs a client id")
        self._engine = engine
        self._client_id = client_id

    def __repr__(self) -> str:
        return f"SqlLearningStore(client={self._client_id!r})"

    def _register(self, connection) -> None:
        ensure_client(connection, self._client_id, created_at=datetime.now(UTC), created_by=None)

    def record_trace(self, trace: StoredTrace) -> None:
        values = {
            "client_id": self._client_id,
            "case_ref": trace.case_ref,
            "action_ref": trace.action_ref,
            "at": trace.at,
            "decision": trace.decision,
            "action_type": trace.action_type,
            "channel": trace.channel,
            "length": trace.length,
            "questions": trace.questions,
            "gap_hours": trace.gap_hours,
            "unanswered": trace.unanswered,
            "answers_reply": trace.answers_reply,
            "labels": json.dumps(trace.labels, sort_keys=True),
        }
        try:
            with self._engine.begin() as connection:
                self._register(connection)
                connection.execute(
                    delete(traces).where(
                        traces.c.client_id == self._client_id,
                        traces.c.action_ref == trace.action_ref,
                    )
                )
                connection.execute(insert(traces).values(**values))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def traces_of(self, case_ref: str) -> list[StoredTrace]:
        query = (
            select(traces)
            .where(traces.c.client_id == self._client_id, traces.c.case_ref == case_ref)
            .order_by(traces.c.at, traces.c.seq)
        )
        try:
            with self._engine.connect() as connection:
                return [
                    StoredTrace(
                        case_ref=r.case_ref,
                        action_ref=r.action_ref,
                        at=r.at,
                        decision=r.decision,
                        action_type=r.action_type,
                        channel=r.channel,
                        length=r.length,
                        questions=r.questions,
                        gap_hours=float(r.gap_hours) if r.gap_hours is not None else None,
                        unanswered=r.unanswered,
                        answers_reply=bool(r.answers_reply),
                        labels=json.loads(r.labels or "{}"),
                    )
                    for r in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def case_refs(self) -> list[str]:
        query = select(traces.c.case_ref).where(traces.c.client_id == self._client_id).distinct()
        try:
            with self._engine.connect() as connection:
                return sorted(r.case_ref for r in connection.execute(query))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def reviewed_refs(self) -> set[str]:
        query = select(reviews.c.case_ref).where(reviews.c.client_id == self._client_id)
        try:
            with self._engine.connect() as connection:
                return {r.case_ref for r in connection.execute(query)}
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def save_review(self, review: StoredReview) -> None:
        values = {
            "client_id": self._client_id,
            "case_ref": review.case_ref,
            "result": review.result,
            "ended_at": review.ended_at,
            "reviewed_at": review.reviewed_at,
            "causes": ",".join(review.causes),
            "practices": ",".join(review.practices),
            "confidence": review.confidence,
            "turning_step": review.turning_step,
            "unknown": review.unknown,
            "product": review.product,
            "applied_notes": ",".join(review.applied_notes),
            "steps_seen": review.steps_seen,
        }
        try:
            with self._engine.begin() as connection:
                self._register(connection)
                connection.execute(
                    delete(reviews).where(
                        reviews.c.client_id == self._client_id,
                        reviews.c.case_ref == review.case_ref,
                    )
                )
                connection.execute(insert(reviews).values(**values))
                connection.execute(
                    delete(traces).where(
                        traces.c.client_id == self._client_id,
                        traces.c.case_ref == review.case_ref,
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def reviews(self) -> list[StoredReview]:
        query = select(reviews).where(reviews.c.client_id == self._client_id)
        try:
            with self._engine.connect() as connection:
                return [
                    StoredReview(
                        case_ref=r.case_ref,
                        result=r.result,
                        ended_at=r.ended_at,
                        reviewed_at=r.reviewed_at,
                        causes=_split(r.causes),
                        practices=_split(r.practices),
                        confidence=r.confidence,
                        turning_step=r.turning_step,
                        unknown=bool(r.unknown),
                        product=r.product,
                        applied_notes=_split(r.applied_notes),
                        steps_seen=r.steps_seen,
                    )
                    for r in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def notes(self) -> list[StoredNote]:
        query = select(notes).where(notes.c.client_id == self._client_id)
        try:
            with self._engine.connect() as connection:
                return [
                    StoredNote(
                        note_id=r.note_id,
                        code=r.code,
                        kind=r.kind,
                        theme=r.theme,
                        state=r.state,
                        product=r.product,
                        evidence=r.evidence,
                        cases=r.cases,
                        against=r.against,
                        reason=r.reason,
                        created_at=r.created_at,
                        updated_at=r.updated_at,
                        active_since=r.active_since,
                        off_by=r.off_by,
                        confirmed_by_client=bool(r.confirmed_by_client),
                    )
                    for r in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def _values(self, note: StoredNote) -> dict:
        return {
            "client_id": self._client_id,
            "note_id": note.note_id,
            "code": note.code,
            "kind": note.kind,
            "theme": note.theme,
            "state": note.state,
            "product": note.product,
            "evidence": note.evidence,
            "cases": note.cases,
            "against": note.against,
            "reason": note.reason,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "active_since": note.active_since,
            "off_by": note.off_by,
            "confirmed_by_client": note.confirmed_by_client,
        }

    def replace_notes(self, items: list[StoredNote]) -> None:
        try:
            with self._engine.begin() as connection:
                self._register(connection)
                connection.execute(delete(notes).where(notes.c.client_id == self._client_id))
                if items:
                    connection.execute(insert(notes), [self._values(n) for n in items])
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def set_note(self, note: StoredNote) -> None:
        try:
            with self._engine.begin() as connection:
                self._register(connection)
                connection.execute(
                    delete(notes).where(
                        notes.c.client_id == self._client_id, notes.c.note_id == note.note_id
                    )
                )
                connection.execute(insert(notes).values(**self._values(note)))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def forget_case(self, case_ref: str) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(traces).where(
                        traces.c.client_id == self._client_id, traces.c.case_ref == case_ref
                    )
                )
                connection.execute(
                    delete(reviews).where(
                        reviews.c.client_id == self._client_id, reviews.c.case_ref == case_ref
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def purge(self, now: datetime) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(traces).where(
                        traces.c.client_id == self._client_id, traces.c.at < now - TRACE_KEEP
                    )
                )
                connection.execute(
                    delete(reviews).where(
                        reviews.c.client_id == self._client_id,
                        reviews.c.ended_at < now - REVIEW_KEEP,
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
