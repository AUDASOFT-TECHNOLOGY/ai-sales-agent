"""Whether a prospect's payment was confirmed by a person authorised by the client.

One question, for the parts of the Core that must not name payments (the Engine, the learning from
results): only a valid confirmation counts, a claim or a receipt never does.
"""

from collections.abc import Iterable

from sales_core.contracts import Fact, PaymentProgress, payment_progress


def has_settled(facts: Iterable[Fact]) -> bool:
    return payment_progress(facts) is PaymentProgress.PAID
