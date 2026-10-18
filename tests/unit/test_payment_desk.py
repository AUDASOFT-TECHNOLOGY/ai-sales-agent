"""The desk of the person who checks payments: who said they paid, what is waiting,
what was confirmed and by whom. Reading never changes anything; confirming goes through the one
door (`PaymentConfirmationService`). Generic: no provider, no real account."""

from datetime import timedelta
from decimal import Decimal

import pytest
from engine_harness import ScriptedBrain, prospect
from factories import CLIENT, NOW, make_client_context

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    CONFIRM_PAYMENTS,
    PAYMENT_CLAIMED_KEY,
    PAYMENT_PROOF_KEY,
    ConfirmingActor,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    PaymentConfirmation,
    PaymentConfirmationStatus,
    PaymentProgress,
    ProspectId,
    Scheduling,
)
from sales_core.engine import Engine, EngineConfig
from sales_core.payments import PaymentDesk, PaymentsNotSupportedError
from sales_integrations.fakes import FakeClock, FakeMessagingChannel, FakeSalesRepository

STAFF = ConfirmingActor(actor_id="client:acme", display_name="Acme", permissions={CONFIRM_PAYMENTS})


def claim(key=PAYMENT_CLAIMED_KEY, at=NOW):
    return Fact(
        key=key,
        value="claimed",
        status=FactStatus.DISCOVERED,
        evidence=FactEvidence(source=FactSource.PROSPECT),
        recorded_at=at,
    )


def wrote(pid, **overrides):
    """A prospect who has written back (a claim needs a message)."""
    return prospect(pid, schedule=Scheduling(last_inbound_at=NOW), **overrides)


def confirmation(pid="p1", **overrides):
    fields = {
        "client_id": CLIENT,
        "prospect_id": ProspectId(pid),
        "confirmation_id": f"c-{pid}",
        "actor": STAFF,
        "confirmed_at": NOW - timedelta(minutes=1),
        "payment_reference": "ref-1",
        "amount": Decimal("125.50"),
        "currency": "TND",
    }
    return PaymentConfirmation(**(fields | overrides))


class Desk:
    """The desk as the Client API builds it: through the Engine's lock, over its own collaborators."""

    def __init__(self, repo, *, with_facts=True):
        self.engine = Engine(
            client=make_client_context(),
            repository=repo,
            brain=ScriptedBrain(),
            channel=FakeMessagingChannel(),
            clock=FakeClock(NOW),
            config=EngineConfig(mode=ApprovalMode.APPROVAL),
            facts=repo if with_facts else None,
            facts_writer=repo if with_facts else None,
        )

    def _desk(self, work):
        return self.engine.exclusive(
            lambda repository, facts, writer, clock: work(
                PaymentDesk(
                    client_id=CLIENT,
                    repository=repository,
                    facts=facts,
                    facts_writer=writer,
                    clock=clock,
                )
            )
        )

    def payment_cases(self):
        return self._desk(PaymentDesk.cases)

    def confirm_payment(self, confirmation):
        return self._desk(lambda d: d.confirm(confirmation))

    def payment_case_of(self, prospect_id):
        return self._desk(lambda d: d.case_of(prospect_id))


def engine_over(repo, *, with_facts=True):
    return Desk(repo, with_facts=with_facts)


@pytest.fixture
def repo():
    return FakeSalesRepository(
        CLIENT,
        prospects=[
            wrote("p1", display_name="Alice"),
            wrote("p2", display_name="Bob"),
            prospect("p3", display_name="Silent"),  # never wrote back
        ],
    )


