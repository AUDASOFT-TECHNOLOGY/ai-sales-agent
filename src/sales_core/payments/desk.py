"""What a person at the client sees of its payments and does about them.

A prospect may say they paid or send a receipt; the Brain records that as exactly that (a claim, a
proof). A payment is *paid* only when a person authorised by the client checked it and confirmed it:
`PaymentDesk.confirm` is the surface for that act, over the one door that records it
(`PaymentConfirmationService`). Reading changes nothing.

The desk is built by a surface that has authenticated the person (the Client API, a CRM screen), and it
runs inside the Engine's lock (`Engine.exclusive`) so the confirmation has the same single writer as
everything else. It is never reachable by a Brain, a prospect, a message or an automatic event.

Only people who wrote back can have said they paid, so only the most recent `MAX_LOOKUPS` of them are
looked at when listing: that bounds the facts read, whatever the size of the prospect list.
"""

from sales_core.contracts import (
    ClientId,
    PaymentCase,
    PaymentConfirmation,
    PaymentConfirmationReceipt,
    PaymentProgress,
    ProspectId,
)
from sales_core.payments.cases import payment_case
from sales_core.payments.confirmation import PaymentConfirmationService
from sales_core.ports import Clock, FactsReader, FactsWriter, SalesRepository

MAX_LOOKUPS = 300


class PaymentsNotSupportedError(RuntimeError):
    """This client keeps no facts, so a payment can be neither read nor confirmed."""


class PaymentDesk:
    def __init__(
        self,
        *,
        client_id: ClientId,
        repository: SalesRepository,
        facts: FactsReader | None,
        facts_writer: FactsWriter | None,
        clock: Clock,
    ) -> None:
        self._client_id = client_id
        self._repo = repository
        self._facts = facts
        self._writer = facts_writer
        self._clock = clock

    def cases(self) -> tuple[PaymentCase, ...]:
        """The payments to check (first) and those already confirmed."""
        if self._facts is None:
            return ()
        writers = sorted(
            (
                p
                for p in self._repo.list_prospects()
                if p.client_id == self._client_id and p.schedule.last_inbound_at is not None
            ),
            key=lambda p: p.schedule.last_inbound_at,  # type: ignore[arg-type,return-value]
            reverse=True,
        )[:MAX_LOOKUPS]
        found = []
        for prospect in writers:
            case = payment_case(
                prospect.prospect_id,
                prospect.display_name,
                self._facts.get_facts(prospect.prospect_id),
            )
            if case is not None:
                found.append(case)
        waiting = [c for c in found if c.progress is not PaymentProgress.PAID]
        paid = [c for c in found if c.progress is PaymentProgress.PAID]
        return (*waiting, *paid)

    def case_of(self, prospect_id: ProspectId) -> PaymentCase | None:
        """One prospect's payment, or None when nothing was said about it (or they are not ours)."""
        if self._facts is None:
            return None
        prospect = self._repo.get_prospect(prospect_id)
        if prospect is None or prospect.client_id != self._client_id:
            return None
        return payment_case(
            prospect.prospect_id, prospect.display_name, self._facts.get_facts(prospect_id)
        )

    def confirm(self, confirmation: PaymentConfirmation) -> PaymentConfirmationReceipt:
        """Records that a person checked and confirmed a payment. Idempotent."""
        if self._facts is None or self._writer is None:
            raise PaymentsNotSupportedError("this client keeps no facts")
        return PaymentConfirmationService(
            repository=self._repo, facts=self._facts, facts_writer=self._writer, clock=self._clock
        ).confirm(confirmation)
