"""The SQL side of the notifications (the port of `sales_app.notifications`).

`open_or_reopen` is one statement (`INSERT ... ON CONFLICT (dedupe_key) DO UPDATE ... WHERE
resolved_at IS NOT NULL`): a new event makes a row, an open one is left alone, a resolved one is
opened again, and two processes can never make two rows.
"""

from datetime import datetime

from sqlalchemy import Engine, and_, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.notifications import Notification, NotificationQuery, scoped_key
from sales_app.persistence.tables import notifications as table
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "notifications are unavailable"


def _insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - only the two supported backends
        raise StoreUnavailableError(_UNAVAILABLE)
    return insert


def _conditions(query: NotificationQuery) -> list:
    found = [table.c.audience == query.audience]
    if query.client_id is not None:
        found.append(table.c.client_id == query.client_id)
    if query.client_ids is not None:
        found.append(table.c.client_id.in_(sorted(query.client_ids)))
    if query.unread_only:
        found.append(table.c.read_at.is_(None))
    if not query.include_resolved:
        found.append(table.c.resolved_at.is_(None))
    return found


def _notification(row) -> Notification:
    return Notification(
        id=row.id,
        created_at=row.created_at,
        audience=row.audience,
        client_id=row.client_id,
        kind=row.kind,
        severity=row.severity,
        title=row.title,
        message=row.message,
        action=row.suggested_action,
        dedupe_key=row.dedupe_key,
        read_at=row.read_at,
        resolved_at=row.resolved_at,
    )


class SqlNotificationStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlNotificationStore()"

    def open_or_reopen(self, notification: Notification) -> Notification:
        values = {
            "created_at": notification.created_at,
            "audience": notification.audience,
            "client_id": notification.client_id,
            "kind": notification.kind,
            "severity": notification.severity,
            "title": notification.title,
            "message": notification.message,
            "suggested_action": notification.action,
            "dedupe_key": notification.dedupe_key,
            "read_at": None,
            "resolved_at": None,
        }
        try:
            with self._engine.begin() as connection:
                statement = _insert(connection.dialect.name)(table).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[table.c.dedupe_key],
                        set_={
                            key: getattr(statement.excluded, key)
                            for key in (
                                "created_at",
                                "kind",
                                "severity",
                                "title",
                                "message",
                                "suggested_action",
                                "read_at",
                                "resolved_at",
                            )
                        },
                        where=table.c.resolved_at.is_not(None),
                    )
                )
                row = connection.execute(
                    select(table).where(table.c.dedupe_key == notification.dedupe_key)
                ).one()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return _notification(row)

    def search(self, query: NotificationQuery) -> tuple[list[Notification], int]:
        where = and_(*_conditions(query))
        try:
            with self._engine.connect() as connection:
                count = connection.execute(
                    select(func.count()).select_from(table).where(where)
                ).scalar_one()
                rows = connection.execute(
                    select(table)
                    .where(where)
                    .order_by(table.c.created_at.desc(), table.c.id.desc())
                    .limit(query.limit)
                    .offset(query.offset)
                ).all()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return [_notification(row) for row in rows], int(count)

    def unread_count(self, query: NotificationQuery) -> int:
        conditions = _conditions(query) + [table.c.read_at.is_(None), table.c.resolved_at.is_(None)]
        try:
            with self._engine.connect() as connection:
                return int(
                    connection.execute(
                        select(func.count()).select_from(table).where(and_(*conditions))
                    ).scalar_one()
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def mark_read(self, query: NotificationQuery, notification_id: int, at: datetime) -> bool:
        try:
            with self._engine.begin() as connection:
                visible = connection.execute(
                    select(table.c.id).where(and_(table.c.id == notification_id, *_conditions(query)))
                ).first()
                if visible is None:
                    return False
                connection.execute(
                    update(table)
                    .where(table.c.id == notification_id, table.c.read_at.is_(None))
                    .values(read_at=at)
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return True

    def mark_all_read(self, query: NotificationQuery, at: datetime) -> int:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(table)
                    .where(and_(*_conditions(query)), table.c.read_at.is_(None))
                    .values(read_at=at)
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return int(result.rowcount or 0)

    def resolve(self, audience: str, client_id: str | None, dedupe_key: str, at: datetime) -> int:
        key = scoped_key(audience, client_id, dedupe_key)
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(table)
                    .where(table.c.dedupe_key == key, table.c.resolved_at.is_(None))
                    .values(resolved_at=at, read_at=func.coalesce(table.c.read_at, at))
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return int(result.rowcount or 0)
