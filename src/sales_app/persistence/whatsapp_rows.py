"""The SQL side of a client's WhatsApp registers (the port of `sales_app.whatsapp_state`).

Windows, delivery statuses and message templates, all per client and every statement filtered on it.
Nothing of a message's content is kept: a window is a phone number and a date, a delivery is Meta's
id for a message and its status. A database that cannot be reached is `StoreUnavailableError`, a
fixed sentence.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine, and_, delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import wa_contact_windows as windows
from sales_app.persistence.tables import wa_deliveries as deliveries
from sales_app.persistence.tables import wa_templates as templates
from sales_app.sourcing.errors import StoreUnavailableError
from sales_app.whatsapp_state import FAILED, DeliveryRecord, TemplateRecord, moves_forward

_UNAVAILABLE = "the WhatsApp register is unavailable"


def _dialect_insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - only the two supported backends
        raise StoreUnavailableError(_UNAVAILABLE)
    return insert


def _delivery(row) -> DeliveryRecord:
    return DeliveryRecord(
        client_id=row.client_id,
        message_id=row.message_id,
        status=row.status,
        at=row.at,
        recipient=row.recipient,
        error_code=row.error_code,
        error_title=row.error_title,
    )


def _template(row) -> TemplateRecord:
    return TemplateRecord(
        client_id=row.client_id,
        name=row.name,
        language=row.language,
        category=row.category,
        status=row.status,
        body=row.body,
        parameter_names=tuple(p for p in row.parameter_names.split(",") if p),
        reason=row.reason,
        meta_id=row.meta_id,
        is_opening=bool(row.is_opening),
        updated_at=row.updated_at,
    )


def _template_values(record: TemplateRecord) -> dict:
    return {
        "client_id": record.client_id,
        "name": record.name,
        "language": record.language,
        "category": record.category[:24],
        "status": record.status[:24],
        "body": record.body[:4096],
        "parameter_names": ",".join(record.parameter_names)[:512],
        "reason": record.reason[:300],
        "meta_id": record.meta_id[:40],
        "is_opening": record.is_opening,
        "updated_at": record.updated_at or datetime.now(UTC),
    }


class SqlWhatsAppState:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlWhatsAppState()"

    # ---- windows ---------------------------------------------------------------------------

    def record_inbound(self, client_id: str, wa_id: str, at: datetime) -> None:
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, client_id, created_at=datetime.now(UTC), created_by=None)
                statement = _dialect_insert(connection.dialect.name)(windows).values(
                    client_id=client_id, wa_id=wa_id, last_inbound_at=at
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[windows.c.client_id, windows.c.wa_id],
                        set_={"last_inbound_at": statement.excluded.last_inbound_at},
                        where=windows.c.last_inbound_at < statement.excluded.last_inbound_at,
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def last_inbound(self, client_id: str, wa_id: str) -> datetime | None:
        query = select(windows.c.last_inbound_at).where(
            windows.c.client_id == client_id, windows.c.wa_id == wa_id
        )
        try:
            with self._engine.connect() as connection:
                return connection.execute(query).scalar()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def close_window(self, client_id: str, wa_id: str) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(windows).where(
                        windows.c.client_id == client_id, windows.c.wa_id == wa_id
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    # ---- deliveries ------------------------------------------------------------------------

    def record_delivery(self, record: DeliveryRecord) -> None:
        values = {
            "client_id": record.client_id,
            "message_id": record.message_id[:128],
            "status": record.status[:16],
            "at": record.at,
            "recipient": record.recipient[:32],
            "error_code": record.error_code[:16],
            "error_title": record.error_title[:200],
        }
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, record.client_id, created_at=datetime.now(UTC), created_by=None
                )
                where = and_(
                    deliveries.c.client_id == record.client_id,
                    deliveries.c.message_id == values["message_id"],
                )
                row = connection.execute(select(deliveries).where(where)).first()
                if row is None:
                    # A concurrent first insert of the same message loses harmlessly.
                    statement = _dialect_insert(connection.dialect.name)(deliveries).values(
                        **values
                    )
                    connection.execute(statement.on_conflict_do_nothing())
                elif moves_forward(_delivery(row), record):
                    connection.execute(update(deliveries).where(where).values(**values))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def delivery(self, client_id: str, message_id: str) -> DeliveryRecord | None:
        return self.deliveries(client_id, [message_id]).get(message_id)

    def deliveries(self, client_id: str, message_ids: Sequence[str]) -> dict[str, DeliveryRecord]:
        if not message_ids:
            return {}
        query = select(deliveries).where(
            deliveries.c.client_id == client_id, deliveries.c.message_id.in_(list(message_ids))
        )
        try:
            with self._engine.connect() as connection:
                return {row.message_id: _delivery(row) for row in connection.execute(query)}
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def delivery_counts(self, client_id: str, since: datetime) -> dict[str, int]:
        query = (
            select(deliveries.c.status, func.count())
            .where(deliveries.c.client_id == client_id, deliveries.c.at >= since)
            .group_by(deliveries.c.status)
        )
        try:
            with self._engine.connect() as connection:
                return {status: int(n) for status, n in connection.execute(query)}
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def recent_failures(self, client_id: str, limit: int = 20) -> Sequence[DeliveryRecord]:
        query = (
            select(deliveries)
            .where(deliveries.c.client_id == client_id, deliveries.c.status == FAILED)
            .order_by(deliveries.c.at.desc())
            .limit(limit)
        )
        try:
            with self._engine.connect() as connection:
                return [_delivery(row) for row in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    # ---- templates -------------------------------------------------------------------------

    def templates(self, client_id: str) -> Sequence[TemplateRecord]:
        query = (
            select(templates)
            .where(templates.c.client_id == client_id)
            .order_by(templates.c.name, templates.c.language)
        )
        try:
            with self._engine.connect() as connection:
                return [_template(row) for row in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def save_template(self, record: TemplateRecord) -> None:
        values = _template_values(record)
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, record.client_id, created_at=datetime.now(UTC), created_by=None
                )
                statement = _dialect_insert(connection.dialect.name)(templates).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            templates.c.client_id,
                            templates.c.name,
                            templates.c.language,
                        ],
                        set_={
                            k: v
                            for k, v in values.items()
                            if k not in ("client_id", "name", "language")
                        },
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def replace_templates(self, client_id: str, records: Sequence[TemplateRecord]) -> None:
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, client_id, created_at=datetime.now(UTC), created_by=None)
                opening = {
                    (r.name, r.language)
                    for r in connection.execute(
                        select(templates).where(
                            templates.c.client_id == client_id, templates.c.is_opening.is_(True)
                        )
                    )
                }
                connection.execute(delete(templates).where(templates.c.client_id == client_id))
                for record in records:
                    keep = (record.name, record.language) in opening and record.sendable
                    connection.execute(
                        templates.insert().values(
                            **_template_values(
                                TemplateRecord(**{**_fields(record), "is_opening": keep})
                            )
                        )
                    )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def delete_template(self, client_id: str, name: str) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(templates).where(
                        templates.c.client_id == client_id, templates.c.name == name
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def set_opening(self, client_id: str, name: str, language: str) -> bool:
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    select(templates).where(
                        templates.c.client_id == client_id,
                        templates.c.name == name,
                        templates.c.language == language,
                    )
                ).first()
                if row is None or not _template(row).sendable:
                    return False
                connection.execute(
                    update(templates)
                    .where(templates.c.client_id == client_id)
                    .values(is_opening=False)
                )
                connection.execute(
                    update(templates)
                    .where(
                        templates.c.client_id == client_id,
                        templates.c.name == name,
                        templates.c.language == language,
                    )
                    .values(is_opening=True)
                )
                return True
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def clear_opening(self, client_id: str) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    update(templates)
                    .where(templates.c.client_id == client_id)
                    .values(is_opening=False)
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def opening_template(self, client_id: str) -> TemplateRecord | None:
        query = select(templates).where(
            templates.c.client_id == client_id, templates.c.is_opening.is_(True)
        )
        try:
            with self._engine.connect() as connection:
                row = connection.execute(query).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        found = _template(row) if row is not None else None
        return found if found is not None and found.sendable else None

    def forget_client(self, client_id: str) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(delete(templates).where(templates.c.client_id == client_id))
                connection.execute(delete(windows).where(windows.c.client_id == client_id))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None


def _fields(record: TemplateRecord) -> dict:
    return {
        "client_id": record.client_id,
        "name": record.name,
        "language": record.language,
        "category": record.category,
        "status": record.status,
        "body": record.body,
        "parameter_names": record.parameter_names,
        "reason": record.reason,
        "meta_id": record.meta_id,
        "is_opening": record.is_opening,
        "updated_at": record.updated_at,
    }
