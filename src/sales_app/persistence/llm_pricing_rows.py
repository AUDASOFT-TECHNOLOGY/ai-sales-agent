"""The SQL price list for LLM calls (`sales_app.llm_pricing`).

Global: it is Revansa's, not a client's. A database that cannot be reached is
`StoreUnavailableError`, a fixed sentence.
"""

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.llm_pricing import LLMPrice
from sales_app.persistence.tables import llm_pricing as pricing
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the LLM price list is unavailable"


class SqlLLMPricingStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_prices(self) -> list[LLMPrice]:
        query = select(pricing).order_by(pricing.c.provider, pricing.c.model)
        try:
            with self._engine.connect() as connection:
                return [
                    LLMPrice(
                        provider=r.provider,
                        model=r.model,
                        input_per_million=r.input_per_million,
                        output_per_million=r.output_per_million,
                        currency=r.currency,
                        updated_at=r.updated_at,
                        updated_by=r.updated_by,
                    )
                    for r in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def set_price(self, price: LLMPrice) -> None:
        values = {
            "input_per_million": price.input_per_million,
            "output_per_million": price.output_per_million,
            "currency": price.currency,
            "updated_at": price.updated_at,
            "updated_by": price.updated_by,
        }
        try:
            with self._engine.begin() as connection:
                changed = connection.execute(
                    update(pricing)
                    .where(pricing.c.provider == price.provider, pricing.c.model == price.model)
                    .values(**values)
                )
                if changed.rowcount == 0:
                    connection.execute(
                        pricing.insert().values(
                            provider=price.provider, model=price.model, **values
                        )
                    )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def delete_price(self, provider: str, model: str) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    delete(pricing).where(pricing.c.provider == provider, pricing.c.model == model)
                )
                return result.rowcount > 0
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
