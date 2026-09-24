"""Does a configured scope apply to this prospect, and how specifically?

A scope names markets, countries, products and currencies the client declared; an empty dimension
means "any". A dimension the scope restricts only matches when the prospect's value is *known*: an
unknown market never matches a rule written for one (nothing is assumed). Among several matching
rules the most specific wins, and ties go to the one declared first, so the choice is deterministic.
"""

from dataclasses import dataclass

from sales_brain_kit.settings.scope import Applicability, MarketScope

# What makes a match more specific: a country is narrower than a market, which is narrower than
# a product or a currency.
_COUNTRY, _MARKET, _PRODUCT, _CURRENCY = 8, 4, 2, 1


def normalize_country(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass(frozen=True, slots=True)
class Place:
    """Where the prospect is, as far as trusted configuration and facts say. Both may be unknown."""

    market_id: str | None = None
    country: str | None = None  # normalised (see `normalize_country`)


def specificity(
    scope: MarketScope | Applicability,
    place: Place,
    *,
    product_id: str | None = None,
    currency: str | None = None,
) -> int | None:
    """How specifically `scope` applies (higher is narrower), or None when it does not apply."""
    score = 0
    if scope.markets:
        if place.market_id is None or place.market_id not in scope.markets:
            return None
        score += _MARKET
    if scope.countries:
        countries = {normalize_country(c) for c in scope.countries}
        if place.country is None or place.country not in countries:
            return None
        score += _COUNTRY
    products = getattr(scope, "products", ())
    if products:
        if product_id is None or product_id not in products:
            return None
        score += _PRODUCT
    currencies = getattr(scope, "currencies", ())
    if currencies:
        if currency is None or currency not in currencies:
            return None
        score += _CURRENCY
    return score
