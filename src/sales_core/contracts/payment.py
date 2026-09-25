"""What the Core knows about a payment, and who is allowed to say that it happened.

Three different things must never be confused:

- a **claim**: the prospect says they paid (`payment_claimed`);
- a **proof**: the prospect sent something that may be a receipt (`payment_proof_received`);
- a **confirmation**: a person authorised by the client checked the payment and confirmed it
  (`core_payment_confirmed`).

Claims and proofs are things a prospect said or sent. A Brain may record them (as *discovered*
facts, never confirmed: see `UNVERIFIED_CLAIM_KEYS`). A confirmation is different in kind: only the
Core writes it, only from a `PaymentConfirmation` made by an authorised person, with that person's
identity in the evidence. A Brain, a prospect, a message, a stage or an external system cannot
produce one, and a claim or a proof never turns into one. "Paid" is derived, never stored: it is
`PaymentProgress.PAID`, and exists only while a valid confirmation does (see `payment_progress`).

`PaymentConfirmation` is the whole trust boundary. Whatever builds one (a CRM screen, an admin
tool) is asserting that the person was authenticated and holds the permission to confirm payments;
the Core checks the permission is present, and records who confirmed.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sales_core.contracts._validation import require_aware, require_text
from sales_core.contracts.enums import FactSource, FactStatus
from sales_core.contracts.fact import CORE_FACT_KEY_PREFIX, Fact
from sales_core.contracts.ids import ClientId, MessageId, ProspectId

PAYMENT_CLAIMED_KEY = "payment_claimed"  # the prospect says they paid
PAYMENT_PROOF_KEY = "payment_proof_received"  # the prospect sent something that may be a receipt
PAYMENT_CONFIRMED_KEY = f"{CORE_FACT_KEY_PREFIX}payment_confirmed"  # a person verified the payment
PAYMENT_REFERENCE_KEY = f"{CORE_FACT_KEY_PREFIX}payment_reference"
PAYMENT_AMOUNT_KEY = f"{CORE_FACT_KEY_PREFIX}payment_amount"
PAYMENT_CURRENCY_KEY = f"{CORE_FACT_KEY_PREFIX}payment_currency"
CONFIRMED_VALUE = "confirmed"  # the value of PAYMENT_CONFIRMED_KEY
CONFIRMATION_KEYS = frozenset(
    {PAYMENT_CONFIRMED_KEY, PAYMENT_REFERENCE_KEY, PAYMENT_AMOUNT_KEY, PAYMENT_CURRENCY_KEY}
)

# Facts that record what someone said or sent, never what was verified. A Brain may propose them
# only as discovered, anchored to the prospect's own message.
UNVERIFIED_CLAIM_KEYS = frozenset({PAYMENT_CLAIMED_KEY, PAYMENT_PROOF_KEY})

# The only sources a confirmation can come from: a person, or the client's own records. Never the
# prospect, the conversation, a Brain, or the system acting on its own.
CONFIRMING_SOURCES = frozenset({FactSource.HUMAN, FactSource.CLIENT_DATA})

CONFIRM_PAYMENTS = "payments.confirm"  # the permission a person needs to confirm a payment

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_TEXT = 200


class PaymentProgress(StrEnum):
    """Where a prospect's payment stands, derived from facts. Only `PAID` is ever verified."""

    NONE = "none"
    CLAIMED = "claimed"  # the prospect says they paid
    PROOF_RECEIVED = "proof_received"  # a possible receipt arrived
    PAID = "paid"  # a person authorised by the client confirmed the payment


def is_payment_confirmation(fact: Fact) -> bool:
    """Whether this fact is a valid confirmation: the confirmation key, confirmed, from a person
    or the client's own records. Nothing else (a system, the prospect, a Brain) counts."""
    return (
        fact.key == PAYMENT_CONFIRMED_KEY
        and fact.status is FactStatus.CONFIRMED
        and fact.evidence is not None
        and fact.evidence.source in CONFIRMING_SOURCES
    )


