"""Read access to the client's product catalog: zero, one or many products, all from settings."""

from collections.abc import Sequence
from typing import Literal

from sales_brain_kit.consultative.language import localize, match_language
from sales_brain_kit.settings.catalog import CatalogSettings, Product, SourceInterest
from sales_brain_kit.settings.market import Market

MatchBy = Literal["id", "name", "aliases"]


class ProductCatalog:
    """Finds products and says which of them may be offered. Never changes the catalog."""

    def __init__(self, settings: CatalogSettings, default_language: str) -> None:
        self._settings = settings
        self._default_language = default_language
        self._by_id = {product.id: product for product in settings.products}

    @property
    def products(self) -> tuple[Product, ...]:
        return self._settings.products

    @property
    def source_interest(self) -> SourceInterest | None:
        return self._settings.source_interest

    def get(self, product_id: str) -> Product | None:
        return self._by_id.get(product_id)

    def name_of(self, product: Product, language: str | None) -> str:
        return localize(product.name, language, self._default_language) or product.id

    def resolve(
        self, reference: str, match_by: Sequence[MatchBy] = ("id", "name", "aliases")
    ) -> Product | None:
        """The product a text refers to (by id, name or alias, ignoring case), if any."""
        wanted = reference.strip().casefold()
        if not wanted:
            return None
        for product in self._settings.products:
            names: list[str] = []
            if "id" in match_by:
                names.append(product.id)
            if "name" in match_by:
                names += (
                    [product.name] if isinstance(product.name, str) else [*product.name.values()]
                )
            if "aliases" in match_by:
                names += product.aliases
            if any(name.casefold() == wanted for name in names):
                return product
        return None

    def exclusion(
        self, product: Product, language: str | None, market: Market | None = None
    ) -> str | None:
        """Why a product may not be offered now, or None if it may."""
        if product.status == "inactive":
            return "inactive"
        if market is not None and market.catalog is not None:
            scope = market.catalog
            if (scope.only and product.id not in scope.only) or product.id in scope.exclude:
                return f"not offered in market {market.id}"
        allowed = product.restrictions.languages if product.restrictions else ()
        if allowed and language is not None and match_language(language, allowed) is None:
            return f"not offered in {language}"
        return None
