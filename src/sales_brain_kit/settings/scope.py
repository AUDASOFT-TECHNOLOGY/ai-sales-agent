"""Where a rule applies: by market, country, product and currency.

A scope narrows a rule without naming any client or place: every value in it is the client's own
data (a market id it declared, a country as its data spells it, a product id, a currency code).
An empty dimension means "any". Matching is done by the runtime (`consultative/applicability.py`).
"""

from pydantic import Field

from sales_brain_kit.settings.common import Currency, Model, ShortText, Slug


class MarketScope(Model):
    """Narrows by where the prospect is, as far as the client's own configuration knows."""

    markets: tuple[Slug, ...] = Field(default=(), max_length=20)
    countries: tuple[ShortText, ...] = Field(default=(), max_length=50)  # as the client writes them

    @property
    def restricts_place(self) -> bool:
        return bool(self.markets or self.countries)


class Applicability(MarketScope):
    """A market scope that can also narrow by product and by the currency of the price in play."""

    products: tuple[Slug, ...] = Field(default=(), max_length=50)
    currencies: tuple[Currency, ...] = Field(default=(), max_length=10)

    @property
    def restricts(self) -> bool:
        return bool(self.restricts_place or self.products or self.currencies)
