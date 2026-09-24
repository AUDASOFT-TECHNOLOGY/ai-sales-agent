"""Markets: client-defined groups of countries with their own language, style, catalog and cadence.

A market is data. It exists because a client wrote it, and it applies to a prospect only when the
prospect's *country of residence* is known from a trusted, explicit source and is one the market
lists. Nothing is ever inferred: not from a name, a phone number, a locale or an accent, and the
country is never taken for nationality. Which fact holds the country, and which sources are trusted,
are the client's own settings.
"""

from typing import Literal

from pydantic import Field, model_validator

from sales_brain_kit.settings.common import Lang, LocalizedShort, Model, ShortText, Slug
from sales_brain_kit.settings.followup import ProductFollowUp

# Where a fact may come from and still be trusted to place a prospect in a market. A guess made by
# a Brain (`brain_inference`) is never one of them.
TrustedSource = Literal["prospect", "client_data", "human", "system", "conversation"]
DEFAULT_TRUSTED: tuple[TrustedSource, ...] = ("prospect", "client_data", "human", "system")


class MarketLanguages(Model):
    allowed: tuple[Lang, ...] = Field(default=(), max_length=10)  # empty: every supported language
    default: Lang | None = None

    @model_validator(mode="after")
    def _default_is_allowed(self) -> "MarketLanguages":
        if self.default is not None and self.allowed and self.default not in self.allowed:
            raise ValueError("the market's default language must be one of 'allowed'")
        if len(set(self.allowed)) != len(self.allowed):
            raise ValueError("'allowed' lists a language twice")
        return self


class MarketCatalog(Model):
    """Which products the market offers: only these, or all except these."""

    only: tuple[Slug, ...] = Field(default=(), max_length=200)
    exclude: tuple[Slug, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def _not_both_ways(self) -> "MarketCatalog":
        if set(self.only) & set(self.exclude):
            raise ValueError("a product cannot be both in 'only' and in 'exclude'")
        return self


class Market(Model):
    id: Slug
    label: ShortText | None = None
    countries: tuple[ShortText, ...] = Field(min_length=1, max_length=50)
    languages: MarketLanguages | None = None
    style: LocalizedShort | None = None  # the preferred communication style here
    catalog: MarketCatalog | None = None
    workflow: Slug | None = None  # the conversion workflow used in this market
    follow_up: ProductFollowUp | None = None  # cadence overrides, by situation


class MarketSettings(Model):
    country_fact: Slug = "country"  # the discovery field that holds the country of residence
    trusted_sources: tuple[TrustedSource, ...] = DEFAULT_TRUSTED
    markets: tuple[Market, ...] = Field(default=(), max_length=50)
