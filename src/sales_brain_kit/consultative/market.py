"""The prospect's market: from a *trusted, explicit* country of residence and the client's own rules.

Nothing is inferred. The country is read from one configured fact (`markets.country_fact`) and only
when that fact is settled (never a hypothesis) and comes from a source the client trusts: the
prospect said it, the client's own lead data holds it, a person or the platform recorded it, or it
was confirmed. It is never taken from a name, a phone number, a locale, an accent or a model's
intuition, and it is the country of residence, never a nationality.
"""

from dataclasses import dataclass

from sales_brain_kit.consultative.applicability import Place, normalize_country
from sales_brain_kit.consultative.facts import KnownFact, KnownFacts
from sales_brain_kit.settings.market import Market, MarketSettings
from sales_core.contracts import FactStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketContext:
    market: Market | None  # the client's market this country belongs to, if any
    country: str | None  # the country as known (as recorded), None when it is not known
    reason: str  # why, for the audit trail

    @property
    def place(self) -> Place:
        return Place(
            market_id=self.market.id if self.market else None,
            country=normalize_country(self.country) if self.country else None,
        )


def resolve_market(facts: KnownFacts, settings: MarketSettings) -> MarketContext:
    fact = facts.settled(settings.country_fact)
    if fact is None:
        return MarketContext(market=None, country=None, reason="the country is not known")
    if not _trusted(fact, settings):
        return MarketContext(
            market=None,
            country=None,
            reason=f"the country is not from a trusted source ({fact.source or 'unknown source'})",
        )
    wanted = normalize_country(fact.value)
    for market in settings.markets:
        if any(normalize_country(country) == wanted for country in market.countries):
            return MarketContext(
                market=market, country=fact.value.strip(), reason=f"the country is in {market.id}"
            )
    return MarketContext(
        market=None, country=fact.value.strip(), reason="the country is in no configured market"
    )


def _trusted(fact: KnownFact, settings: MarketSettings) -> bool:
    if fact.status is FactStatus.CONFIRMED:
        return True
    if fact.lead_data:
        return "client_data" in settings.trusted_sources
    return fact.source is not None and fact.source.value in settings.trusted_sources
