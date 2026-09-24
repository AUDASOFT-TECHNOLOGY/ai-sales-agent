"""Which products this conversation is about, from the catalog and what is known of the interest.

Selection is deliberately modest: it never guesses an interest. It narrows the catalog to what may
be offered, ranks by the strength of the known interest, and says how sure it is:

- INTEREST     the prospect has an interest in some offerable product: focus on the strongest tier
- SOLE_OFFER   no interest is known but only one product can be offered: it is the offer
- UNRESOLVED   several products could be offered and nothing says which: discovery decides
- NONE_SUITABLE the catalog has products but none may be offered now
- NO_CATALOG   the client has no products

Choosing the best fit among several remains a judgement the model makes, inside these bounds.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sales_brain_kit.consultative.catalog import ProductCatalog
from sales_brain_kit.consultative.interest import InterestLevel, ProductInterest
from sales_brain_kit.settings.catalog import Presentation, Product
from sales_brain_kit.settings.market import Market


class SelectionBasis(StrEnum):
    INTEREST = "interest"
    SOLE_OFFER = "sole_offer"
    UNRESOLVED = "unresolved"
    NONE_SUITABLE = "none_suitable"
    NO_CATALOG = "no_catalog"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductSelection:
    basis: SelectionBasis
    focus: tuple[Product, ...]  # what this turn should talk about, best first
    candidates: tuple[Product, ...]  # everything offerable, best first (a superset of focus)
    interests: tuple[ProductInterest, ...]  # of offerable products only
    excluded: Mapping[str, str]  # product id -> why it may not be offered

    @property
    def strongest(self) -> InterestLevel | None:
        return self.interests[0].level if self.interests else None

    def interest_in(self, product_id: str) -> InterestLevel | None:
        return next((i.level for i in self.interests if i.product_id == product_id), None)


def select_products(
    catalog: ProductCatalog,
    interests: tuple[ProductInterest, ...],
    *,
    language: str,
    presentation: Presentation,
    market: Market | None = None,
) -> ProductSelection:
    excluded: dict[str, str] = {}
    offerable: dict[str, Product] = {}
    for product in catalog.products:
        reason = catalog.exclusion(product, language, market)
        if reason is None:
            offerable[product.id] = product
        else:
            excluded[product.id] = reason

    live = tuple(i for i in interests if i.product_id in offerable)
    ranked = [offerable[i.product_id] for i in live]
    candidates = (*ranked, *(p for p in offerable.values() if p.id not in {r.id for r in ranked}))

    if not catalog.products:
        basis, focus = SelectionBasis.NO_CATALOG, ()
    elif not offerable:
        basis, focus = SelectionBasis.NONE_SUITABLE, ()
    elif live:
        top = live[0].level
        tier = tuple(offerable[i.product_id] for i in live if i.level is top)
        basis, focus = SelectionBasis.INTEREST, tier[: presentation.max_products_per_message]
    elif len(offerable) == 1:
        basis, focus = SelectionBasis.SOLE_OFFER, candidates
    else:
        basis, focus = SelectionBasis.UNRESOLVED, ()
    return ProductSelection(
        basis=basis, focus=focus, candidates=candidates, interests=live, excluded=excluded
    )
