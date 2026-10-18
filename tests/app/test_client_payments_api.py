"""`/client/v1/payments`: a client checks and confirms a payment its buyer says they made.

SIMULATED here: the messaging channel, the Brain, the model, the prospect source (a fake) and the
client login. REAL here: the application, the routes, the session cookie, the runtime lease, the
Engine and the payment confirmation service (the Core's one door). Nothing here proves a real bank,
card or payment provider: Revansa confirms nothing by itself.
"""

import pytest
from engine_harness import prospect
from factories import NOW
from test_client_agent_api import World

from sales_core.contracts import (
    PAYMENT_CLAIMED_KEY,
    PAYMENT_PROOF_KEY,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    ProspectId,
    Scheduling,
)

LIST = "/client/v1/payments"


def confirm(prospect_id):
    return f"{LIST}/{prospect_id}/confirm"


TERMS = {"amount": "125.50", "currency": "TND", "reference": "VIR-2026-001"}


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.application.close()


def claim(world, cid, pid, key=PAYMENT_CLAIMED_KEY, name="Alice"):
    """The prospect wrote back and said they paid (a fact recorded as what it is: a claim)."""
    repository = world.made.repositories[cid]
    repository.add_prospects(
        prospect(pid, client_id=cid, display_name=name, schedule=Scheduling(last_inbound_at=NOW))
    )
    repository.append_facts(
        ProspectId(pid),
        [
            Fact(
                key=key,
                value="claimed",
                status=FactStatus.DISCOVERED,
                evidence=FactEvidence(source=FactSource.PROSPECT),
                recorded_at=NOW,
            )
        ],
    )


class TestTheRoutes:
    def test_they_need_a_client_session(self, world):
        anonymous = world.anonymous()
        assert anonymous.get(LIST).status_code == 401
        assert anonymous.post(confirm("p1"), json=TERMS).status_code == 401

    def test_nothing_is_listed_before_anyone_speaks_of_paying(self, world):
        response = world.browser("alpha").get(LIST)
        assert response.status_code == 200 and response.json() == {"cases": []}
        assert response.headers["cache-control"] == "no-store"

    def test_a_claim_is_listed_as_a_claim_not_as_paid(self, world):
        claim(world, "alpha", "p1")
        (case,) = world.browser("alpha").get(LIST).json()["cases"]
        assert case["prospect_name"] == "Alice" and case["progress"] == "claimed"
        assert case["confirmed_at"] is None and case["amount"] is None

    def test_a_receipt_is_listed_as_a_receipt(self, world):
        claim(world, "alpha", "p1", key=PAYMENT_PROOF_KEY)
        (case,) = world.browser("alpha").get(LIST).json()["cases"]
        assert case["progress"] == "proof_received"


class TestConfirming:
    def test_the_client_confirms_after_checking_and_the_payment_becomes_paid(self, world):
        claim(world, "alpha", "p1")
        browser = world.browser("alpha")
        response = browser.post(confirm("p1"), json=TERMS)
        assert response.status_code == 200, response.text
        case = response.json()
        assert case["progress"] == "paid"
        assert (case["amount"], case["currency"], case["reference"]) == (
            "125.50",
            "TND",
            "VIR-2026-001",
        )
        assert case["confirmed_by"] == "client:alpha"
        assert browser.get(LIST).json()["cases"][0]["progress"] == "paid"

    def test_confirming_twice_changes_nothing(self, world):
        claim(world, "alpha", "p1")
        browser = world.browser("alpha")
        assert browser.post(confirm("p1"), json=TERMS).status_code == 200
        assert browser.post(confirm("p1"), json=TERMS).status_code == 200
        assert len(browser.get(LIST).json()["cases"]) == 1

    def test_the_same_reference_with_other_terms_is_a_conflict(self, world):
        claim(world, "alpha", "p1")
        browser = world.browser("alpha")
        browser.post(confirm("p1"), json=TERMS)
        other = browser.post(confirm("p1"), json={**TERMS, "amount": "1"})
        assert other.status_code == 409

    def test_an_unknown_prospect_is_not_found(self, world):
        assert world.browser("alpha").post(confirm("nobody"), json=TERMS).status_code == 404

    @pytest.mark.parametrize(
        "terms",
        [
            {**TERMS, "amount": "0"},
            {**TERMS, "amount": "-5"},
            {**TERMS, "amount": "abc"},
            {**TERMS, "amount": "NaN"},
            {**TERMS, "amount": "1" * 30},
            {**TERMS, "currency": "tnd"},
            {**TERMS, "currency": "DINAR"},
            {**TERMS, "reference": "  "},
            {"amount": "10", "currency": "TND"},
            {**TERMS, "provider": "stripe"},
        ],
    )
    def test_terms_that_are_not_valid_are_refused_and_nothing_is_recorded(self, world, terms):
        claim(world, "alpha", "p1")
        browser = world.browser("alpha")
        assert browser.post(confirm("p1"), json=terms).status_code == 422
        assert browser.get(LIST).json()["cases"][0]["progress"] == "claimed"

    def test_an_agent_that_takes_no_work_answers_a_conflict(self, world):
        # gamma lacks its platform credentials: it is not ready
        assert world.browser("gamma").get(LIST).status_code == 409


class TestClientsNeverMeet:
    def test_one_client_never_sees_or_confirms_another_clients_payments(self, world):
        claim(world, "alpha", "p1", name="Alpha buyer")
        claim(world, "beta", "p1", name="Beta buyer")
        alpha, beta = world.browser("alpha"), world.browser("beta")

        assert [c["prospect_name"] for c in alpha.get(LIST).json()["cases"]] == ["Alpha buyer"]
        assert [c["prospect_name"] for c in beta.get(LIST).json()["cases"]] == ["Beta buyer"]

        alpha.post(confirm("p1"), json=TERMS)
        assert alpha.get(LIST).json()["cases"][0]["progress"] == "paid"
        assert beta.get(LIST).json()["cases"][0]["progress"] == "claimed"  # untouched
