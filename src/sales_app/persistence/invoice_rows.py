"""The SQL half of the monthly invoices (`sales_app.billing`).

An invoice and its lines are written in one transaction (the client is registered first, so the
foreign key holds): all or nothing. The database refuses a second invoice for a client and period,
and a settled invoice is never updated again (the `UPDATE` itself requires `PENDING`), so two racing
requests cannot both settle it. A database that cannot be reached is `StoreUnavailableError`, a fixed
sentence.
"""

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.billing import (
    Invoice,
    InvoiceFilter,
    InvoiceLine,
    InvoiceNotFoundError,
    InvoiceStatus,
    InvoiceStatusError,
    check_settle,
)
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import invoice_lines as lines_table
from sales_app.persistence.tables import invoices
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the invoices are unavailable"


def _line(row) -> InvoiceLine:
    return InvoiceLine(
        plan_id=row.plan_id,
        plan_name=row.plan_name,
        monthly_price=row.monthly_price,
        currency=row.currency,
        included_credits=row.included_credits,
        daily_quota=row.daily_quota,
        days=row.days,
        days_in_period=row.days_in_period,
        amount=row.amount,
    )


def _invoice(row, lines) -> Invoice:
    return Invoice(
        invoice_id=row.invoice_id,
        client_id=row.client_id,
        period=row.period,
        currency=row.currency,
        amount=row.amount,
        status=InvoiceStatus(row.status),
        lines=tuple(lines),
        created_at=row.created_at,
        created_by=row.created_by,
        status_changed_at=row.status_changed_at,
        status_changed_by=row.status_changed_by,
    )


def _with_lines(connection: Connection, rows) -> list[Invoice]:
    ids = [r.invoice_id for r in rows]
    by_invoice: dict[int, list[InvoiceLine]] = {i: [] for i in ids}
    if ids:
        query = (
            select(lines_table)
            .where(lines_table.c.invoice_id.in_(ids))
            .order_by(lines_table.c.line_id)
        )
        for r in connection.execute(query):
            by_invoice[r.invoice_id].append(_line(r))
    return [_invoice(r, by_invoice[r.invoice_id]) for r in rows]


class SqlInvoiceStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, invoice: Invoice) -> Invoice | None:
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, invoice.client_id, created_at=datetime.now(UTC), created_by=None
                )
                known = connection.execute(
                    select(invoices.c.invoice_id).where(
                        invoices.c.client_id == invoice.client_id,
                        invoices.c.period == invoice.period,
                    )
                ).first()
                if known is not None:
                    return None
                result = connection.execute(
                    insert(invoices).values(
                        client_id=invoice.client_id,
                        period=invoice.period,
                        currency=invoice.currency,
                        amount=invoice.amount,
                        status=invoice.status.value,
                        created_at=invoice.created_at,
                        created_by=invoice.created_by,
                    )
                )
                invoice_id = result.inserted_primary_key[0]
                for line in invoice.lines:
                    connection.execute(
                        insert(lines_table).values(
                            invoice_id=invoice_id,
                            plan_id=line.plan_id,
                            plan_name=line.plan_name,
                            monthly_price=line.monthly_price,
                            currency=line.currency,
                            included_credits=line.included_credits,
                            daily_quota=line.daily_quota,
                            days=line.days,
                            days_in_period=line.days_in_period,
                            amount=line.amount,
                        )
                    )
        except IntegrityError:  # another request created it first: it stays as it is
            return None
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return replace(invoice, invoice_id=invoice_id)

    def list_invoices(self, where: InvoiceFilter) -> list[Invoice]:
        query = select(invoices)
        if where.client_ids is not None:
            query = query.where(invoices.c.client_id.in_(list(where.client_ids)))
        if where.client_id is not None:
            query = query.where(invoices.c.client_id == where.client_id)
        if where.period_from is not None:
            query = query.where(invoices.c.period >= where.period_from)
        if where.period_to is not None:
            query = query.where(invoices.c.period <= where.period_to)
        if where.status is not None:
            query = query.where(invoices.c.status == where.status.value)
        query = query.order_by(invoices.c.period.desc(), invoices.c.client_id)
        try:
            with self._engine.connect() as connection:
                return _with_lines(connection, list(connection.execute(query)))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def get(self, invoice_id: int) -> Invoice | None:
        try:
            with self._engine.connect() as connection:
                rows = list(
                    connection.execute(select(invoices).where(invoices.c.invoice_id == invoice_id))
                )
                found = _with_lines(connection, rows)
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return found[0] if found else None

    def settle(self, invoice_id: int, status: InvoiceStatus, *, at: datetime, by: str) -> Invoice:
        try:
            with self._engine.begin() as connection:
                changed = 0
                if status is not InvoiceStatus.PENDING:  # PENDING is never a target
                    changed = connection.execute(
                        update(invoices)
                        .where(
                            invoices.c.invoice_id == invoice_id,
                            invoices.c.status == InvoiceStatus.PENDING.value,
                        )
                        .values(status=status.value, status_changed_at=at, status_changed_by=by)
                    ).rowcount
                if changed == 0:
                    row = connection.execute(
                        select(invoices).where(invoices.c.invoice_id == invoice_id)
                    ).first()
                    if row is None:
                        raise InvoiceNotFoundError(invoice_id)
                    check_settle(_invoice(row, []), status)  # says why it is refused
                    raise InvoiceStatusError("the invoice could not be settled")
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        settled = self.get(invoice_id)
        assert settled is not None
        return settled
