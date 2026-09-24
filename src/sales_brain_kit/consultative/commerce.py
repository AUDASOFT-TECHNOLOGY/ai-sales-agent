"""The exact values that may be given to the prospect in this message, and what each stands for.

Built from the commercial context alone, and used twice so the two can never disagree: the prompt
lists the *placeholders* that are available (never the values), and validation expands the
placeholders the model used from the very same table.

- `price:<product>`    a configured price that applies to this prospect, for a product that may
                       be offered
- `payment:<method>`   the instruction of a payment method that applies, while the current
                       workflow step is a payment step that may proceed
- `booking:<step>`     the booking link, while the current step is an appointment step that uses
                       a booking link
"""

from dataclasses import dataclass

from sales_brain_kit.consultative.context import CommercialContext
from sales_brain_kit.consultative.exact import Placeholder
from sales_brain_kit.consultative.language import localize
from sales_brain_kit.consultative.payment import render_instruction
from sales_brain_kit.consultative.pricing import PriceResolution, resolve_price
from sales_brain_kit.consultative.workflow import WorkflowStatus
from sales_brain_kit.settings.catalog import Product
from sales_brain_kit.settings.conversion import PAYMENT_KINDS


@dataclass(frozen=True, slots=True)
class ExactValue:
    placeholder: Placeholder
    value: str  # exact: goes to the prospect unchanged, never to the model
    describes: str  # what it is, safe to show the model


def price_of(context: CommercialContext, product: Product) -> PriceResolution:
    return resolve_price(product, context.market.place)


def exact_values(context: CommercialContext, language: str) -> dict[Placeholder, ExactValue]:
    values: dict[Placeholder, ExactValue] = {}
    default = context.settings.communication.languages.default

    for product in context.selection.candidates:
        text = price_of(context, product).text
        if text is not None:
            placeholder = Placeholder("price", product.id)
            name = context.catalog.name_of(product, language)
            values[placeholder] = ExactValue(placeholder, text, f"the price of {name}")

    state = context.plan.workflow
    if state is None or state.current is None or state.forced:
        return values
    if state.status not in (WorkflowStatus.IN_PROGRESS, WorkflowStatus.BLOCKED):
        return values
    step = state.current
    if step.kind in PAYMENT_KINDS:
        for offer in state.methods:
            placeholder = Placeholder("payment", offer.method.id)
            label = localize(offer.method.label, language, default) or offer.method.id
            values[placeholder] = ExactValue(
                placeholder,
                render_instruction(offer.instruction, language, default),
                f"the payment instructions for {label}",
            )
    appointment = state.appointment
    if appointment is not None and appointment.mode == "booking_url" and appointment.booking_url:
        placeholder = Placeholder("booking", step.id)
        values[placeholder] = ExactValue(placeholder, appointment.booking_url, "the booking link")
    return values