class TestWhatIsListed:
    def test_nothing_is_listed_when_nobody_spoke_of_paying(self, repo):
        assert engine_over(repo).payment_cases() == ()

    def test_a_claim_and_a_receipt_are_listed_as_what_they_are(self, repo):
        repo.append_facts(ProspectId("p1"), [claim(PAYMENT_CLAIMED_KEY)])
        repo.append_facts(ProspectId("p2"), [claim(PAYMENT_PROOF_KEY)])
        cases = {c.prospect_id: c for c in engine_over(repo).payment_cases()}
        assert cases["p1"].progress is PaymentProgress.CLAIMED
        assert cases["p2"].progress is PaymentProgress.PROOF_RECEIVED
        assert cases["p1"].prospect_name == "Alice"
        assert cases["p1"].signalled_at == NOW
        assert cases["p1"].confirmed_at is None and cases["p1"].amount is None

    def test_only_people_who_wrote_back_are_looked_at(self, repo):
        repo.append_facts(ProspectId("p3"), [claim()])  # a claim on someone who never wrote
        assert engine_over(repo).payment_cases() == ()

    def test_a_guess_is_not_a_claim(self, repo):
        guess = Fact(
            key=PAYMENT_CLAIMED_KEY,
            value="claimed",
            status=FactStatus.HYPOTHESIS,
            evidence=FactEvidence(source=FactSource.BRAIN_INFERENCE),
            recorded_at=NOW,
        )
        repo.append_facts(ProspectId("p1"), [guess])
        assert engine_over(repo).payment_cases() == ()

    def test_a_client_that_keeps_no_facts_lists_nothing(self, repo):
        assert engine_over(repo, with_facts=False).payment_cases() == ()


class TestConfirming:
    def test_a_confirmed_payment_is_paid_with_the_terms_the_person_entered(self, repo):
        repo.append_facts(ProspectId("p1"), [claim()])
        engine = engine_over(repo)
        receipt = engine.confirm_payment(confirmation("p1"))
        assert receipt.status is PaymentConfirmationStatus.RECORDED
        (case,) = engine.payment_cases()
        assert case.progress is PaymentProgress.PAID
        assert (case.amount, case.currency, case.reference) == ("125.50", "TND", "ref-1")
        assert case.confirmed_by == "client:acme"
        assert case.confirmed_at == NOW - timedelta(minutes=1)

    def test_what_waits_is_listed_before_what_is_paid(self, repo):
        repo.append_facts(ProspectId("p1"), [claim()])
        repo.append_facts(ProspectId("p2"), [claim()])
        engine = engine_over(repo)
        engine.confirm_payment(confirmation("p1"))
        assert [c.prospect_id for c in engine.payment_cases()] == ["p2", "p1"]

    def test_confirming_twice_is_one_confirmation(self, repo):
        repo.append_facts(ProspectId("p1"), [claim()])
        engine = engine_over(repo)
        engine.confirm_payment(confirmation("p1"))
        again = engine.confirm_payment(confirmation("p1"))
        assert again.status is PaymentConfirmationStatus.DUPLICATE
        assert len(engine.payment_cases()) == 1

    def test_the_same_reference_with_other_terms_is_refused(self, repo):
        engine = engine_over(repo)
        engine.confirm_payment(confirmation("p1"))
        other = engine.confirm_payment(
            confirmation("p1", confirmation_id="c-other", amount=Decimal(1))
        )
        assert other.status is PaymentConfirmationStatus.REJECTED

    def test_an_unknown_prospect_is_refused(self, repo):
        receipt = engine_over(repo).confirm_payment(confirmation("nobody"))
        assert receipt.status is PaymentConfirmationStatus.REJECTED
        assert receipt.reason == "unknown_prospect"

    def test_a_person_without_the_permission_cannot_confirm(self, repo):
        weak = ConfirmingActor(actor_id="client:acme", permissions=set())
        receipt = engine_over(repo).confirm_payment(confirmation("p1", actor=weak))
        assert receipt.status is PaymentConfirmationStatus.REJECTED

    def test_a_client_that_keeps_no_facts_cannot_confirm(self, repo):
        with pytest.raises(PaymentsNotSupportedError):
            engine_over(repo, with_facts=False).confirm_payment(confirmation("p1"))

    def test_one_case_can_be_read_alone(self, repo):
        repo.append_facts(ProspectId("p1"), [claim()])
        engine = engine_over(repo)
        assert engine.payment_case_of(ProspectId("p1")).progress is PaymentProgress.CLAIMED
        assert engine.payment_case_of(ProspectId("p2")) is None
        assert engine.payment_case_of(ProspectId("unknown")) is None
