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
    MANUAL_STATUSES,
    InvoiceStatusError,
    PaymentOrigin,
    PaymentOutcome,
    PaymentRecord,
    PaymentResult,
    check_settle,
    decide,
    discount_100,
    manual_record,
    period_upper_bound,
    parse_reference,
    webhook_record,
)
from sales_app.payment_provider import PaymentEvent
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import invoice_lines as lines_table
from sales_app.persistence.tables import invoices, payment_events
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
        payment_origin=PaymentOrigin(row.payment_origin) if row.payment_origin else None,
        payment_transaction_id=row.payment_transaction_id,
        payment_event_id=row.payment_event_id,
        discount_percent=row.discount_percent or 0,
        amount_after_discount=row.amount_after_discount,
        discount_amount=row.discount_amount,
        free_amount=row.free_amount,
        discount_reason=row.discount_reason,
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
            query = query.where(invoices.c.period <= period_upper_bound(where.period_to))
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

    def settle(
        self,
        invoice_id: int,
        status: InvoiceStatus,
        *,
        at: datetime,
        by: str,
        reason: str | None = None,
    ) -> Invoice:
        try:
            with self._engine.begin() as connection:
                changed = 0
                if status in MANUAL_STATUSES:
                    extra: dict[str, object] = {}
                    if status is InvoiceStatus.DISCOUNTED_100:
                        current = connection.execute(
                            select(invoices).where(invoices.c.invoice_id == invoice_id)
                        ).first()
                        if current is not None:
                            extra = discount_100(_invoice(current, []), reason)
                    changed = connection.execute(
                        update(invoices)
                        .where(
                            invoices.c.invoice_id == invoice_id,
                            invoices.c.status.in_(
                                [InvoiceStatus.PENDING.value, InvoiceStatus.FAILED.value]
                            ),
                        )
                        .values(
                            status=status.value,
                            status_changed_at=at,
                            status_changed_by=by,
                            payment_origin=PaymentOrigin.MANUAL.value,
                            **extra,
                        )
                    ).rowcount
                if changed == 0:
                    row = connection.execute(
                        select(invoices).where(invoices.c.invoice_id == invoice_id)
                    ).first()
                    if row is None:
                        raise InvoiceNotFoundError(invoice_id)
                    check_settle(_invoice(row, []), status)  # says why it is refused
                    raise InvoiceStatusError("the invoice could not be settled")
                row = connection.execute(
                    select(invoices).where(invoices.c.invoice_id == invoice_id)
                ).one()
                _insert_record(connection, manual_record(_invoice(row, []), status, at=at, by=by))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        settled = self.get(invoice_id)
        assert settled is not None
        return settled

    def process_payment(self, event: PaymentEvent, *, at: datetime, by: str) -> PaymentResult:
        try:
            for _ in range(3):  # a race on the event id or the transaction is settled by retrying
                try:
                    with self._engine.begin() as connection:
                        return self._process(connection, event, at=at, by=by)
                except IntegrityError:
                    continue
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        raise StoreUnavailableError(_UNAVAILABLE)

    def _process(
        self, connection: Connection, event: PaymentEvent, *, at: datetime, by: str
    ) -> PaymentResult:
        known = connection.execute(
            select(payment_events.c.invoice_id).where(payment_events.c.event_id == event.event_id)
        ).first()
        if known is not None:
            return PaymentResult(PaymentOutcome.DUPLICATE, self._load(connection, known[0]))
        row = None
        parsed = parse_reference(event.invoice_reference)
        if parsed is not None:
            row = connection.execute(
                select(invoices).where(
                    invoices.c.client_id == parsed[0], invoices.c.period == parsed[1]
                )
            ).first()
        owner = connection.execute(
            select(invoices.c.invoice_id).where(
                invoices.c.payment_transaction_id == event.transaction_id
            )
        ).scalar()
        invoice = _invoice(row, []) if row is not None else None
        outcome = decide(invoice, event, owner)
        if outcome is PaymentOutcome.APPLIED:
            assert invoice is not None
            changed = connection.execute(
                update(invoices)
                .where(
                    invoices.c.invoice_id == invoice.invoice_id,
                    invoices.c.status.in_(
                        [InvoiceStatus.PENDING.value, InvoiceStatus.FAILED.value]
                    ),
                )
                .values(
                    status=InvoiceStatus.PAID.value,
                    status_changed_at=at,
                    status_changed_by=by,
                    payment_origin=PaymentOrigin.ELECTRONIC.value,
                    payment_transaction_id=event.transaction_id,
                    payment_event_id=event.event_id,
                )
            ).rowcount
            if changed == 0:  # settled by someone else a moment ago
                outcome = PaymentOutcome.ALREADY_SETTLED
        elif outcome is PaymentOutcome.FAILURE_RECORDED:
            assert invoice is not None
            connection.execute(
                update(invoices)
                .where(
                    invoices.c.invoice_id == invoice.invoice_id,
                    invoices.c.status == InvoiceStatus.PENDING.value,
                )
                .values(
                    status=InvoiceStatus.FAILED.value, status_changed_at=at, status_changed_by=by
                )
            )
        _insert_record(connection, webhook_record(event, invoice, outcome, at=at, by=by))
        return PaymentResult(
            outcome, self._load(connection, invoice.invoice_id) if invoice else None
        )

    @staticmethod
    def _load(connection: Connection, invoice_id: int | None) -> Invoice | None:
        if invoice_id is None:
            return None
        rows = list(connection.execute(select(invoices).where(invoices.c.invoice_id == invoice_id)))
        found = _with_lines(connection, rows)
        return found[0] if found else None

    def payment_events(self, invoice_id: int | None = None) -> list[PaymentRecord]:
        query = select(payment_events).order_by(
            payment_events.c.received_at.desc(), payment_events.c.event_id.desc()
        )
        if invoice_id is not None:
            query = query.where(payment_events.c.invoice_id == invoice_id)
        try:
            with self._engine.connect() as connection:
                return [PaymentRecord(**row._mapping) for row in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None


def _insert_record(connection: Connection, record: PaymentRecord) -> None:
    connection.execute(
        insert(payment_events).values(
            event_id=record.event_id,
            source=record.source,
            invoice_id=record.invoice_id,
            client_id=record.client_id,
            invoice_reference=record.invoice_reference,
            amount=record.amount,
            currency=record.currency,
            transaction_id=record.transaction_id,
            event_status=record.event_status,
            outcome=record.outcome,
            actor=record.actor,
            received_at=record.received_at,
        )
    )
