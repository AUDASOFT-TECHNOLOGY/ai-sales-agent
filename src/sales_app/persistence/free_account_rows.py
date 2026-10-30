"""The SQL half of the free accounts (`sales_app.free_accounts`): one row per client, written once.
A database that cannot be reached is `StoreUnavailableError`, a fixed sentence."""

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.free_accounts import FreeAccount
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import client_free_accounts as accounts
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "free accounts are unavailable"


class SqlFreeAccountStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlFreeAccountStore()"

    def get(self, client_id: str) -> FreeAccount | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(accounts).where(accounts.c.client_id == client_id)
                ).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return None if row is None else FreeAccount(row.client_id, row.credits, row.created_at)

    def add(self, account: FreeAccount) -> None:
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, account.client_id, created_at=account.created_at, created_by=None
                )
                connection.execute(
                    insert(accounts).values(
                        client_id=account.client_id,
                        credits=account.credits,
                        created_at=account.created_at,
                    )
                )
        except IntegrityError:
            return  # already granted: the first record stays
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
