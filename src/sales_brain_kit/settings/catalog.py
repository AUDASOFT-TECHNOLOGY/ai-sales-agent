"""The product catalog: any number of products, each with its own commercial rules.

A product is data. Deactivating one sets its status; nothing is ever deleted, because facts and
history refer to product ids.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from sales_brain_kit.settings.common import (
    Currency,
    HttpsUrl,
    Lang,
    LocalizedShort,
    LocalizedText,
    Model,
    ShortText,
    Slug,
    Text,
)
from sales_brain_kit.settings.discovery import BudgetOverride, DiscoveryField
from sales_brain_kit.settings.followup import ProductFollowUp

ProductStatus = Literal["active", "coming_soon", "inactive"]
CtaKind = Literal["request_info", "enrol_link", "request_callback", "notify_me"]
Amount = Annotated[int | float, Field(ge=0, allow_inf_nan=False)]


class Cta(Model):
    """The next step a message should point to."""

    kind: CtaKind = "request_info"
    label: LocalizedShort | None = None
    url: HttpsUrl | None = None

    @model_validator(mode="after")
    def _link_needs_url(self) -> "Cta":
        if self.kind == "enrol_link" and self.url is None:
            raise ValueError("an 'enrol_link' call to action needs a url")
        return self


class Objection(Model):
    code: Slug
    examples: tuple[ShortText, ...] = Field(default=(), max_length=10)  # how it tends to be phrased
    response: Text
    max_rebuttals: int = Field(default=1, ge=0, le=3)
    then: Literal["follow_up", "handoff", "drop"] = "follow_up"


class Availability(Model):
    regions: Literal["all"] | tuple[ShortText, ...] = "all"
    delivery: tuple[Literal["online", "onsite", "hybrid"], ...] = ()
    next_start: date | None = None
    capacity_note: ShortText | None = None


class Pricing(Model):
    model: Literal["fixed", "from", "range", "on_request", "free"] = "on_request"
    amount: Amount | None = None
    max_amount: Amount | None = None
    currency: Currency | None = None
    disclose: Literal["always", "after_interest", "on_request"] = "after_interest"
    note: ShortText | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "Pricing":
        priced = self.model in ("fixed", "from", "range")
        if priced and (self.amount is None or self.currency is None):
            raise ValueError(f"a '{self.model}' price needs 'amount' and 'currency'")
        if not priced and (self.amount is not None or self.max_amount is not None):
            raise ValueError(f"a '{self.model}' price has no amount")
        if self.model == "range":
            if self.max_amount is None or self.amount is None or self.max_amount < self.amount:
                raise ValueError("a 'range' price needs 'max_amount' of at least 'amount'")
        elif self.max_amount is not None:
            raise ValueError("'max_amount' is only for a 'range' price")
        return self


class Audience(Model):
    suited_for: tuple[ShortText, ...] = Field(default=(), max_length=20)
    not_for: tuple[ShortText, ...] = Field(default=(), max_length=20)
    prerequisites: tuple[ShortText, ...] = Field(default=(), max_length=20)


class ProofPoint(Model):
    text: ShortText
    verified: bool = False  # only verified claims may be used


class Value(Model):
    proposition: LocalizedShort | None = None
    benefits: tuple[ShortText, ...] = Field(default=(), max_length=10)
    differentiators: tuple[ShortText, ...] = Field(default=(), max_length=10)
    proof_points: tuple[ProofPoint, ...] = Field(default=(), max_length=10)


class SalesArgument(Model):
    id: Slug
    text: ShortText
    use_when: dict[Slug, tuple[ShortText, ...]] | None = None  # fact key -> values


class Fit(Model):
    id: Slug
    description: ShortText
    fact: Slug


class Disqualifier(Model):
    id: Slug
    description: ShortText
    fact: Slug
    effect: Literal["not_a_fit", "handoff"] = "not_a_fit"


class Qualification(Model):
    fit: tuple[Fit, ...] = Field(default=(), max_length=10)
    disqualifiers: tuple[Disqualifier, ...] = Field(default=(), max_length=10)


class ProductDiscovery(Model):
    extra_fields: tuple[DiscoveryField, ...] = Field(default=(), max_length=20)
    budget: BudgetOverride | None = None


class Strategy(Model):
    cta: Cta | None = None
    angle: ShortText | None = None


class Appointment(Model):
    """MVP: an appointment is a callback scheduled by the agent and carried out by a human."""

    supported: bool = False
    kind: Literal["call", "visit", "demo"] = "call"
    handled_by: Literal["human"] = "human"
    min_notice_hours: int = Field(default=24, ge=0, le=720)
    note: ShortText | None = None


class Restrictions(Model):
    do_not_claim: tuple[ShortText, ...] = Field(default=(), max_length=20)
    do_not_discuss: tuple[ShortText, ...] = Field(default=(), max_length=20)
    requires_human_for: tuple[ShortText, ...] = Field(default=(), max_length=20)
    languages: tuple[Lang, ...] = Field(default=(), max_length=10)  # empty: every language


class Product(Model):
    id: Slug  # immutable once published; facts and history refer to it
    status: ProductStatus = "active"
    name: LocalizedShort
    summary: LocalizedShort
    description: LocalizedText | None = None
    category: ShortText | None = None
    url: HttpsUrl | None = None
    aliases: tuple[ShortText, ...] = Field(default=(), max_length=20)
    tags: tuple[Slug, ...] = Field(default=(), max_length=30)
    availability: Availability | None = None
    pricing: Pricing | None = None
    audience: Audience | None = None
    value: Value | None = None
    sales_arguments: tuple[SalesArgument, ...] = Field(default=(), max_length=20)
    objections: tuple[Objection, ...] = Field(default=(), max_length=20)
    qualification: Qualification | None = None
    discovery: ProductDiscovery | None = None
    strategy: Strategy | None = None
    follow_up: ProductFollowUp | None = None
    appointment: Appointment | None = None
    restrictions: Restrictions | None = None

    @model_validator(mode="after")
    def _status_rules(self) -> "Product":
        cta = self.strategy.cta if self.strategy else None
        if self.status == "coming_soon" and cta is not None and cta.kind == "enrol_link":
            raise ValueError("a 'coming_soon' product cannot use an 'enrol_link' call to action")
        return self


class SourceInterest(Model):
    """Turns a Sheet column such as "Product" into an interest in a catalog product."""

    columns: tuple[ShortText, ...] = Field(min_length=1, max_length=10)
    match_by: tuple[Literal["id", "name", "aliases"], ...] = ("id", "name", "aliases")
    state: Literal["probable", "confirmed"] = (
        "probable"  # a form that asks for it may say confirmed
    )


class Presentation(Model):
    max_products_per_message: int = Field(default=2, ge=1, le=5)
    style: Literal["single_best", "shortlist"] = "shortlist"


class CatalogSettings(Model):
    source_interest: SourceInterest | None = None
    presentation: Presentation = Field(default_factory=Presentation)
    products: tuple[Product, ...] = Field(default=(), max_length=200)
