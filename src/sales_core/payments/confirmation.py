"""Records that a person authorised by the client verified a payment, once, or refuses and says why.

The only door through which a payment becomes confirmed in the Core. What comes in is a
`PaymentConfirmation`: an explicit act of a person the client's own CRM or Admin authenticated. This
module does not authenticate anyone, read provider payloads or know any payment provider, and it
cannot be reached by a Brain, a prospect or an automatic event. It decides whether the act may be
recorded and records it as confirmed facts from `FactSource.HUMAN`, whose evidence names the person.

- amount and currency are recorded exactly as entered: nothing is converted or inferred;
- the same confirmation id is recorded once, however often it arrives; the same payment reference
  confirmed again with the same terms is a duplicate, and with other terms a conflict, refused.
  Nothing else is treated as the same payment;
- prospect status, stage and actions are never touched: "paid" is derived from the fact
  (`payment_progress`), and what the sale does next is the Brain's decision on its next request.

Failures fail closed: a confirmation that cannot be validated records nothing and is audited.
Storage failures are raised, so the caller knows nothing was recorded and can retry.
"""

import threading
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

from sales_core.contracts import (
    CONFIRMED_VALUE,
    PAYMENT_AMOUNT_KEY,
    PAYMENT_CONFIRMED_KEY,
    PAYMENT_CURRENCY_KEY,
    PAYMENT_REFERENCE_KEY,
    AuditEvent,
    ClientId,
    Direction,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    PaymentConfirmation,
    PaymentConfirmationReceipt,
    PaymentConfirmationStatus,
)
from sales_core.ports import Clock, FactsReader, FactsWriter, SalesRepository

MAX_CLOCK_SKEW = timedelta(minutes=5)  # a caller's clock may run this far ahead of ours

# Audit vocabulary.
CONFIRMATION_RECEIVED = "payment.confirmation_received"
CONFIRMATION_RECORDED = "payment.confirmed"
CONFIRMATION_DUPLICATE = "payment.confirmation_duplicate"
CONFIRMATION_REJECTED = "payment.confirmation_rejected"

# Reasons carried by a receipt and by the audit trail.
UNKNOWN_CLIENT = "unknown_client"
NOT_AUTHORIZED = "not_authorized"
UNKNOWN_PROSPECT = "unknown_prospect"
INVALID_TIMESTAMP = "invalid_timestamp"
UNKNOWN_PROOF_MESSAGE = "unknown_proof_message"
CONFLICTING_CONFIRMATION = "conflicting_confirmation"
SAME_CONFIRMATION = "confirmation_id"
SAME_PAYMENT = "payment_reference"

_SEPARATOR = "|confirmation:"

type Terms = tuple[
    str | None, str | None, str | None, str | None
]  # reference, amount, currency, proof


def confirmation_ref(confirmation: PaymentConfirmation) -> str:
    """How the facts of one confirmation name it and the person who made it as their evidence."""
    return f"human:{confirmation.actor.actor_id}{_SEPARATOR}{confirmation.confirmation_id}"


def _id_of(fact: Fact) -> str | None:
    recorded_by = fact.evidence.recorded_by if fact.evidence is not None else None
    if recorded_by is None or _SEPARATOR not in recorded_by:
        return None
    return recorded_by.split(_SEPARATOR, 1)[1]


