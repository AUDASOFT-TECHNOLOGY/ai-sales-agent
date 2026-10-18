"""Reading a prospect's payment out of its recorded facts, for the person who has to check it.

Pure: facts in, a view out. Nothing here records or changes anything. What it shows is exactly what
the facts say (see `sales_core.contracts.payment`): a claim or a receipt is only what the prospect said
or sent; "paid" exists only with a valid confirmation, and then the terms are those the person entered.
"""

from collections.abc import Iterable
from datetime import datetime

from sales_core.contracts import (
    PAYMENT_AMOUNT_KEY,
    PAYMENT_CURRENCY_KEY,
    PAYMENT_REFERENCE_KEY,
    UNVERIFIED_CLAIM_KEYS,
    Fact,
    FactStatus,
    PaymentCase,
    PaymentProgress,
    ProspectId,
    is_payment_confirmation,
    payment_progress,
)

_ACTOR_PREFIX = "human:"
_SEPARATOR = "|confirmation:"


def _actor_of(recorded_by: str | None) -> str | None:
    if recorded_by is None or not recorded_by.startswith(_ACTOR_PREFIX):
        return None
    return recorded_by[len(_ACTOR_PREFIX) :].split(_SEPARATOR, 1)[0] or None


def payment_case(
    prospect_id: ProspectId, name: str | None, facts: Iterable[Fact]
) -> PaymentCase | None:
    """The prospect's payment case, or None when nothing was said about a payment."""
    known = list(facts)
    progress = payment_progress(known)
    if progress is PaymentProgress.NONE:
        return None
    signals = [
        f.recorded_at
        for f in known
        if f.key in UNVERIFIED_CLAIM_KEYS
        and f.status is not FactStatus.HYPOTHESIS
        and f.recorded_at
    ]
    signalled_at: datetime | None = max(signals) if signals else None
    if progress is not PaymentProgress.PAID:
        return PaymentCase(
            prospect_id=prospect_id,
            prospect_name=name,
            progress=progress,
            signalled_at=signalled_at,
        )
    confirmations = [f for f in known if is_payment_confirmation(f) and f.recorded_at]
    latest = max(confirmations, key=lambda f: f.recorded_at)  # type: ignore[arg-type,return-value]
    ref = latest.evidence.recorded_by if latest.evidence is not None else None
    terms = {
        f.key: f.value
        for f in known
        if ref is not None and f.evidence is not None and f.evidence.recorded_by == ref
    }
    return PaymentCase(
        prospect_id=prospect_id,
        prospect_name=name,
        progress=progress,
        signalled_at=signalled_at,
        confirmed_at=latest.recorded_at,
        confirmed_by=_actor_of(ref),
        amount=terms.get(PAYMENT_AMOUNT_KEY),
        currency=terms.get(PAYMENT_CURRENCY_KEY),
        reference=terms.get(PAYMENT_REFERENCE_KEY),
    )
