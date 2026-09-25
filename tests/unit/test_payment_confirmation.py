"""Slice 6c (payment model): only a person authorised by the client confirms a payment.

A prospect's claim and a prospect's proof are recorded as what they are. "Paid" is derived from a
valid confirmation and from nothing else. Generic: no provider, no client, no real account.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from engine_harness import ScriptedBrain, decide, inbound, prospect, send
from factories import (
    CLIENT,
    NOW,
    make_client_context,
    make_conversation,
    make_message,
    make_prospect,
)

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    CONFIRM_PAYMENTS,
    CONFIRMATION_KEYS,
    CORE_FACT_KEY_PREFIX,
    PAYMENT_AMOUNT_KEY,
    PAYMENT_CLAIMED_KEY,
    PAYMENT_CONFIRMED_KEY,
    PAYMENT_CURRENCY_KEY,
    PAYMENT_PROOF_KEY,
    PAYMENT_REFERENCE_KEY,
    UNVERIFIED_CLAIM_KEYS,
    ClientId,
    ConfirmingActor,
    DecisionType,
    Direction,
    Fact,
    FactEvidence,
    FactProposal,
    FactSource,
    FactStatus,
    MessageId,
    PaymentConfirmation,
    PaymentConfirmationStatus,
    PaymentProgress,
    ProspectId,
    ProspectStatus,
    payment_progress,
)
from sales_core.engine import Engine, EngineConfig
from sales_core.facts import ProposalRejection, review_proposals
from sales_core.payments import PaymentConfirmationService, confirmation_ref
from sales_integrations.fakes import FakeClock, FakeMessagingChannel, FakeSalesRepository

P1 = ProspectId("p1")
STAFF = ConfirmingActor(actor_id="user-7", display_name="Sam", permissions={CONFIRM_PAYMENTS})


def fact(key, value="x", status=FactStatus.DISCOVERED, source=FactSource.PROSPECT):
    return Fact(
        key=key,
        value=value,
        status=status,
        evidence=FactEvidence(source=source) if source else None,
        recorded_at=NOW,
    )


def confirmation(**overrides):
    fields = {
        "client_id": CLIENT,
        "prospect_id": P1,
        "confirmation_id": "c-1",
        "actor": STAFF,
        "confirmed_at": NOW - timedelta(minutes=1),
        "payment_reference": "ref-1",
        "amount": Decimal("125.50"),
        "currency": "XTS",
    }
    return PaymentConfirmation(**(fields | overrides))


class World:
    def __init__(self):
        self.clock = FakeClock(NOW)
        self.repo = FakeSalesRepository(CLIENT, prospects=[prospect("p1")])
        self.service = PaymentConfirmationService(
            repository=self.repo, facts=self.repo, facts_writer=self.repo, clock=self.clock
        )

    def facts(self, pid=P1):
        return list(self.repo.get_facts(pid))

    def by_key(self, key, pid=P1):
        return [f for f in self.facts(pid) if f.key == key]

    def events(self, event_type):
        return [e for e in self.repo.audit_events if e.event_type == event_type]

    def progress(self, pid=P1):
        return payment_progress(self.facts(pid))


@pytest.fixture
def world():
    return World()


class TestWhatIsPaid:
    """`PAID` is derived, and only a valid confirmation yields it."""

    def test_nothing_recorded_is_not_paid(self):
        assert payment_progress([]) is PaymentProgress.NONE

    def test_a_claim_is_a_claim(self):
        assert payment_progress([fact(PAYMENT_CLAIMED_KEY)]) is PaymentProgress.CLAIMED

    def test_a_proof_is_a_proof(self):
        assert payment_progress([fact(PAYMENT_PROOF_KEY)]) is PaymentProgress.PROOF_RECEIVED

    def test_a_claim_and_a_proof_together_are_still_not_paid(self):
        both = [fact(PAYMENT_CLAIMED_KEY), fact(PAYMENT_PROOF_KEY)]
        assert payment_progress(both) is PaymentProgress.PROOF_RECEIVED

    def test_a_guessed_claim_counts_for_nothing(self):
        guess = fact(
            PAYMENT_CLAIMED_KEY, status=FactStatus.HYPOTHESIS, source=FactSource.BRAIN_INFERENCE
        )
        assert payment_progress([guess]) is PaymentProgress.NONE

    @pytest.mark.parametrize("source", [FactSource.HUMAN, FactSource.CLIENT_DATA])
    def test_a_confirmation_from_a_person_or_the_clients_records_is_paid(self, source):
        confirmed = fact(PAYMENT_CONFIRMED_KEY, "confirmed", FactStatus.CONFIRMED, source)
        assert payment_progress([confirmed]) is PaymentProgress.PAID

    @pytest.mark.parametrize(
        "source",
        [
            FactSource.SYSTEM,
            FactSource.PROSPECT,
            FactSource.CONVERSATION,
            None,
        ],
    )
    def test_the_system_the_prospect_or_the_conversation_never_make_it_paid(self, source):
        forged = fact(PAYMENT_CONFIRMED_KEY, "confirmed", FactStatus.CONFIRMED, source)
        assert payment_progress([forged]) is PaymentProgress.NONE

    def test_a_brain_inference_cannot_even_be_a_confirmed_fact(self):
        with pytest.raises(ValueError, match="Brain inference"):
            fact(
                PAYMENT_CONFIRMED_KEY, "confirmed", FactStatus.CONFIRMED, FactSource.BRAIN_INFERENCE
            )

    def test_an_unconfirmed_confirmation_is_not_one(self):
        weak = fact(PAYMENT_CONFIRMED_KEY, "confirmed", FactStatus.DISCOVERED, FactSource.HUMAN)
        assert payment_progress([weak]) is PaymentProgress.NONE

    def test_any_other_fact_saying_paid_is_only_a_fact(self):
        loud = [
            fact(key, "paid", FactStatus.CONFIRMED, FactSource.HUMAN)
            for key in ("payment_status", "paid", "stage")
        ]
        assert payment_progress(loud) is PaymentProgress.NONE


class TestTheConfirmationContract:
    def test_it_is_immutable_and_provider_free(self):
        names = set(PaymentConfirmation.__dataclass_fields__)
        assert not names & {"provider", "event_id", "payload", "raw", "status", "paid"}
        with pytest.raises(AttributeError):
            confirmation().amount = Decimal(1)  # type: ignore[misc]

    def test_the_actor_and_what_they_may_do_are_part_of_it(self):
        assert confirmation().actor.may_confirm_payments
        assert not ConfirmingActor(actor_id="u").may_confirm_payments

    def test_the_time_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            confirmation(confirmed_at=NOW.replace(tzinfo=None))

    @pytest.mark.parametrize("field", ["confirmation_id"])
    def test_ids_are_plain_text(self, field):
        for bad in ("", "  ", "a|b", "a\nb", "x" * 201):
            with pytest.raises(ValueError):
                confirmation(**{field: bad})

    def test_the_actor_id_is_plain_text_without_the_separator(self):
        for bad in ("", "a|b", "a\nb"):
            with pytest.raises(ValueError):
                ConfirmingActor(actor_id=bad)

    def test_an_amount_needs_its_currency_and_never_the_other_way_round(self):
        with pytest.raises(ValueError, match="never inferred"):
            confirmation(currency=None)
        with pytest.raises(ValueError, match="never inferred"):
            confirmation(amount=None)

    @pytest.mark.parametrize("amount", [125.5, "125.50", Decimal("NaN"), Decimal(0), Decimal(-1)])
    def test_an_amount_is_an_exact_positive_decimal(self, amount):
        with pytest.raises(ValueError):
            confirmation(amount=amount)

    @pytest.mark.parametrize("currency", ["xts", "XT", "XTSS", ""])
    def test_a_currency_is_three_capital_letters(self, currency):
        with pytest.raises(ValueError, match="currency"):
            confirmation(currency=currency)


class TestAPersonConfirms:
    def test_an_authorised_person_confirms_and_the_prospect_becomes_paid(self, world):
        receipt = world.service.confirm(confirmation())
        assert receipt.status is PaymentConfirmationStatus.RECORDED and receipt.accepted
        (confirmed,) = world.by_key(PAYMENT_CONFIRMED_KEY)
        assert confirmed.value == "confirmed" and confirmed.status is FactStatus.CONFIRMED
        assert world.progress() is PaymentProgress.PAID

    def test_the_provenance_is_human_and_names_the_person(self, world):
        world.service.confirm(confirmation())
        for f in world.facts():
            assert f.evidence.source is FactSource.HUMAN
            assert f.evidence.recorded_by == confirmation_ref(confirmation())
            assert f.evidence.recorded_by.startswith("human:user-7|")
            assert f.evidence.observed_at == NOW - timedelta(minutes=1)
            assert f.key in CONFIRMATION_KEYS

    def test_amount_and_currency_are_kept_exactly_as_entered(self, world):
        world.service.confirm(confirmation(amount=Decimal("1200.000"), currency="ZZZ"))
        assert world.by_key(PAYMENT_AMOUNT_KEY)[0].value == "1200.000"
        assert world.by_key(PAYMENT_CURRENCY_KEY)[0].value == "ZZZ"
        assert world.by_key(PAYMENT_REFERENCE_KEY)[0].value == "ref-1"

    def test_nothing_is_invented_when_the_person_gave_only_the_confirmation(self, world):
        world.service.confirm(confirmation(payment_reference=None, amount=None, currency=None))
        assert [f.key for f in world.facts()] == [PAYMENT_CONFIRMED_KEY]

    def test_the_confirmation_is_written_last(self, world):
        seen = []
        original = world.repo.append_facts
        world.repo.append_facts = lambda pid, facts: (
            seen.append([f.key for f in facts]),
            original(pid, facts),
        )
        world.service.confirm(confirmation())
        assert seen[0][-1] == PAYMENT_CONFIRMED_KEY

    def test_the_proof_the_person_checked_can_be_referenced(self, world):
        proof = make_message(
            message_id=MessageId("in-proof"),
            prospect_id=P1,
            direction=Direction.INBOUND,
            metadata={"type": "image"},
        )
        world.repo.append_messages(
            [replace(proof, conversation_id=world.repo.get_conversation(P1).conversation_id)]
        )
        world.service.confirm(confirmation(proof_message_id=MessageId("in-proof")))
        assert world.by_key(PAYMENT_CONFIRMED_KEY)[0].evidence.message_id == "in-proof"

    def test_a_proof_that_is_not_a_message_of_this_prospect_is_refused(self, world):
        receipt = world.service.confirm(confirmation(proof_message_id=MessageId("nope")))
        assert receipt.reason == "unknown_proof_message" and world.facts() == []

    def test_commercial_state_is_left_alone_and_the_audit_names_the_person(self, world):
        before = world.repo.get_prospect(P1)
        world.service.confirm(confirmation())
        assert world.repo.get_prospect(P1) == before
        assert world.repo.list_actions() == [] and world.repo.messages == ()
        (recorded,) = world.events("payment.confirmed")
        assert recorded.actor == "human:user-7" and recorded.prospect_id == P1
        assert recorded.details["amount"] == "125.50"

    def test_a_confirmation_does_not_close_or_advance_anything_by_itself(self, world):
        world.service.confirm(confirmation())
        assert world.repo.get_prospect(P1).status is not ProspectStatus.CLOSED


class TestNobodyElseCan:
    def test_someone_without_the_permission_cannot(self, world):
        actor = ConfirmingActor(actor_id="user-8", permissions={"something.else"})
        receipt = world.service.confirm(confirmation(actor=actor))
        assert (
            receipt.status is PaymentConfirmationStatus.REJECTED
            and receipt.reason == "not_authorized"
        )
        assert world.facts() == [] and world.progress() is PaymentProgress.NONE
        assert (
            world.events("payment.confirmation_rejected")[0].details["reason"] == "not_authorized"
        )

    def test_an_actor_with_no_permissions_at_all_cannot(self, world):
        receipt = world.service.confirm(
            confirmation(actor=ConfirmingActor(actor_id="provider-callback"))
        )
        assert receipt.reason == "not_authorized" and world.facts() == []

    def test_a_confirmation_for_another_client_is_refused(self, world):
        receipt = world.service.confirm(confirmation(client_id=ClientId("someone-else")))
        assert receipt.reason == "unknown_client" and world.facts() == []
        (rejected,) = world.events("payment.confirmation_rejected")
        assert (
            rejected.client_id == CLIENT and rejected.details["claimed_client_id"] == "someone-else"
        )

    def test_an_unknown_prospect_is_refused(self, world):
        receipt = world.service.confirm(confirmation(prospect_id=ProspectId("ghost")))
        assert receipt.reason == "unknown_prospect"
        assert world.by_key(PAYMENT_CONFIRMED_KEY, ProspectId("ghost")) == []

    def test_a_confirmation_from_the_future_is_refused(self, world):
        receipt = world.service.confirm(confirmation(confirmed_at=NOW + timedelta(hours=1)))
        assert receipt.reason == "invalid_timestamp" and world.facts() == []

    def test_the_service_is_bound_to_one_client(self):
        repo = FakeSalesRepository(CLIENT)
        with pytest.raises(ValueError, match="different client"):
            PaymentConfirmationService(
                repository=repo,
                facts=repo,
                facts_writer=FakeSalesRepository(ClientId("other")),
                clock=FakeClock(NOW),
            )

    def test_a_claim_and_a_proof_recorded_by_the_brain_do_not_confirm_anything(self, world):
        world.repo.append_facts(P1, [fact(PAYMENT_CLAIMED_KEY), fact(PAYMENT_PROOF_KEY)])
        assert world.progress() is PaymentProgress.PROOF_RECEIVED
        assert world.by_key(PAYMENT_CONFIRMED_KEY) == []


class TestIdempotencyAndConflicts:
    def test_the_same_confirmation_twice_is_recorded_once(self, world):
        world.service.confirm(confirmation())
        count = len(world.facts())
        again = world.service.confirm(confirmation())
        assert (
            again.status is PaymentConfirmationStatus.DUPLICATE
            and again.reason == "confirmation_id"
        )
        assert again.accepted and len(world.facts()) == count
        assert len(world.events("payment.confirmation_duplicate")) == 1

    def test_a_restarted_service_recognises_it_too(self, world):
        world.service.confirm(confirmation())
        restarted = PaymentConfirmationService(
            repository=world.repo, facts=world.repo, facts_writer=world.repo, clock=world.clock
        )
        assert restarted.confirm(confirmation()).status is PaymentConfirmationStatus.DUPLICATE

    def test_the_same_id_with_other_terms_is_a_contradiction_and_is_refused(self, world):
        world.service.confirm(confirmation())
        count = len(world.facts())
        clash = world.service.confirm(confirmation(amount=Decimal(99)))
        assert (
            clash.status is PaymentConfirmationStatus.REJECTED
            and clash.reason == "conflicting_confirmation"
        )
        assert len(world.facts()) == count

    def test_another_id_for_the_same_reference_and_terms_is_the_same_payment(self, world):
        world.service.confirm(confirmation())
        count = len(world.facts())
        second = world.service.confirm(confirmation(confirmation_id="c-2"))
        assert (
            second.status is PaymentConfirmationStatus.DUPLICATE
            and second.reason == "payment_reference"
        )
        assert len(world.facts()) == count

    @pytest.mark.parametrize("change", [{"amount": Decimal(99)}, {"currency": "ZZZ"}])
    def test_another_id_for_the_same_reference_with_other_terms_is_refused(self, world, change):
        world.service.confirm(confirmation())
        clash = world.service.confirm(confirmation(confirmation_id="c-2", **change))
        assert clash.reason == "conflicting_confirmation"
        assert len(world.by_key(PAYMENT_CONFIRMED_KEY)) == 1

    def test_a_different_reference_is_a_different_payment(self, world):
        world.service.confirm(confirmation())
        other = world.service.confirm(
            confirmation(confirmation_id="c-2", payment_reference="ref-2")
        )
        assert other.status is PaymentConfirmationStatus.RECORDED
        assert len(world.by_key(PAYMENT_CONFIRMED_KEY)) == 2

    def test_a_failed_write_records_nothing_and_a_retry_completes_it(self, world):
        world.repo.fail_next("append_facts")
        with pytest.raises(RuntimeError):
            world.service.confirm(confirmation())
        assert world.facts() == []
        assert world.service.confirm(confirmation()).status is PaymentConfirmationStatus.RECORDED

    def test_a_failed_audit_write_does_not_undo_the_confirmation(self, world):
        world.repo.fail_next("append_audit_events")
        receipt = world.service.confirm(confirmation())
        assert receipt.accepted and receipt.errors and world.progress() is PaymentProgress.PAID


class TestWhatABrainMayRecord:
    """A Brain may report a claim or a proof, as discovered facts anchored to the prospect's message."""

    INBOUND = make_message(message_id=MessageId("in-1"), direction=Direction.INBOUND, text="I paid")
    OUTBOUND = make_message(message_id=MessageId("out-1"), direction=Direction.OUTBOUND)

    def review(self, **overrides):
        fields = {
            "key": PAYMENT_CLAIMED_KEY,
            "value": "claimed",
            "status": FactStatus.DISCOVERED,
            "source": FactSource.PROSPECT,
            "message_id": MessageId("in-1"),
        }
        return review_proposals(
            [FactProposal(**(fields | overrides))],
            client_id=CLIENT,
            prospect=make_prospect(),
            conversation=make_conversation(messages=(self.OUTBOUND, self.INBOUND)),
            existing=[],
            recorded_at=NOW,
            recorded_by="brain-proposal:a-1",
        )

    @pytest.mark.parametrize("key", sorted(UNVERIFIED_CLAIM_KEYS))
    def test_a_claim_and_a_proof_are_recorded_as_discovered_from_the_prospects_message(self, key):
        (recorded,) = self.review(key=key).accepted
        assert recorded.status is FactStatus.DISCOVERED and not recorded.is_confirmed
        assert recorded.evidence.source is FactSource.PROSPECT
        assert recorded.evidence.message_id == "in-1"

    @pytest.mark.parametrize("key", sorted(UNVERIFIED_CLAIM_KEYS))
    def test_they_are_never_confirmed(self, key):
        result = self.review(key=key, status=FactStatus.CONFIRMED)
        assert not result.accepted
        assert [r.reason for r in result.rejected] == [ProposalRejection.STATUS_NOT_ALLOWED]

    @pytest.mark.parametrize("key", sorted(UNVERIFIED_CLAIM_KEYS))
    def test_nor_guessed(self, key):
        assert not self.review(key=key, status=FactStatus.HYPOTHESIS).accepted
        assert not self.review(
            key=key, source=FactSource.BRAIN_INFERENCE, status=FactStatus.HYPOTHESIS
        ).accepted

    @pytest.mark.parametrize(
        "source", [FactSource.HUMAN, FactSource.CLIENT_DATA, FactSource.SYSTEM]
    )
    def test_nor_claimed_as_coming_from_a_person_the_client_or_the_system(self, source):
        result = self.review(source=source, status=FactStatus.DISCOVERED)
        assert [r.reason for r in result.rejected] == [ProposalRejection.SOURCE_NOT_ALLOWED]

    def test_they_need_the_prospects_own_message(self):
        assert [r.reason for r in self.review(message_id=None).rejected] == [
            ProposalRejection.MESSAGE_REQUIRED
        ]
        assert [r.reason for r in self.review(message_id=MessageId("out-1")).rejected] == [
            ProposalRejection.NOT_A_PROSPECT_MESSAGE
        ]
        assert [r.reason for r in self.review(message_id=MessageId("nope")).rejected] == [
            ProposalRejection.UNKNOWN_MESSAGE
        ]

    def test_the_confirmation_and_everything_around_it_is_reserved_to_the_core(self):
        for key in CONFIRMATION_KEYS:
            assert key.startswith(CORE_FACT_KEY_PREFIX)
            for source in (FactSource.PROSPECT, FactSource.CONVERSATION):
                result = self.review(key=key, source=source, status=FactStatus.DISCOVERED)
                assert [r.reason for r in result.rejected] == [ProposalRejection.RESERVED_KEY]


