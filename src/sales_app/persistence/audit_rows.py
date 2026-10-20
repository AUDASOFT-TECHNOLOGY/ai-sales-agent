"""The SQL side of the central audit log (the port of `sales_app.audit_log`). Append and read only:
nothing here updates or deletes an entry."""

from sqlalchemy import Engine, and_, func, insert, select, true
from sqlalchemy.exc import SQLAlchemyError

from sales_app.audit_log import AuditEntry, AuditQuery
from sales_app.persistence.tables import audit_log as table
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the audit log is unavailable"


def _conditions(query: AuditQuery) -> list:
    found = []
    if query.client_ids is not None:
        found.append(table.c.client_id.in_(sorted(query.client_ids)))
    if query.client_id is not None:
        found.append(table.c.client_id == query.client_id)
    if query.actor is not None:
        found.append(table.c.actor == query.actor)
    if query.action is not None:
        if query.action.endswith("*"):
            prefix = query.action[:-1].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            found.append(table.c.action.like(prefix + "%", escape="\\"))
        else:
            found.append(table.c.action == query.action)
    if query.since is not None:
        found.append(table.c.at >= query.since)
    if query.until is not None:
        found.append(table.c.at < query.until)
    return found


class SqlAuditLog:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlAuditLog()"

    def append(self, entry: AuditEntry) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(table).values(
                        at=entry.at,
                        actor=entry.actor,
                        actor_role=entry.actor_role,
                        client_id=entry.client_id,
                        action=entry.action,
                        target=entry.target[:256],
                        outcome=entry.outcome,
                        before=entry.before,
                        after=entry.after,
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def search(self, query: AuditQuery) -> tuple[list[AuditEntry], int]:
        where = and_(true(), *_conditions(query))
        try:
            with self._engine.connect() as connection:
                total = connection.execute(select(func.count()).select_from(table).where(where))
                rows = connection.execute(
                    select(table)
                    .where(where)
                    .order_by(table.c.at.desc(), table.c.id.desc())
                    .limit(query.limit)
                    .offset(query.offset)
                ).all()
                count = int(total.scalar_one())
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return [
            AuditEntry(
                id=row.id,
                at=row.at,
                actor=row.actor,
                actor_role=row.actor_role,
                client_id=row.client_id,
                action=row.action,
                target=row.target,
                outcome=row.outcome,
                before=row.before,
                after=row.after,
            )
            for row in rows
        ], count
