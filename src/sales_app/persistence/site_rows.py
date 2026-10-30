"""The SQL half of the public pages (`sales_app.site_content`) and of the offline plan payment
requests (`sales_app.plan_payments`). A database that cannot be reached is `StoreUnavailableError`,
a fixed sentence."""

from datetime import datetime

from sqlalchemy import Engine, delete, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import plan_payment_requests as requests
from sales_app.persistence.tables import site_pages as pages
from sales_app.plan_payments import (
    PENDING,
    PaymentRequest,
    PendingRequestExistsError,
    Receipt,
    RequestNotFoundError,
    RequestNotPendingError,
)
from sales_app.site_content import SitePage
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "this content is unavailable"
_LISTED = [c for c in requests.c if c.name != "receipt_data"]  # the receipt is read apart


class SqlSitePageStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlSitePageStore()"

    @staticmethod
    def _page(row) -> SitePage:
        return SitePage(
            row.page_key, row.locale, row.title, row.body, row.updated_at, row.updated_by
        )

    def get(self, key: str, locale: str) -> SitePage | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(pages).where(pages.c.page_key == key, pages.c.locale == locale)
                ).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return None if row is None else self._page(row)

    def list_all(self) -> list[SitePage]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(select(pages).order_by(pages.c.page_key, pages.c.locale))
                return [self._page(r) for r in rows]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def save(self, page: SitePage) -> None:
        values = {
            "title": page.title,
            "body": page.body,
            "updated_at": page.updated_at,
            "updated_by": page.updated_by,
        }
        try:
            with self._engine.begin() as connection:
                done = connection.execute(
                    update(pages)
                    .where(pages.c.page_key == page.key, pages.c.locale == page.locale)
                    .values(**values)
                )
                if done.rowcount == 0:
                    connection.execute(
                        insert(pages).values(page_key=page.key, locale=page.locale, **values)
                    )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def remove(self, key: str, locale: str) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(pages).where(pages.c.page_key == key, pages.c.locale == locale)
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None


def _request(row) -> PaymentRequest:
    return PaymentRequest(
        request_id=row.request_id,
        client_id=row.client_id,
        plan_id=row.plan_id,
        plan_name=row.plan_name,
        amount=row.amount,
        currency=row.currency,
        reference=row.reference,
        receipt_type=row.receipt_type,
        receipt_size=row.receipt_size,
        status=row.status,
        created_at=row.created_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        reason=row.reason,
    )


class SqlPlanPaymentStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlPlanPaymentStore()"

    def create(self, request: PaymentRequest, receipt: Receipt) -> PaymentRequest:
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, request.client_id, created_at=request.created_at, created_by=None
                )
                result = connection.execute(
                    insert(requests).values(
                        client_id=request.client_id,
                        plan_id=request.plan_id,
                        plan_name=request.plan_name,
                        amount=request.amount,
                        currency=request.currency,
                        reference=request.reference,
                        receipt_type=receipt.content_type,
                        receipt_size=len(receipt.data),
                        receipt_data=receipt.data,
                        status=PENDING,
                        created_at=request.created_at,
                    )
                )
                request_id = result.inserted_primary_key[0]
        except IntegrityError:
            raise PendingRequestExistsError(request.client_id) from None
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        made = self.get(request_id)
        assert made is not None
        return made

    def get(self, request_id: int) -> PaymentRequest | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(*_LISTED).where(requests.c.request_id == request_id)
                ).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return None if row is None else _request(row)

    def list_requests(
        self, *, client_id: str | None = None, status: str | None = None
    ) -> list[PaymentRequest]:
        query = select(*_LISTED).order_by(requests.c.request_id.desc())
        if client_id is not None:
            query = query.where(requests.c.client_id == client_id)
        if status is not None:
            query = query.where(requests.c.status == status)
        try:
            with self._engine.connect() as connection:
                return [_request(r) for r in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def receipt(self, request_id: int) -> Receipt | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(requests.c.receipt_type, requests.c.receipt_data).where(
                        requests.c.request_id == request_id
                    )
                ).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return None if row is None else Receipt(row.receipt_type, bytes(row.receipt_data))

    def decide(
        self, request_id: int, status: str, *, at: datetime, by: str, reason: str | None
    ) -> PaymentRequest:
        try:
            with self._engine.begin() as connection:
                done = connection.execute(
                    update(requests)
                    .where(requests.c.request_id == request_id, requests.c.status == PENDING)
                    .values(status=status, decided_at=at, decided_by=by, reason=reason)
                )
                changed = done.rowcount
                exists = (
                    changed
                    or connection.execute(
                        select(requests.c.request_id).where(requests.c.request_id == request_id)
                    ).first()
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        if not exists:
            raise RequestNotFoundError(request_id)
        if not changed:
            raise RequestNotPendingError(request_id)
        decided = self.get(request_id)
        assert decided is not None
        return decided

    def reopen(self, request_id: int) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    update(requests)
                    .where(requests.c.request_id == request_id)
                    .values(status=PENDING, decided_at=None, decided_by=None, reason=None)
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
