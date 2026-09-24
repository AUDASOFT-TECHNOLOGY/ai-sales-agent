"""Slice 6b, output safety: whatever the model writes, a price, a payment detail or a link reaches the
prospect only if the configuration put it there. The model never receives these values; it can only
point at them, and anything it writes itself that looks like one is refused."""

import json

import pytest
from brain_support import set_at
from commerce_support import (
    ACCOUNT_NUMBER,
    ONGOING,
    PAY_URL,
    answer,
    at_payment,
    commerce_doc,
    interested,
    make_brain,
    request,
)

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError
from sales_brain_kit.consultative.exact import (
    Placeholder,
    configured_links,
    expand,
    find_placeholders,
    unexpected_content,
    without_placeholders,
)

FACTS = at_payment("Country A")


def outcome(message: str, *, doc=None, facts=FACTS, language="fr", **fields) -> str:
    """The text sent, or the reasons the answer was refused."""
    scripted = answer(message=message, language=language, **fields)
    brain, _ = make_brain(doc or commerce_doc(), scripted, scripted)
    try:
        return brain.decide(request(facts=list(facts), **ONGOING)).message.text
    except BrainOutputError as error:
        return "REFUSED: " + str(error)


def is_refused(text: str, fragment: str = "") -> bool:
    return text.startswith("REFUSED") and fragment in text


class TestTheModelCannotInventPaymentDetails:
    @pytest.mark.parametrize(
        "invented",
        [
            "Send it to account 123456789012.",  # a long digit run
            "IBAN: XX12 3456 7890 1234 5678",  # grouped digits
            "Account XX12345678901234567890",  # IBAN-like
            "Pay to 1234 5678 9012 3456",  # card-like groups
            "Compte 00123456789",
        ],
    )
    def test_an_account_number_it_wrote_is_refused(self, invented):
        assert is_refused(outcome(invented), "account-like number")

    def test_the_configured_account_is_not_accepted_when_typed_by_the_model_either(self):
        assert is_refused(outcome(f"Transfer to {ACCOUNT_NUMBER}"), "account-like number")

    def test_a_payment_link_it_wrote_is_refused(self):
        assert is_refused(outcome("Pay at https://pay.example.org/other"), "not a configured link")

    def test_even_the_real_payment_link_is_refused_when_typed_by_the_model(self):
        assert is_refused(outcome(f"Pay at {PAY_URL}"), "not a configured link")

    @pytest.mark.parametrize(
        "text",
        [
            "Pay at www.example.net now",
            "Pay at shop.example.net/checkout",
            "http://example.net/pay",
        ],
    )
    def test_any_web_address_it_wrote_is_refused(self, text):
        assert is_refused(outcome(text), "not a configured link")

    def test_a_placeholder_for_a_method_that_does_not_exist_is_refused(self):
        assert is_refused(outcome("Use [[payment:crypto]]"), "[[payment:crypto]] is not available")

    def test_a_placeholder_of_another_kind_is_not_a_placeholder(self):
        assert is_refused(outcome("Use [[iban:main]]"), "malformed")

    @pytest.mark.parametrize(
        "text",
        ["[[payment: bank-transfer]]", "[[Payment:bank-transfer]]", "[[payment:BANK]]",
         "[payment:bank-transfer]", "[[payment:bank-transfer]", "{{payment:bank-transfer}}"],
    )  # fmt: skip
    def test_a_malformed_placeholder_is_never_repaired_or_sent(self, text):
        result = outcome(text)
        assert is_refused(result)

    def test_the_configured_details_reach_the_prospect_when_the_placeholder_is_used(self):
        result = outcome("Here it is:\n[[payment:bank-transfer]]")
        assert ACCOUNT_NUMBER in result and result.startswith("Here it is:\nVirement vers :")

    def test_the_same_placeholder_twice_gives_the_same_text_twice(self):
        result = outcome("[[payment:bank-transfer]]\n---\n[[payment:bank-transfer]]")
        assert result.count(ACCOUNT_NUMBER) == 2


class TestTheModelCannotInventPrices:
    @pytest.mark.parametrize(
        "text",
        ["It costs 100 USD.", "It costs USD 100.", "Only $99 today.", "Only 99$ today.",
         "For €90 a month.", "90€ only", "About 50 XYZ", "EUR 12.5 per seat"],
    )  # fmt: skip
    def test_an_amount_with_a_currency_it_wrote_is_refused(self, text):
        assert is_refused(outcome(text), "use the [[price:id]] placeholder")

    def test_including_a_currency_the_client_does_not_use(self):
        assert is_refused(outcome("That is 40 JPY."), "amount with a currency")

    def test_the_price_comes_only_from_the_placeholder(self):
        result = outcome("It costs [[price:alpha]].", selected_product="alpha")
        assert result == "It costs 85.5+ EUR."

    def test_a_placeholder_for_a_product_with_no_price_is_refused(self):
        assert is_refused(
            outcome("It costs [[price:beta]].", facts=[*FACTS, interested("beta")]),
            "[[price:beta]] is not available",
        )

    def test_a_placeholder_for_a_product_that_may_not_be_offered_is_refused(self):
        doc = set_at(commerce_doc(), "catalog.products.0.status", "inactive")
        assert is_refused(outcome("It costs [[price:alpha]].", doc=doc), "is not available")

    @pytest.mark.parametrize(
        "text",
        ["It lasts 3 sessions.", "Ready in 2 days.", "Available 24h a day.", "Start on 2026-01-15.",
         "Call us between 9 and 17.", "We have 12 seats left.", "Version 2 of the course."],
    )  # fmt: skip
    def test_ordinary_numbers_are_fine(self, text):
        assert not outcome(text).startswith("REFUSED")


