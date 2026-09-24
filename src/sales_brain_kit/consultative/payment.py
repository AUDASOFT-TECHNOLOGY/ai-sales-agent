"""Which payment methods apply to this prospect, and the exact text to give for each.

Selection is deterministic: a method applies when it is active and its scope (product, market,
country, currency) matches what is known; within a method the most specific matching instruction is
used (ties go to the one declared first). Nothing is guessed: a scope that restricts a dimension
whose value is unknown does not match.

Rendering never rewrites configured values. The instruction's own text, each `label: value` line
and the payment URL are put together exactly as the client wrote them, in the message language.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sales_brain_kit.consultative.applicability import Place, specificity
from sales_brain_kit.consultative.language import localize
from sales_brain_kit.settings.payment import PaymentInstruction, PaymentMethod, PaymentSettings


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
) -> tuple[MethodOffer, ...]:
    offers = []
    for method in settings.methods:
        if method.status != "active" or (only and method.id not in only):
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
