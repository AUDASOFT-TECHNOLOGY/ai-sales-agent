"""The SQL side of the daily report: the client's report settings and the reports kept per day.

The database decides uniqueness: (client, day) is the primary key, so a second process cannot make
a second report; `claim` is one conditional UPDATE, so two senders cannot both take the same report.
An unreachable database is `ReportStoreError`, a fixed sentence.
"""

from collections.abc import Sequence
from datetime import date, datetime, time

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.daily_report import ReportRecord, ReportSettings, ReportStoreError
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import client_report_settings as settings_table
from sales_app.persistence.tables import daily_reports as reports

_UNAVAILABLE = "the daily report store is unavailable"


def _dialect_insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - only the two supported backends
        raise ReportStoreError(_UNAVAILABLE)
    return insert


class SqlReportSettingsStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlReportSettingsStore()"

    def get(self, client_id: str) -> ReportSettings | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(settings_table).where(settings_table.c.client_id == client_id)
                ).first()
        except SQLAlchemyError:
            raise ReportStoreError(_UNAVAILABLE) from None
        if row is None:
            return None
        hour, minute = row.send_time.split(":")
        return ReportSettings(
            client_id=row.client_id,
            enabled=bool(row.enabled),
            channel=row.channel,
            recipient=row.recipient,
            send_time=time(int(hour), int(minute)),
            timezone=row.timezone,
            language=row.language,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )

    def set(self, settings: ReportSettings) -> None:
        values = {
            "enabled": settings.enabled,
            "channel": settings.channel,
            "recipient": settings.recipient,
            "send_time": settings.send_time.strftime("%H:%M"),
            "timezone": settings.timezone,
            "language": settings.language,
            "updated_at": settings.updated_at,
            "updated_by": settings.updated_by,
        }
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection,
                    settings.client_id,
                    created_at=settings.updated_at,
                    created_by=settings.updated_by,
                )
                statement = _dialect_insert(connection.dialect.name)(settings_table).values(
                    client_id=settings.client_id, **values
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[settings_table.c.client_id], set_=values
                    )
                )
        except SQLAlchemyError:
            raise ReportStoreError(_UNAVAILABLE) from None


def _record(row) -> ReportRecord:
    return ReportRecord(
        client_id=row.client_id,
        day=row.report_day,
        window_start=row.window_start,
        window_end=row.window_end,
        timezone=row.timezone,
        generated_at=row.generated_at,
        language=row.language,
        figures=row.figures,
        text=row.body,
        status=row.status,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        error_code=row.error_code,
        channel=row.channel,
        recipient_hint=row.recipient_hint,
        sent_at=row.sent_at,
        updated_at=row.updated_at,
    )


class SqlReportStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlReportStore()"

    def add(self, record: ReportRecord) -> bool:
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, record.client_id, created_at=record.generated_at, created_by=None
                )
                connection.execute(
                    reports.insert().values(
                        client_id=record.client_id,
                        report_day=record.day,
                        window_start=record.window_start,
                        window_end=record.window_end,
                        timezone=record.timezone,
                        generated_at=record.generated_at,
                        language=record.language,
                        figures=dict(record.figures),
                        body=record.text,
                        status=record.status,
                        attempts=record.attempts,
                        next_attempt_at=record.next_attempt_at,
                        error_code=record.error_code,
                        channel=record.channel,
                        recipient_hint=record.recipient_hint,
                        sent_at=record.sent_at,
                        updated_at=record.updated_at or record.generated_at,
                    )
                )
        except IntegrityError:
            return False
        except SQLAlchemyError:
            raise ReportStoreError(_UNAVAILABLE) from None
        return True

    def get(self, client_id: str, day: date) -> ReportRecord | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(reports).where(
                        reports.c.client_id == client_id, reports.c.report_day == day
                    )
                ).first()
        except SQLAlchemyError:
            raise ReportStoreError(_UNAVAILABLE) from None
        return _record(row) if row else None

    def list(self, client_id: str, limit: int) -> list[ReportRecord]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    select(reports)
                    .where(reports.c.client_id == client_id)
                    .order_by(reports.c.report_day.desc())
                    .limit(limit)
                ).all()
        except SQLAlchemyError:
            raise ReportStoreError(_UNAVAILABLE) from None
        return [_record(r) for r in rows]

    def claim(
        self, client_id: str, day: date, allowed: Sequence[str], now: datetime
    ) -> ReportRecord | None:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(reports)
                    .where(
                        reports.c.client_id == client_id,
                        reports.c.report_day == day,
                        reports.c.status.in_(list(allowed)),
                    )
                    .values(status="sending", updated_at=now)
                )
                if result.rowcount != 1:
                    return None
                row = connection.execute(
                    select(reports).where(
                        reports.c.client_id == client_id, reports.c.report_day == day
                    )
                ).first()
        except SQLAlchemyError:
            raise ReportStoreError(_UNAVAILABLE) from None
        return _record(row) if row else None

    def save(self, record: ReportRecord) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    update(reports)
                    .where(
                        reports.c.client_id == record.client_id,
                        reports.c.report_day == record.day,
                    )
                    .values(
                        status=record.status,
                        attempts=record.attempts,
                        next_attempt_at=record.next_attempt_at,
                        error_code=record.error_code,
                        channel=record.channel,
                        recipient_hint=record.recipient_hint,
                        sent_at=record.sent_at,
                        updated_at=record.updated_at or record.generated_at,
                    )
                )
        except SQLAlchemyError:
            raise ReportStoreError(_UNAVAILABLE) from None
