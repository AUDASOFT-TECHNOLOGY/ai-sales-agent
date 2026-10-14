"""The products a request may talk about: the catalog, narrowed to the lane of the prospect.

The narrowing is done once, on the settings the whole context is built from, so the prompt, the
response schema, the selection and the validation of the model's answer all see the same products
and a product outside the lane does not exist for any of them. The model is never trusted to stay
inside the scope: a product it names that is outside is refused by validation like an unknown one.

A lane without product ids offers the catalog as it is when the Brain is asked (so a product added
later is offered at once). A lane that lists a product the catalog no longer has is a configuration
that is wrong: it is refused, never silently reduced to the products that remain.
"""

from sales_brain_kit.consultative.errors import LaneScopeError
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_core.contracts import LaneRoute


def scope_to_lane(
    settings: ConsultativeSalesSettings, lane: LaneRoute | None
) -> ConsultativeSalesSettings:
    if lane is None or lane.offers_whole_catalog:
        return settings
    known = {product.id for product in settings.catalog.products}
    missing = [product_id for product_id in lane.product_ids if product_id not in known]
    if missing:
        raise LaneScopeError(lane.lane_id, missing)
    allowed = set(lane.product_ids)
    products = tuple(p for p in settings.catalog.products if p.id in allowed)
    catalog = settings.catalog.model_copy(update={"products": products})
    return settings.model_copy(update={"catalog": catalog})