class PaymentConfirmationService:
    """One instance per client, like the Engine. Safe to call from several threads."""

    def __init__(
        self,
        *,
        repository: SalesRepository,
        facts: FactsReader,
        facts_writer: FactsWriter,
        clock: Clock,
    ) -> None:
        if facts_writer.client_id != repository.client_id:
            raise ValueError("facts writer is bound to a different client than the repository")
        self._client_id: ClientId = repository.client_id
        self._repo = repository
        self._facts = facts
        self._writer = facts_writer
        self._clock = clock
        self._lock = threading.RLock()
        self._events: list[AuditEvent] = []  # audit events not yet written

    def confirm(self, confirmation: PaymentConfirmation) -> PaymentConfirmationReceipt:
        """Record the confirmation. Idempotent: repeating it changes nothing.

        Raises what the storage raises (nothing is then recorded, and the caller may retry).
        """
        with self._lock:
            receipt = self._confirm(confirmation)
            return replace(receipt, errors=self._flush())

    # ---- decision --------------------------------------------------------------------------

    def _confirm(self, c: PaymentConfirmation) -> PaymentConfirmationReceipt:
        self._emit(CONFIRMATION_RECEIVED, c)
        if c.client_id != self._client_id:
            return self._reject(c, UNKNOWN_CLIENT, claimed_client_id=c.client_id)
        if not c.actor.may_confirm_payments:
            return self._reject(c, NOT_AUTHORIZED)
        if c.confirmed_at > self._clock.now() + MAX_CLOCK_SKEW:
            return self._reject(c, INVALID_TIMESTAMP)
        prospect = self._repo.get_prospect(c.prospect_id)
        if prospect is None or prospect.client_id != self._client_id:
            return self._reject(c, UNKNOWN_PROSPECT)
        if c.proof_message_id is not None:
            conversation = self._repo.get_conversation(prospect.prospect_id)
            message = next(
                (m for m in conversation.messages if m.message_id == c.proof_message_id), None
            )
            if message is None or message.direction is not Direction.INBOUND:
                return self._reject(c, UNKNOWN_PROOF_MESSAGE)

        known = list(self._facts.get_facts(prospect.prospect_id))
        mine = _terms_by_id(known)
        if c.confirmation_id in mine:
            if mine[c.confirmation_id] == _terms_of(c):
                return self._duplicate(c, SAME_CONFIRMATION)
            return self._reject(c, CONFLICTING_CONFIRMATION)
        if c.payment_reference is not None:
            for other in mine.values():
                if other[0] == c.payment_reference:
                    same = other[1:3] == _terms_of(c)[1:3]
                    return (
                        self._duplicate(c, SAME_PAYMENT)
                        if same
                        else self._reject(c, CONFLICTING_CONFIRMATION)
                    )

        self._writer.append_facts(prospect.prospect_id, _facts_of(c))
        self._emit(
            CONFIRMATION_RECORDED,
            c,
            payment_reference=c.payment_reference,
            amount=_amount(c),
            currency=c.currency,
        )
        return PaymentConfirmationReceipt(
            status=PaymentConfirmationStatus.RECORDED,
            confirmation_id=c.confirmation_id,
            prospect_id=prospect.prospect_id,
        )

    def _duplicate(self, c: PaymentConfirmation, same: str) -> PaymentConfirmationReceipt:
        self._emit(CONFIRMATION_DUPLICATE, c, same=same)
        return PaymentConfirmationReceipt(
            status=PaymentConfirmationStatus.DUPLICATE,
            confirmation_id=c.confirmation_id,
            reason=same,
            prospect_id=c.prospect_id,
        )

    def _reject(
        self, c: PaymentConfirmation, reason: str, **details: object
    ) -> PaymentConfirmationReceipt:
        self._emit(CONFIRMATION_REJECTED, c, reason=reason, **details)
        return PaymentConfirmationReceipt(
            status=PaymentConfirmationStatus.REJECTED,
            confirmation_id=c.confirmation_id,
            reason=reason,
        )

    # ---- audit -----------------------------------------------------------------------------

    def _emit(self, event_type: str, c: PaymentConfirmation, **details: object) -> None:
        # The person and the confirmation, never a credential or a payment detail beyond the terms.
        details = {"confirmation_id": c.confirmation_id, **details}
        self._events.append(
            AuditEvent(
                timestamp=self._clock.now(),
                client_id=self._client_id,
                event_type=event_type,
                actor=f"human:{c.actor.actor_id}",
                prospect_id=c.prospect_id if c.client_id == self._client_id else None,
                details={k: str(v) for k, v in details.items() if v is not None},
            )
        )

    def _flush(self) -> tuple[str, ...]:
        """Write buffered audit events. On failure they are kept and retried at the next call."""
        if not self._events:
            return ()
        batch = list(self._events)
        try:
            self._repo.append_audit_events(batch)
        except Exception as error:  # noqa: BLE001 - the decision is made; only report
            return (f"audit write failed, {len(batch)} events kept for retry: {error!r}",)
        del self._events[: len(batch)]
        return ()


def _amount(c: PaymentConfirmation) -> str | None:
    return None if c.amount is None else format(c.amount, "f")


def _terms_of(c: PaymentConfirmation) -> Terms:
    return (c.payment_reference, _amount(c), c.currency, c.proof_message_id)


def _terms_by_id(known: Sequence[Fact]) -> dict[str, Terms]:
    """The terms of every confirmation already recorded, by confirmation id. Only facts from a
    person or the client's own records count: nothing else can be a confirmation."""
    values: dict[str, dict[str, str | None]] = {}
    for fact in known:
        cid = _id_of(fact)
        if cid is None or fact.evidence is None or fact.evidence.source is not FactSource.HUMAN:
            continue
        group = values.setdefault(cid, {})
        group[fact.key] = fact.value
        if fact.key == PAYMENT_CONFIRMED_KEY:
            group["proof"] = fact.evidence.message_id
    return {
        cid: (
            group.get(PAYMENT_REFERENCE_KEY),
            group.get(PAYMENT_AMOUNT_KEY),
            group.get(PAYMENT_CURRENCY_KEY),
            group.get("proof"),
        )
        for cid, group in values.items()
        if PAYMENT_CONFIRMED_KEY in group
    }


def _facts_of(c: PaymentConfirmation) -> Sequence[Fact]:
    """The observations one confirmation makes, confirmed and from a person. Dated by the moment
    the person confirmed, which a retry repeats, so the store recognises a repeat by identity too.
    The confirmation comes last: it is never visible without the details that came with it."""
    ref = confirmation_ref(c)
    values = [
        (PAYMENT_REFERENCE_KEY, c.payment_reference),
        (PAYMENT_AMOUNT_KEY, _amount(c)),
        (PAYMENT_CURRENCY_KEY, c.currency),
        (PAYMENT_CONFIRMED_KEY, CONFIRMED_VALUE),
    ]
    return [
        Fact(
            key=key,
            value=value,
            status=FactStatus.CONFIRMED,
            evidence=FactEvidence(
                source=FactSource.HUMAN,
                message_id=c.proof_message_id if key == PAYMENT_CONFIRMED_KEY else None,
                observed_at=c.confirmed_at,
                recorded_by=ref,
            ),
            recorded_at=c.confirmed_at,
        )
        for key, value in values
        if value is not None
    ]
