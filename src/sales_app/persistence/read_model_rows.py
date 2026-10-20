"""The SQL adapter of `ProspectReadModel`: a client's prospects and conversations, listed, filtered,
searched, sorted and paged by the database, without the Engine.

It reads `op_prospect_index` (the projection of the prospects, see `tables.py`), `op_messages` and
`op_actions`. It is bound to one client and every statement filters on it, so no value sent by a
browser can reach another client's rows. Failures are `ReadModelUnavailableError`, a fixed sentence
that never repeats a value: an id, a name or a number could be in what the database said.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    Engine,
    Select,
    and_,
    exists,
    false,
    func,
    or_,
    select,
)
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.tables import op_actions, op_messages, op_prospect_index
from sales_app.read_model.models import (
    ActionError,
    ConversationDetail,
    ConversationItem,
    MessageItem,
    MessageOrder,
    Page,
    ProspectFilter,
    ProspectItem,
    ProspectListQuery,
    ProspectSort,
)
from sales_app.read_model.ports import ReadModelUnavailableError
from sales_app.read_model.search import search_terms

_UNAVAILABLE = "the prospects are unavailable"
_MAX_ERRORS = 20
_MAX_REASON = 300
_FAILED = "failed"
_I = op_prospect_index


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SqlProspectReadModel:
    def __init__(self, engine: Engine, client_id: str) -> None:
        if not client_id:
            raise ValueError("a read model needs a client id")
        self._engine = engine
        self._client_id = client_id

    def __repr__(self) -> str:
        return f"SqlProspectReadModel(client={self._client_id!r})"

    # ---- the port ------------------------------------------------------------------------

    def is_populated(self) -> bool:
        query = select(_I.c.prospect_id).where(_I.c.client_id == self._client_id).limit(1)
        return self._first(query) is not None

    def list_prospects(self, query: ProspectListQuery) -> Page[ProspectItem]:
        where = self._where(query.filter, _I.c.last_activity_at)
        total = select(func.count()).select_from(_I).where(*where)
        page = (
            self._item_columns()
            .where(*where)
            .order_by(*self._order(query.sort, _I.c.last_activity_at))
            .limit(query.limit)
            .offset(query.offset)
        )
        with self._read() as connection:
            count = connection.execute(total).scalar_one()
            rows = connection.execute(page).all()
        return Page(
            items=tuple(self._item(r) for r in rows),
            total=count,
            limit=query.limit,
            offset=query.offset,
        )

    def list_conversations(self, query: ProspectListQuery) -> Page[ConversationItem]:
        talked = (
            select(
                op_messages.c.prospect_id.label("prospect_id"),
                func.max(op_messages.c.sent_at).label("last_at"),
                func.count().label("n"),
            )
            .where(op_messages.c.client_id == self._client_id)
            .group_by(op_messages.c.prospect_id)
            .subquery()
        )
        join = _I.join(
            talked,
            and_(_I.c.prospect_id == talked.c.prospect_id, _I.c.client_id == self._client_id),
        )
        where = self._where(query.filter, talked.c.last_at)
        last_direction = (
            select(op_messages.c.direction)
            .where(
                op_messages.c.client_id == self._client_id,
                op_messages.c.prospect_id == _I.c.prospect_id,
            )
            .order_by(op_messages.c.sent_at.desc(), op_messages.c.seq.desc())
            .limit(1)
            .scalar_subquery()
        )
        total = select(func.count()).select_from(join).where(*where)
        page = (
            self._item_columns(talked.c.last_at, talked.c.n, last_direction.label("last_direction"))
            .select_from(join)
            .where(*where)
            .order_by(*self._order(query.sort, talked.c.last_at))
            .limit(query.limit)
            .offset(query.offset)
        )
        with self._read() as connection:
            count = connection.execute(total).scalar_one()
            rows = connection.execute(page).all()
        return Page(
            items=tuple(
                ConversationItem(
                    prospect=self._item(r),
                    message_count=r.n,
                    last_message_at=_aware(r.last_at),
                    last_message_direction=r.last_direction,
                )
                for r in rows
            ),
            total=count,
            limit=query.limit,
            offset=query.offset,
        )

    def conversation(
        self, prospect_id: str, *, limit: int, offset: int, order: MessageOrder
    ) -> ConversationDetail | None:
        sent = op_messages.c.sent_at
        first = (sent.asc(), op_messages.c.seq.asc())
        last = (sent.desc(), op_messages.c.seq.desc())
        mine = (
            op_messages.c.client_id == self._client_id,
            op_messages.c.prospect_id == prospect_id,
        )
        with self._read() as connection:
            row = connection.execute(
                self._item_columns().where(
                    _I.c.client_id == self._client_id, _I.c.prospect_id == prospect_id
                )
            ).first()
            if row is None:
                return None
            total = connection.execute(
                select(func.count()).select_from(op_messages).where(*mine)
            ).scalar_one()
            messages = connection.execute(
                select(op_messages.c.conversation_id, op_messages.c.document)
                .where(*mine)
                .order_by(*(first if order is MessageOrder.ASC else last))
                .limit(limit)
                .offset(offset)
            ).all()
            failed = connection.execute(
                select(op_actions.c.action_id, op_actions.c.action_type, op_actions.c.due_at)
                .add_columns(op_actions.c.document)
                .where(
                    op_actions.c.client_id == self._client_id,
                    op_actions.c.prospect_id == prospect_id,
                    op_actions.c.status == _FAILED,
                )
                .order_by(op_actions.c.due_at.desc())
                .limit(_MAX_ERRORS)
            ).all()
        item = self._item(row)
        conversation_id = row.conversation_id or (
            messages[0].conversation_id if messages else f"conv-{prospect_id}"
        )
        return ConversationDetail(
            prospect=item,
            conversation_id=conversation_id,
            messages=tuple(_message(m.document) for m in messages),
            total_messages=total,
            limit=limit,
            offset=offset,
            order=order,
            errors=tuple(_error(f) for f in failed),
        )

    # ---- statements ----------------------------------------------------------------------

    def _item_columns(self, *extra: Any) -> Select:
        has_error = exists().where(
            op_actions.c.client_id == self._client_id,
            op_actions.c.prospect_id == _I.c.prospect_id,
            op_actions.c.status == _FAILED,
        )
        return select(*_I.c, has_error.label("has_error"), *extra)

    def _where(self, f: ProspectFilter, activity: ColumnElement) -> list[ColumnElement]:
        where: list[ColumnElement] = [_I.c.client_id == self._client_id]
        if f.statuses:
            where.append(_I.c.status.in_(f.statuses))
        if f.lane_ids is not None:
            where.append(_I.c.lane_id.in_(f.lane_ids) if f.lane_ids else false())
        if f.channel is not None:
            where.append(_I.c.channel == f.channel)
        if f.source_id is not None:
            where.append(_I.c.source_id == f.source_id)
        if f.needs_human is not None:
            where.append(_I.c.needs_human.is_(f.needs_human))
        if f.has_error is not None:
            failed = exists().where(
                op_actions.c.client_id == self._client_id,
                op_actions.c.prospect_id == _I.c.prospect_id,
                op_actions.c.status == _FAILED,
            )
            where.append(failed if f.has_error else ~failed)
        if f.active_since is not None:
            where.append(activity >= f.active_since)
        if f.active_until is not None:
            where.append(activity < f.active_until)
        for term, digits in search_terms(f.search):
            options = [_I.c.search_text.like(_like(term), escape="\\")]
            if digits is not None:
                options.append(_I.c.search_text.like(_like(digits), escape="\\"))
            where.append(or_(*options))
        return where

    @staticmethod
    def _order(sort: ProspectSort, activity: ColumnElement) -> list[ColumnElement]:
        by_name = [func.lower(_I.c.display_name), _I.c.prospect_id]
        if sort is ProspectSort.NAME:
            return by_name
        return [activity.is_(None), activity.desc(), *by_name]

    @staticmethod
    def _item(row: Any) -> ProspectItem:
        return ProspectItem(
            prospect_id=row.prospect_id,
            display_name=row.display_name,
            status=row.status,
            stage=row.stage,
            locale=row.locale,
            lane_id=row.lane_id,
            source_id=row.source_id,
            phone=row.phone,
            email=row.email,
            channel=row.channel,
            next_action_type=row.next_action_type,
            next_action_at=row.next_action_at,
            last_contacted_at=row.last_contacted_at,
            last_inbound_at=row.last_inbound_at,
            last_activity_at=row.last_activity_at,
            follow_up_count=row.follow_up_count,
            needs_human=bool(row.needs_human),
            status_reason=row.status_reason,
            conversation_id=row.conversation_id,
            has_error=bool(row.has_error),
        )

    # ---- plumbing ------------------------------------------------------------------------

    def _first(self, query: Select) -> Any:
        with self._read() as connection:
            return connection.execute(query).first()

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        try:
            with self._engine.connect() as connection:
                yield connection
        except SQLAlchemyError:
            raise ReadModelUnavailableError(_UNAVAILABLE) from None


def _aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):  # an aggregate loses the column's type on SQLite
        value = datetime.fromisoformat(value)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _author(document: Any) -> str:
    """Who wrote a message: the prospect, the agent, or a person of the client (`human`)."""
    if document.get("direction") == "inbound":
        return "prospect"
    metadata = document.get("metadata")
    written = metadata.get("author") if isinstance(metadata, dict) else None
    return "human" if written == "human" else "agent"


def _message(document: Any) -> MessageItem:
    return MessageItem(
        author=_author(document),
        message_id=document["message_id"],
        direction=document["direction"],
        channel=document["channel"],
        text=document["text"],
        timestamp=datetime.fromisoformat(document["timestamp"]),
        external_id=document.get("external_id"),
    )


def _error(row: Any) -> ActionError:
    reason = row.document.get("last_error")
    return ActionError(
        action_id=row.action_id,
        action_type=row.action_type or row.document.get("action_type", ""),
        due_at=row.due_at,
        attempts=int(row.document.get("attempts", 0)),
        reason=None if not reason else str(reason)[:_MAX_REASON],
    )
