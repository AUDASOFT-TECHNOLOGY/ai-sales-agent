"""Which payment methods apply to this prospect, and the exact text to give for each.

Selection is deterministic: a method applies when it is active and its scope (product, market,
country, currency) matches what is known; within a method the most specific matching instruction is
used (ties go to the one declared first). Nothing is guessed: a scope that restricts a dimension
whose value is unknown does not match.

Rendering never rewrites configured values. The instruction's own text, each `label: value` line
and the payment URL are put together exactly as the client wrote them, in the message language.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sales_brain_kit.consultative.applicability import Place, specificity
from sales_brain_kit.consultative.language import localize
from sales_brain_kit.consultative.pricing import PriceResolution, PriceStatus, format_number
from sales_brain_kit.settings.catalog import Product
from sales_brain_kit.settings.payment import PaymentInstruction, PaymentMethod, PaymentSettings

# What a prospect may send as a receipt: media of these kinds (a channel's own `type`). Whether it
# really is one, and whether the payment is real, is for a person at the client to check.
RECEIPT_MEDIA = frozenset({"image", "document"})


@dataclass(frozen=True, slots=True, kw_only=True)
class MethodOffer:
    method: PaymentMethod
    instruction: PaymentInstruction


def applicable_methods(
    settings: PaymentSettings,
    *,
    product_id: str | None,
    place: Place,
    currency: str | None,
    only: Sequence[str] = (),
    product: Product | None = None,
) -> tuple[MethodOffer, ...]:
    """The methods that may be offered now. A product that declares its payment options
    (`Product.payment`) offers only the options it enables: a method of a disabled option is never
    offered, so neither it nor its link can reach the prospect."""
    offers = []
    for method in settings.methods:
        if method.status != "active" or (only and method.id not in only):
            continue
        if (
            product is not None
            and product.payment is not None
            and not product.payment.allows(method)
        ):
            continue
        if specificity(method.applies_to, place, product_id=product_id, currency=currency) is None:
            continue
        best: PaymentInstruction | None = None
        best_score = -1
        for instruction in method.instructions:
            score = specificity(
                instruction.applies_to, place, product_id=product_id, currency=currency
            )
            if score is not None and score > best_score:
                best, best_score = instruction, score
        if best is not None:
            offers.append(MethodOffer(method=method, instruction=best))
    return tuple(offers)


def render_instruction(
    instruction: PaymentInstruction, language: str | None, default_language: str | None
) -> str:
    """The instruction as the prospect receives it: configured text, fields and URL, unaltered."""
    lines: list[str] = []
    text = localize(instruction.text, language, default_language)
    if text is not None:
        lines.append(text)
    for field in instruction.fields:
        label = localize(field.label, language, default_language)
        value = localize(field.value, language, default_language)
        lines.append(f"{label}: {value}")
    if instruction.url is not None:
        lines.append(instruction.url)
    return "\n".join(lines)


def receipt_required(product: Product | None, offers: Sequence[MethodOffer]) -> bool:
    """Whether the product asks the prospect for a receipt after paying offline. Only a product that
    declares it does, and only while an offline method is on offer."""
    if product is None or product.payment is None:
        return False
    offline = product.payment.offline
    return (
        offline.enabled
        and offline.receipt_required
        and any(o.method.kind != "online" for o in offers)
    )


# The metadata key under which a method records the currency its buyers pay in.
PAY_CURRENCY = "pay_currency"

_EQUIVALENT = {
    "fr": "Prix : {price}, soit environ {local} au taux du jour.",
    "en": "Price: {price}, about {local} at today's rate.",
}


def local_equivalent(
    price: PriceResolution,
    method: PaymentMethod,
    rates: Mapping[str, float],
    language: str | None,
) -> str | None:
    """The price as configured, then what it comes to in the currency the buyer pays an offline
    method in, at today's rate. None when it does not apply: an online method (the price stays as
    configured), a price that is not an amount, the same currency, or no rate for either currency.
    The amount is worked out here, in code; the model never converts or types it."""
    pay = method.metadata.get(PAY_CURRENCY)
    terms = price.terms
    if (
        method.kind == "online"
        or pay is None
        or terms is None
        or price.status is not PriceStatus.STATED
    ):
        return None
    pay, own = pay.upper(), (terms.currency or "").upper()
    if pay == own or not rates.get(pay) or not rates.get(own):
        return None
    factor = Decimal(str(rates[pay])) / Decimal(str(rates[own]))

    def converted(amount: float) -> str:
        value = (Decimal(str(amount)) * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format_number(float(value))

    assert terms.amount is not None
    match terms.model:
        case "from":
            local = f"{converted(terms.amount)}+ {pay}"
        case "range":
            assert terms.max_amount is not None
            local = f"{converted(terms.amount)}–{converted(terms.max_amount)} {pay}"
        case _:
            local = f"{converted(terms.amount)} {pay}"
    template = _EQUIVALENT.get((language or "en").split("-")[0].lower(), _EQUIVALENT["en"])
    return template.format(price=price.text, local=local)