class TestThroughTheEngine:
    """Nothing a Brain proposes or says, and no stage it sets, makes a prospect paid."""

    def world(self, decisions):
        clock = FakeClock(NOW)
        repo = FakeSalesRepository(CLIENT, prospects=[prospect("p1")])
        queue = list(decisions)
        self.brain = ScriptedBrain(lambda request: queue.pop(0) if queue else send("ok"))
        engine = Engine(
            client=make_client_context(),
            repository=repo,
            brain=self.brain,
            channel=FakeMessagingChannel(clock=clock),
            clock=clock,
            config=EngineConfig(mode=ApprovalMode.APPROVAL),
            facts=repo,
            facts_writer=repo,
        )
        return engine, repo

    def reply(self, engine, text="I paid", external_id="wamid.1"):
        return engine.process_inbound(
            engine.receive_inbound(inbound(text, external_id=external_id))
        )

    def proposal(self, **overrides):
        fields = {
            "key": PAYMENT_CLAIMED_KEY,
            "value": "claimed",
            "status": FactStatus.DISCOVERED,
            "source": FactSource.PROSPECT,
            "message_id": MessageId("in:whatsapp:wamid.1"),
        }
        return FactProposal(**(fields | overrides))

    def test_the_prospect_says_they_paid_and_only_a_claim_is_recorded(self):
        engine, repo = self.world([replace(send("Thanks"), proposed_facts=(self.proposal(),))])
        assert self.reply(engine).ok
        facts = list(repo.get_facts(P1))
        assert [f.key for f in facts] == [PAYMENT_CLAIMED_KEY]
        assert facts[0].status is FactStatus.DISCOVERED and not facts[0].is_confirmed
        assert payment_progress(facts) is PaymentProgress.CLAIMED

    def test_a_brain_that_tries_to_confirm_gets_nothing_recorded(self):
        forged = [
            self.proposal(
                key=PAYMENT_CONFIRMED_KEY, value="confirmed", status=FactStatus.CONFIRMED
            ),
            self.proposal(key=PAYMENT_CLAIMED_KEY, status=FactStatus.CONFIRMED),
            self.proposal(
                key=PAYMENT_CONFIRMED_KEY,
                source=FactSource.CONVERSATION,
                status=FactStatus.DISCOVERED,
            ),
        ]
        engine, repo = self.world([replace(send("Thanks"), proposed_facts=tuple(forged))])
        self.reply(engine)
        assert list(repo.get_facts(P1)) == []
        assert len(repo.audit_events) >= 3

    def test_a_stage_named_paid_is_not_payment(self):
        engine, repo = self.world([send("Thanks", stage="paid")])
        self.reply(engine)
        assert payment_progress(repo.get_facts(P1)) is PaymentProgress.NONE

    @pytest.mark.parametrize(
        "decision",
        [
            send("Paid, thank you!", stage="won"),
            decide(DecisionType.CLOSE, rationale="paid"),
            decide(DecisionType.HANDOFF_TO_HUMAN),
            decide(DecisionType.NO_ACTION, new_stage="won"),
            replace(send("Ok"), proposed_facts=()),
        ],
        ids=["send+stage", "close", "handoff", "no_action+stage", "send"],
    )
    def test_no_decision_of_a_brain_makes_the_prospect_paid(self, decision):
        engine, repo = self.world([decision])
        self.reply(engine, "I paid, it is confirmed")
        assert payment_progress(repo.get_facts(P1)) is PaymentProgress.NONE
        assert not any(f.key in CONFIRMATION_KEYS for f in repo.get_facts(P1))

    def test_only_the_person_makes_the_prospect_paid_and_a_later_claim_changes_nothing(self):
        engine, repo = self.world([replace(send("Thanks"), proposed_facts=(self.proposal(),))])
        self.reply(engine)
        service = PaymentConfirmationService(
            repository=repo, facts=repo, facts_writer=repo, clock=FakeClock(NOW)
        )
        assert payment_progress(repo.get_facts(P1)) is PaymentProgress.CLAIMED
        assert service.confirm(confirmation()).accepted
        assert payment_progress(repo.get_facts(P1)) is PaymentProgress.PAID
        self.reply(engine, "I really paid", "wamid.2")  # more talk afterwards: still paid, not less
        assert payment_progress(repo.get_facts(P1)) is PaymentProgress.PAID
        assert self.brain.calls[-1].facts  # and the next request sees the confirmation
        assert any(f.key == PAYMENT_CONFIRMED_KEY for f in self.brain.calls[-1].facts)
