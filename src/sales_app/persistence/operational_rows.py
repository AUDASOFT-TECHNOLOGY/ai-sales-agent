"""The SQL half of a client's operational store: rows in, rows out, for one client.

This module knows SQL and nothing of what the rows mean. It moves plain values (strings, numbers,
timestamps and JSON documents); turning them into and out of the Core's objects is the job of
`sales_app.sourcing.sql_store`, which is the only code that names a prospect or an action. That keeps
SQL confined to this package while the Core's contracts stay out of it.

Every object is bound to one client. Every statement filters on that client, so another client's
rows are not reachable through it. Writes are one transaction each: a refused call writes nothing.

Failures are fixed sentences, never a value, an id or what the database said: a local prospect's id
embeds the sender's number or address. A database that cannot be reached is `StoreUnavailableError`;
a stale revision is `StateConflictError`; anything the caller got wrong (a duplicate) is `ValueError`.
"""

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import (
    op_actions,
    op_audit,
    op_facts,
    op_local_contacts,
    op_local_prospects,
    op_messages,
    op_prospect_index,
    op_prospect_states,
)
from sales_app.sourcing.errors import StateConflictError, StoreUnavailableError

Row = Mapping[str, Any]
_UNAVAILABLE = "the operational store is unavailable"


class SqlOperationalRows:
    def __init__(
        self, engine: Engine, client_id: str, *, now: Callable[[], datetime] | None = None
    ):
        if not client_id:
            raise ValueError("an operational store needs a client id")
        self._engine = engine
        self._client_id = client_id
        self._now = now or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"SqlOperationalRows(client={self._client_id!r})"

    @property
    def client_id(self) -> str:
        return self._client_id

    # ---- plumbing ------------------------------------------------------------------------

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        try:
            with self._engine.connect() as connection:
                yield connection
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    @contextmanager
    def _write(self) -> Iterator[Connection]:
        """One transaction. Registers the client first (idempotent), so the foreign key holds."""
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, self._client_id, created_at=self._now(), created_by=None)
                yield connection
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def verify(self) -> list[str]:
        try:
            with self._engine.connect() as connection:
                for table in (op_actions, op_messages, op_audit, op_facts, op_prospect_states):
                    connection.execute(select(table.c.client_id).limit(1)).first()
        except SQLAlchemyError:
            return [_UNAVAILABLE]
        return []

    # ---- actions -------------------------------------------------------------------------

    def list_actions(self, statuses: Collection[str] | None = None) -> list[Any]:
        query = select(op_actions.c.document).where(op_actions.c.client_id == self._client_id)
        if statuses is not None:
            query = query.where(op_actions.c.status.in_(list(statuses)))
        with self._read() as connection:
            return [r.document for r in connection.execute(query.order_by(op_actions.c.action_id))]

    def find_action_by_key(self, key: str) -> Any | None:
        query = select(op_actions.c.document).where(
            op_actions.c.client_id == self._client_id, op_actions.c.idempotency_key == key
        )
        with self._read() as connection:
            row = connection.execute(query).first()
        return None if row is None else row.document

    def save_actions(self, rows: Sequence[Row]) -> None:
        """Insert or update by action id, all or nothing. An idempotency key held by another action
        is a `ValueError`."""
        with self._write() as connection:
            try:
                for row in rows:
                    values = {
                        "idempotency_key": row["idempotency_key"],
                        "status": row["status"],
                        "document": row["document"],
                        "prospect_id": row.get("prospect_id"),
                        "action_type": row.get("action_type"),
                        "due_at": row.get("due_at"),
                    }
                    changed = connection.execute(
                        update(op_actions)
                        .where(
                            op_actions.c.client_id == self._client_id,
                            op_actions.c.action_id == row["action_id"],
                        )
                        .values(**values)
                    ).rowcount
                    if not changed:
                        connection.execute(
                            insert(op_actions).values(
                                client_id=self._client_id, action_id=row["action_id"], **values
                            )
                        )
            except IntegrityError:
                raise ValueError("an idempotency key is already in use") from None

    # ---- messages ------------------------------------------------------------------------

    def conversation_id_of(self, prospect_id: str) -> str | None:
        """The conversation already used for this prospect: its history's, else its state's."""
        with self._read() as connection:
            return self._known_conversation(connection, prospect_id)

    def list_messages(self, prospect_id: str) -> list[Any]:
        query = (
            select(op_messages.c.document)
            .where(
                op_messages.c.client_id == self._client_id,
                op_messages.c.prospect_id == prospect_id,
            )
            .order_by(op_messages.c.seq)
        )
        with self._read() as connection:
            return [r.document for r in connection.execute(query)]

    def append_messages(self, rows: Sequence[Row]) -> None:
        """All or nothing. A message id never stored twice (`ValueError`); a prospect keeps one
        conversation (`ValueError` for a message that names another)."""
        with self._write() as connection:
            assigned: dict[str, str] = {}
            seen: set[str] = set()
            for row in rows:
                if (
                    row["message_id"] in seen
                    or connection.execute(
                        select(op_messages.c.seq).where(
                            op_messages.c.client_id == self._client_id,
                            op_messages.c.message_id == row["message_id"],
                        )
                    ).first()
                ):
                    raise ValueError("a message with this id already exists")
                seen.add(row["message_id"])
                pid = row["prospect_id"]
                expected = assigned.get(pid) or self._known_conversation(connection, pid)
                if expected is not None and row["conversation_id"] != expected:
                    raise ValueError("the message is not in the conversation its prospect uses")
                assigned[pid] = row["conversation_id"]
            try:
                for row in rows:
                    connection.execute(
                        insert(op_messages).values(
                            client_id=self._client_id,
                            message_id=row["message_id"],
                            prospect_id=row["prospect_id"],
                            conversation_id=row["conversation_id"],
                            document=row["document"],
                            direction=row.get("direction"),
                            channel=row.get("channel"),
                            sent_at=row.get("sent_at"),
                        )
                    )
            except IntegrityError:
                raise ValueError("a message with this id already exists") from None

    def _known_conversation(self, connection: Connection, prospect_id: str) -> str | None:
        found = connection.execute(
            select(op_messages.c.conversation_id)
            .where(
                op_messages.c.client_id == self._client_id,
                op_messages.c.prospect_id == prospect_id,
            )
            .order_by(op_messages.c.seq)
            .limit(1)
        ).first()
        if found is not None:
            return found.conversation_id
        state = connection.execute(
            select(op_prospect_states.c.conversation_id).where(
                op_prospect_states.c.client_id == self._client_id,
                op_prospect_states.c.prospect_id == prospect_id,
            )
        ).first()
        return None if state is None else state.conversation_id

    # ---- audit ---------------------------------------------------------------------------

    def append_audit(self, rows: Sequence[Row]) -> None:
        with self._write() as connection:
            for row in rows:
                connection.execute(
                    insert(op_audit).values(
                        client_id=self._client_id,
                        occurred_at=row["occurred_at"],
                        event_type=row["event_type"],
                        prospect_id=row.get("prospect_id"),
                        action_id=row.get("action_id"),
                        document=row["document"],
                    )
                )

    def list_audit(self) -> list[Any]:
        query = (
            select(op_audit.c.document)
            .where(op_audit.c.client_id == self._client_id)
            .order_by(op_audit.c.seq)
        )
        with self._read() as connection:
            return [r.document for r in connection.execute(query)]

    # ---- facts ---------------------------------------------------------------------------

    def list_facts(self, prospect_id: str) -> list[Any]:
        """Oldest first by `recorded_at`; ties keep the order they were appended in."""
        query = (
            select(op_facts.c.document)
            .where(op_facts.c.client_id == self._client_id, op_facts.c.prospect_id == prospect_id)
            .order_by(op_facts.c.recorded_at, op_facts.c.seq)
        )
        with self._read() as connection:
            return [r.document for r in connection.execute(query)]

    def append_facts(self, prospect_id: str, rows: Sequence[Row]) -> None:
        """A fact already recorded (same `fact_id`) is left as it is."""
        with self._write() as connection:
            seen: set[str] = set()
            for row in rows:
                if row["fact_id"] in seen:
                    continue
                seen.add(row["fact_id"])
                if connection.execute(
                    select(op_facts.c.seq).where(
                        op_facts.c.client_id == self._client_id,
                        op_facts.c.fact_id == row["fact_id"],
                    )
                ).first():
                    continue
                try:
                    with connection.begin_nested():
                        connection.execute(
                            insert(op_facts).values(
                                client_id=self._client_id,
                                fact_id=row["fact_id"],
                                prospect_id=prospect_id,
                                recorded_at=row["recorded_at"],
                                document=row["document"],
                                fact_key=row.get("fact_key"),
                            )
                        )
                except IntegrityError:
                    continue  # a concurrent writer recorded the same observation first

    # ---- prospect states -----------------------------------------------------------------

    def get_states(self, prospect_ids: Collection[str]) -> dict[str, Row]:
        wanted = list(prospect_ids)
        if not wanted:
            return {}
        query = select(op_prospect_states).where(
            op_prospect_states.c.client_id == self._client_id,
            op_prospect_states.c.prospect_id.in_(wanted),
        )
        with self._read() as connection:
            return {r.prospect_id: _state_row(r) for r in connection.execute(query)}

    def list_states(self) -> dict[str, Row]:
        query = select(op_prospect_states).where(op_prospect_states.c.client_id == self._client_id)
        with self._read() as connection:
            return {r.prospect_id: _state_row(r) for r in connection.execute(query)}

    def save_states(self, rows: Sequence[Row]) -> None:
        """Each row carries the `revision` it was read at (0 for a new state); it is stored only if
        that is still the stored revision, and stored as `revision + 1`. A stale one raises
        `StateConflictError` and nothing is written."""
        with self._write() as connection:
            ids: set[str] = set()
            for row in rows:
                if row["prospect_id"] in ids:
                    raise ValueError("a prospect appears twice in one call")
                ids.add(row["prospect_id"])
            for row in rows:
                values = {
                    "revision": row["revision"] + 1,
                    "conversation_id": row.get("conversation_id"),
                    "document": row["document"],
                }
                pid = row["prospect_id"]
                changed = connection.execute(
                    update(op_prospect_states)
                    .where(
                        op_prospect_states.c.client_id == self._client_id,
                        op_prospect_states.c.prospect_id == pid,
                        op_prospect_states.c.revision == row["revision"],
                    )
                    .values(**values)
                ).rowcount
                if changed:
                    continue
                if row["revision"] != 0:
                    raise StateConflictError(pid)
                try:
                    with connection.begin_nested():
                        connection.execute(
                            insert(op_prospect_states).values(
                                client_id=self._client_id, prospect_id=pid, **values
                            )
                        )
                except IntegrityError:
                    raise StateConflictError(pid) from None

    # ---- the read model of the prospects (a projection, see `op_prospect_index`) -----------

    def index_prospects(self, rows: Sequence[Row]) -> int:
        """Insert or refresh the index rows, only those that changed. Each row holds the columns of
        `op_prospect_index` but `client_id` and `indexed_at`. A row the database refuses (the same
        external id in the same lane under another prospect) is skipped, never raised. Returns the
        number of rows written."""
        wanted = {r["prospect_id"]: r for r in rows}
        if not wanted:
            return 0
        written = 0
        columns = {c.name for c in op_prospect_index.c} - {"client_id", "prospect_id", "indexed_at"}
        with self._write() as connection:
            held: dict[str, Row] = {}
            ids = list(wanted)
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                for r in connection.execute(
                    select(op_prospect_index).where(
                        op_prospect_index.c.client_id == self._client_id,
                        op_prospect_index.c.prospect_id.in_(chunk),
                    )
                ):
                    held[r.prospect_id] = r._mapping
            now = self._now()
            for pid, row in wanted.items():
                values = {name: row[name] for name in columns if name in row}
                current = held.get(pid)
                if current is None:  # the columns the database does not default
                    values.setdefault("display_name", "")
                    values.setdefault("search_text", "")
                    values.setdefault("follow_up_count", 0)
                    values.setdefault("needs_human", False)
                if current is not None and all(
                    current[name] == value for name, value in values.items()
                ):
                    continue
                try:
                    with connection.begin_nested():
                        if current is None:
                            connection.execute(
                                insert(op_prospect_index).values(
                                    client_id=self._client_id,
                                    prospect_id=pid,
                                    indexed_at=now,
                                    **values,
                                )
                            )
                        else:
                            connection.execute(
                                update(op_prospect_index)
                                .where(
                                    op_prospect_index.c.client_id == self._client_id,
                                    op_prospect_index.c.prospect_id == pid,
                                )
                                .values(indexed_at=now, **values)
                            )
                    written += 1
                except IntegrityError:
                    continue
        return written

    # ---- prospects created locally -------------------------------------------------------

    def list_local(self) -> list[Row]:
        query = (
            select(op_local_prospects.c.prospect_id, op_local_prospects.c.document)
            .where(op_local_prospects.c.client_id == self._client_id)
            .order_by(op_local_prospects.c.seq)
        )
        with self._read() as connection:
            return [self._local_row(connection, r) for r in connection.execute(query)]

    def get_local(self, prospect_id: str) -> Row | None:
        query = select(op_local_prospects.c.prospect_id, op_local_prospects.c.document).where(
            op_local_prospects.c.client_id == self._client_id,
            op_local_prospects.c.prospect_id == prospect_id,
        )
        with self._read() as connection:
            row = connection.execute(query).first()
            return None if row is None else self._local_row(connection, row)

    def find_local_by_keys(self, keys: Collection[str]) -> Row | None:
        """The earliest local prospect holding any of these contact keys."""
        wanted = list(keys)
        if not wanted:
            return None
        query = (
            select(op_local_prospects.c.prospect_id, op_local_prospects.c.document)
            .join(
                op_local_contacts,
                (op_local_contacts.c.client_id == op_local_prospects.c.client_id)
                & (op_local_contacts.c.prospect_id == op_local_prospects.c.prospect_id),
            )
            .where(
                op_local_prospects.c.client_id == self._client_id,
                op_local_contacts.c.contact_key.in_(wanted),
            )
            .order_by(op_local_prospects.c.seq)
            .limit(1)
        )
        with self._read() as connection:
            row = connection.execute(query).first()
            return None if row is None else self._local_row(connection, row)

    def create_local(self, rows: Sequence[Row]) -> None:
        """All or nothing. Each row: `prospect_id`, `contact_keys`, `document` and the initial state
        (`state_document`, `conversation_id`), stored at revision 0. A duplicate id or contact is a
        `ValueError`."""
        with self._write() as connection:
            ids: set[str] = set()
            taken: set[str] = set()
            for row in rows:
                keys = set(row["contact_keys"])
                if (
                    row["prospect_id"] in ids
                    or connection.execute(
                        select(op_local_prospects.c.seq).where(
                            op_local_prospects.c.client_id == self._client_id,
                            op_local_prospects.c.prospect_id == row["prospect_id"],
                        )
                    ).first()
                ):
                    raise ValueError("a local prospect with this id already exists")
                if keys & taken or (
                    keys
                    and connection.execute(
                        select(op_local_contacts.c.contact_key).where(
                            op_local_contacts.c.client_id == self._client_id,
                            op_local_contacts.c.contact_key.in_(list(keys)),
                        )
                    ).first()
                ):
                    raise ValueError("the contact is already in use by another local prospect")
                ids.add(row["prospect_id"])
                taken |= keys
            try:
                for row in rows:
                    pid = row["prospect_id"]
                    connection.execute(
                        insert(op_local_prospects).values(
                            client_id=self._client_id, prospect_id=pid, document=row["document"]
                        )
                    )
                    for key in sorted(set(row["contact_keys"])):
                        connection.execute(
                            insert(op_local_contacts).values(
                                client_id=self._client_id, contact_key=key, prospect_id=pid
                            )
                        )
                    connection.execute(
                        insert(op_prospect_states).values(
                            client_id=self._client_id,
                            prospect_id=pid,
                            revision=0,
                            conversation_id=row.get("conversation_id"),
                            document=row["state_document"],
                        )
                    )
            except IntegrityError:
                raise ValueError("the id or the contact is already in use") from None

    def _local_row(self, connection: Connection, row: Any) -> Row:
        state = connection.execute(
            select(op_prospect_states).where(
                op_prospect_states.c.client_id == self._client_id,
                op_prospect_states.c.prospect_id == row.prospect_id,
            )
        ).first()
        return {
            "prospect_id": row.prospect_id,
            "document": row.document,
            "state": None if state is None else _state_row(state),
        }


def _state_row(row: Any) -> Row:
    return {
        "prospect_id": row.prospect_id,
        "revision": row.revision,
        "conversation_id": row.conversation_id,
        "document": row.document,
    }