class TestOnlyConfiguredLinksAreAllowed:
    def test_the_products_own_link_may_be_shared(self):
        result = outcome("More at https://example.org/alpha.", selected_product="alpha")
        assert result.endswith("https://example.org/alpha.")

    def test_a_link_with_a_changed_path_is_not_the_configured_link(self):
        assert is_refused(
            outcome("More at https://example.org/alpha/../evil"), "not a configured link"
        )

    def test_a_link_of_a_look_alike_domain_is_refused(self):
        assert is_refused(
            outcome("More at https://example.org.evil.net/alpha"), "not a configured link"
        )

    def test_the_configured_links_do_not_include_payment_or_booking_links(self):
        doc = commerce_doc()
        doc["conversion"]["appointment"] = {
            "supported": True, "mode": "booking_url", "booking_url": "https://book.example.org/x"}  # fmt: skip
        links = configured_links(parse_settings(doc))
        assert PAY_URL.rstrip("/") not in links and "https://book.example.org/x" not in links
        assert "https://example.org/alpha" in links


class TestTheModelIsToldOnceWhatWasWrong:
    def test_a_refused_answer_is_corrected_with_the_reasons(self):
        bad = answer(message="Send 100 USD to 123456789012", language="fr")
        good = answer(message="[[payment:bank-transfer]]", language="fr")
        brain, llm = make_brain(commerce_doc(), bad, good)
        decision = brain.decide(request(facts=list(FACTS), **ONGOING))
        assert ACCOUNT_NUMBER in decision.message.text
        feedback = llm.calls[1].messages[-1].content
        assert "amount with a currency" in feedback and "account-like number" in feedback

    def test_two_bad_answers_end_without_a_decision(self):
        bad = answer(message="Send 100 USD", language="fr")
        brain, _ = make_brain(commerce_doc(), bad, bad)
        with pytest.raises(BrainOutputError):
            brain.decide(request(facts=list(FACTS), **ONGOING))

    def test_the_model_is_not_told_the_values_even_when_correcting_it(self):
        bad = answer(message="Pay 999 EUR", language="fr")
        good = answer(message="[[payment:bank-transfer]]", language="fr")
        brain, llm = make_brain(commerce_doc(), bad, good)
        brain.decide(request(facts=list(FACTS), **ONGOING))
        everything = json.dumps([[m.content for m in call.messages] for call in llm.calls])
        assert ACCOUNT_NUMBER not in everything and PAY_URL not in everything


class TestPlaceholderMechanics:
    def test_they_are_found_in_order_with_repeats(self):
        text = "a [[price:x]] b [[payment:y]] c [[price:x]]"
        assert find_placeholders(text) == (
            Placeholder("price", "x"), Placeholder("payment", "y"), Placeholder("price", "x"),
        )  # fmt: skip

    def test_expansion_replaces_only_placeholders_and_keeps_every_other_character(self):
        values = {Placeholder("price", "x"): "100 XXX"}
        text = "Total:  [[price:x]]!  (two spaces)\n"
        assert expand(text, values) == "Total:  100 XXX!  (two spaces)\n"

    def test_an_expanded_value_is_not_expanded_again(self):
        values = {Placeholder("price", "x"): "[[payment:y]]"}
        assert expand("[[price:x]]", values) == "[[payment:y]]"

    def test_what_remains_after_removing_placeholders_is_what_the_model_wrote(self):
        assert without_placeholders("a[[price:x]]b").split() == ["a", "b"]
        assert unexpected_content(without_placeholders("Price: [[price:x]]"), ()) == []

    def test_clean_text_has_nothing_unexpected(self):
        assert unexpected_content("Hello, we start on Monday at 9:30 with 12 people.", ()) == []

    def test_an_over_long_expansion_is_refused(self):
        huge = "x" * 1400
        doc = commerce_doc()
        doc["payment"]["methods"][1]["instructions"][0]["text"] = huge
        doc["payment"]["methods"][1]["instructions"][0]["fields"] = [
            {"label": "L", "value": "y" * 190} for _ in range(3)]  # fmt: skip
        message = "[[payment:bank-transfer]]\n" * 3
        result = outcome(message, doc=doc, language="en")
        assert is_refused(result, "characters once they are inserted")

    def test_questions_are_counted_in_what_the_model_wrote_not_in_the_configured_text(self):
        doc = commerce_doc()
        doc["payment"]["methods"][1]["instructions"][0]["text"] = "Why? How? When? Where?"
        result = outcome("Here you go [[payment:bank-transfer]]", doc=doc, language="fr")
        assert "Why? How? When? Where?" in result
