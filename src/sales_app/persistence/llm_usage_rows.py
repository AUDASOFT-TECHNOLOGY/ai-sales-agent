"""The SQL LLM usage register of one client (`sales_app.llm_usage`).

Bound to one client: every statement filters on it. Each `record` is one transaction that first
registers the client (idempotent), so the foreign key holds. A database that cannot be reached is
`StoreUnavailableError`, a fixed sentence.
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import SQLAlchemyError

from sales_app.llm_usage import LLMUsageRecord
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import op_llm_usage as usage
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the LLM usage register is unavailable"


class SqlLLMUsageStore:
    def __init__(self, engine: Engine, client_id: str) -> None:
        if not client_id:
            raise ValueError("a usage store needs a client id")
        self._engine = engine
        self._client_id = client_id

    def __repr__(self) -> str:
        return f"SqlLLMUsageStore(client={self._client_id!r})"

    def record(self, record: LLMUsageRecord) -> None:
        if record.client_id != self._client_id:
            raise ValueError("a usage record belongs to another client")
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, self._client_id, created_at=datetime.now(UTC), created_by=None
                )
                connection.execute(
                    insert(usage).values(
                        client_id=self._client_id,
                        occurred_at=record.occurred_at,
                        provider=record.provider,
                        model=record.model,
                        input_tokens=record.input_tokens,
                        output_tokens=record.output_tokens,
                        status=record.status,
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def list_usage(self) -> list[LLMUsageRecord]:
        query = select(usage).where(usage.c.client_id == self._client_id).order_by(usage.c.seq)
        try:
            with self._engine.connect() as connection:
                return [
                    LLMUsageRecord(
                        client_id=r.client_id,
                        occurred_at=r.occurred_at,
                        provider=r.provider,
                        model=r.model,
                        status=r.status,
                        input_tokens=r.input_tokens,
                        output_tokens=r.output_tokens,
                    )
                    for r in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