def payment_progress(facts: Iterable[Fact]) -> PaymentProgress:
    """`PAID` only from a valid confirmation; otherwise how far the prospect's own claim and proof
    go. A claim or proof never yields `PAID`, however it was recorded."""
    claimed = proof = False
    for fact in facts:
        if is_payment_confirmation(fact):
            return PaymentProgress.PAID
        if fact.status is FactStatus.HYPOTHESIS:
            continue
        claimed = claimed or fact.key == PAYMENT_CLAIMED_KEY
        proof = proof or fact.key == PAYMENT_PROOF_KEY
    if proof:
        return PaymentProgress.PROOF_RECEIVED
    return PaymentProgress.CLAIMED if claimed else PaymentProgress.NONE


class PaymentConfirmationStatus(StrEnum):
    RECORDED = "recorded"  # the confirmation is now on record
    DUPLICATE = "duplicate"  # the same confirmation, or the same payment, was already recorded
    REJECTED = "rejected"  # refused and audited; nothing recorded


def _text(value: str | None, name: str, *, allow_bar: bool = True) -> None:
    if value is None:
        return
    require_text(value, name)
    if len(value) > _MAX_TEXT or any(ord(c) < 32 for c in value):
        raise ValueError(
            f"{name} must be at most {_MAX_TEXT} characters, without control characters"
        )
    if not allow_bar and "|" in value:
        raise ValueError(f"{name} must not contain '|'")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmingActor:
    """The person who verified the payment, as the CRM or Admin that authenticated them vouches.

    `permissions` are what that system granted after authenticating them; the Core only checks that
    `CONFIRM_PAYMENTS` is among them. It never grants anything itself.
    """

    actor_id: str  # stable identity in the client's own system (a user id, never a secret)
    display_name: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        _text(self.actor_id, "actor_id", allow_bar=False)
        _text(self.display_name, "display_name")
        object.__setattr__(self, "permissions", frozenset(self.permissions))

    @property
    def may_confirm_payments(self) -> bool:
        return CONFIRM_PAYMENTS in self.permissions


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentConfirmation:
    """An explicit act of an authorised person: "I checked this payment and I confirm it".

    Immutable and provider-free. `confirmation_id` is chosen by the caller and makes a retry
    harmless. Amounts are exactly what the person entered: the Core converts and infers nothing,
    so an amount always comes with its currency and never without.
    """

    client_id: ClientId
    prospect_id: ProspectId
    confirmation_id: str  # the caller's idempotency key
    actor: ConfirmingActor
    confirmed_at: datetime
    payment_reference: str | None = None  # the client's or the bank's own reference
    amount: Decimal | None = None
    currency: str | None = None  # three capital letters
    proof_message_id: MessageId | None = None  # the prospect's message the person checked

    def __post_init__(self) -> None:
        require_text(self.client_id, "client_id")
        require_text(self.prospect_id, "prospect_id")
        _text(self.confirmation_id, "confirmation_id", allow_bar=False)
        _text(self.payment_reference, "payment_reference")
        _text(self.proof_message_id, "proof_message_id")
        require_aware(self.confirmed_at, "confirmed_at")
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency go together: a currency is never inferred")
        if self.amount is not None:
            if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
                raise ValueError("amount must be a finite Decimal")
            if self.amount <= 0:
                raise ValueError("amount must be positive")
        if self.currency is not None and not _CURRENCY.match(self.currency):
            raise ValueError("currency must be three capital letters")


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentConfirmationReceipt:
    """What the Core did with one confirmation."""

    status: PaymentConfirmationStatus
    confirmation_id: str
    reason: str | None = None  # why it was refused, or why it counts as a duplicate
    prospect_id: ProspectId | None = None
    errors: tuple[str, ...] = ()  # non-fatal problems, e.g. an audit write that must be retried

    @property
    def accepted(self) -> bool:
        """The payment is confirmed on record: this call recorded it, or an earlier one had."""
        return self.status in (
            PaymentConfirmationStatus.RECORDED,
            PaymentConfirmationStatus.DUPLICATE,
        )
