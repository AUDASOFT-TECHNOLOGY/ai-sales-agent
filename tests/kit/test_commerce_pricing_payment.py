"""Slice 6b, prices and payment: a configured price is authoritative and chosen by the prospect's
known place; payment methods and their exact instructions are chosen deterministically by product,
market, country and currency, and never rewritten."""

import pytest
from brain_support import context_for, set_at
from commerce_support import (
    ACCOUNT_NAME,
    ACCOUNT_NUMBER,
    OTHER_ACCOUNT_NUMBER,
    PAY_URL,
    answer,
    commerce_doc,
    country,
    interested,
    make_brain,
    payload_of,
    request,
)

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative.applicability import Place
from sales_brain_kit.consultative.payment import applicable_methods, render_instruction
from sales_brain_kit.consultative.pricing import (
    PriceStatus,
    format_number,
    format_price,
    resolve_price,
)

SETTINGS = parse_settings(commerce_doc())
ALPHA, BETA = SETTINGS.catalog.products[0], SETTINGS.catalog.products[1]
NOWHERE = Place()
MARKET_A = Place(market_id="market-a", country="country a")
COUNTRY_IN_A = Place(market_id="market-a", country="country a")
MARKET_A_OTHER_COUNTRY = Place(market_id="market-a", country="ca")
MARKET_B = Place(market_id="market-b", country="country b")
ELSEWHERE = Place(country="country z")


class TestAPriceIsChosenByThePlaceAndIsAuthoritative:
    def test_the_products_own_price_when_the_place_is_unknown(self):
        price = resolve_price(ALPHA, NOWHERE)
        assert price.status is PriceStatus.STATED and price.variant_id is None
        assert (price.text, price.currency) == ("100 USD", "USD")

    def test_a_market_specific_price(self):
        price = resolve_price(ALPHA, MARKET_A_OTHER_COUNTRY)
        assert price.variant_id == "market-a-price" and price.text == "90 EUR"

    def test_a_country_specific_price_is_more_specific_than_its_market_s(self):
        price = resolve_price(ALPHA, COUNTRY_IN_A)
        assert price.variant_id == "country-a-price" and price.text == "85.5+ EUR"

    def test_a_price_range_in_another_currency(self):
        price = resolve_price(ALPHA, MARKET_B)
        assert price.text == "1000–1250.75 GBP" and price.currency == "GBP"

    def test_a_place_with_no_variant_gets_the_default_never_a_conversion(self):
        price = resolve_price(ALPHA, ELSEWHERE)
        assert price.variant_id is None and price.text == "100 USD"

    def test_a_variant_may_change_when_the_price_is_disclosed(self):
        assert resolve_price(ALPHA, NOWHERE).disclose == "after_interest"
        assert resolve_price(ALPHA, MARKET_B).disclose == "always"

    def test_no_configured_price_means_no_price_to_state(self):
        price = resolve_price(BETA, MARKET_A)
        assert price.status is PriceStatus.NOT_CONFIGURED and price.text is None

    def test_a_price_on_request_states_nothing(self):
        doc = set_at(commerce_doc(), "catalog.products.0.pricing", {"model": "on_request"})
        product = parse_settings(doc).catalog.products[0]
        price = resolve_price(product, MARKET_A)
        assert price.status is PriceStatus.ON_REQUEST and price.text is None

    def test_free_states_no_amount(self):
        doc = set_at(commerce_doc(), "catalog.products.0.pricing", {"model": "free"})
        price = resolve_price(parse_settings(doc).catalog.products[0], NOWHERE)
        assert price.status is PriceStatus.FREE and price.text is None

    def test_a_place_with_only_place_specific_prices_and_no_default_states_none_elsewhere(self):
        doc = set_at(commerce_doc(), "catalog.products.0.pricing.model", "on_request")
        doc = set_at(doc, "catalog.products.0.pricing.amount", None)
        doc["catalog"]["products"][0]["pricing"].pop("amount")
        doc["catalog"]["products"][0]["pricing"].pop("currency")
        product = parse_settings(doc).catalog.products[0]
        assert resolve_price(product, NOWHERE).text is None
        assert resolve_price(product, MARKET_A_OTHER_COUNTRY).text == "90 EUR"

    def test_ties_go_to_the_variant_declared_first(self):
        doc = set_at(commerce_doc(), "catalog.products.0.pricing.variants", [
            {"id": "first", "applies_to": {"markets": ["market-a"]}, "model": "fixed", "amount": 1, "currency": "EUR"},
            {"id": "second", "applies_to": {"countries": ["Country A"]}, "model": "fixed", "amount": 2, "currency": "EUR"},
            {"id": "third", "applies_to": {"markets": ["market-a"], "countries": ["Country A"]}, "model": "fixed", "amount": 3, "currency": "EUR"},
        ])  # fmt: skip
        product = parse_settings(doc).catalog.products[0]
        assert resolve_price(product, COUNTRY_IN_A).variant_id == "third"  # the most specific
        assert resolve_price(product, Place(market_id="market-a")).variant_id == "first"

    @pytest.mark.parametrize(
        ("amount", "text"),
        [(100, "100"), (85.5, "85.5"), (1250.75, "1250.75"), (19.99, "19.99"), (1000000, "1000000"),
         (0.5, "0.5"), (10.0, "10"), (2500.50, "2500.5")],
    )  # fmt: skip
    def test_amounts_are_written_exactly_as_configured(self, amount, text):
        assert format_number(amount) == text

    def test_a_price_is_a_configured_value_not_a_computation(self):
        doc = set_at(commerce_doc(), "catalog.products.0.pricing", {
            "model": "fixed", "amount": 19.99, "currency": "USD"})  # fmt: skip
        product = parse_settings(doc).catalog.products[0]
        assert format_price(product.pricing) == "19.99 USD"


class TestThePriceReachesTheProspectOnlyThroughAPlaceholder:
    def test_the_model_is_told_a_price_exists_but_never_what_it_is(self):
        brain, llm = make_brain(
            commerce_doc(), answer(selected_product="alpha", language="fr", message="Bonjour")
        )
        brain.decide(request(facts=[interested(), country("Country A")]))
        prompt = llm.calls[0].messages[1].content
        price = payload_of(llm)["catalog"]["products"][0]["price"]
        assert price["status"] == "stated" and price["placeholder"] == "[[price:alpha]]"
        assert not any(text in prompt for text in ("85.5", "1250", "USD", "EUR", "GBP", "amount"))

    def test_the_prompt_says_when_the_price_depends_on_a_place_that_is_not_known(self):
        brain, llm = make_brain(commerce_doc(), answer(selected_product="alpha"))
        brain.decide(request(facts=[interested()]))
        price = payload_of(llm)["catalog"]["products"][0]["price"]
        assert price["varies_by_place"] is True and price["place_known"] is False

    def test_a_product_without_a_price_offers_no_placeholder(self):
        brain, llm = make_brain(commerce_doc(), answer(selected_product="beta"))
        brain.decide(request(facts=[interested("beta")]))
        assert payload_of(llm)["catalog"]["products"][0]["price"] == {"status": "not_configured"}
        offered = [v["placeholder"] for v in payload_of(llm)["exact_values"]["available"]]
        assert "[[price:beta]]" not in offered

    def test_the_placeholder_becomes_the_configured_price_for_the_prospects_place(self):
        for place, expected in (("Country A", "85.5+ EUR"), ("Country B", "1000–1250.75 GBP")):
            brain, _ = make_brain(
                commerce_doc(),
                answer(language="fr" if place == "Country A" else "ar",
                       message="Le prix est [[price:alpha]].", selected_product="alpha"),
            )  # fmt: skip
            decision = brain.decide(request(facts=[interested(), country(place)]))
            assert decision.message.text == f"Le prix est {expected}."

    def test_the_default_price_when_no_country_is_known(self):
        brain, _ = make_brain(
            commerce_doc(), answer(message="It costs [[price:alpha]].", selected_product="alpha")
        )
        decision = brain.decide(request(facts=[interested()]))
        assert decision.message.text == "It costs 100 USD."
        assert decision.metadata["exact_values"] == "[[price:alpha]]"


class TestWhichPaymentMethodsApply:
    def offers(self, place, currency, *, only=(), product="alpha", settings=SETTINGS):
        found = applicable_methods(
            settings.payment, product_id=product, place=place, currency=currency, only=only
        )
        return [(o.method.id, o.instruction.id) for o in found]

    def test_an_online_method_and_an_offline_one(self):
        assert self.offers(MARKET_A, "EUR") == [
            ("online-card", "default"),
            ("bank-transfer", "default"),
        ]

    def test_availability_by_currency(self):
        assert [m for m, _ in self.offers(MARKET_B, "GBP")] == ["bank-transfer", "cash"]
        assert [m for m, _ in self.offers(MARKET_A, "USD")][:1] == ["online-card"]

    def test_an_unknown_currency_never_matches_a_method_restricted_by_currency(self):
        assert [m for m, _ in self.offers(MARKET_A, None)] == ["bank-transfer"]

    def test_availability_by_country(self):
        assert "cash" in [m for m, _ in self.offers(MARKET_B, "GBP")]
        assert "cash" not in [m for m, _ in self.offers(MARKET_A, "EUR")]
        assert "cash" not in [m for m, _ in self.offers(NOWHERE, "EUR")]  # the country is unknown

    def test_availability_by_product(self):
        doc = set_at(commerce_doc(), "payment.methods.2.applies_to", {"products": ["beta"]})
        settings = parse_settings(doc)
        assert "cash" not in [m for m, _ in self.offers(MARKET_B, "GBP", settings=settings)]
        assert "cash" in [
            m for m, _ in self.offers(MARKET_B, "GBP", product="beta", settings=settings)
        ]

    def test_availability_by_market(self):
        doc = set_at(commerce_doc(), "payment.methods.0.applies_to", {"markets": ["market-b"]})
        settings = parse_settings(doc)
        assert "online-card" not in [m for m, _ in self.offers(MARKET_A, "EUR", settings=settings)]
        assert "online-card" in [m for m, _ in self.offers(MARKET_B, "EUR", settings=settings)]

    def test_the_most_specific_instruction_of_a_method_is_used(self):
        assert dict(self.offers(MARKET_A, "EUR"))["bank-transfer"] == "default"
        assert dict(self.offers(MARKET_B, "GBP"))["bank-transfer"] == "for-market-b"

    def test_a_method_that_is_not_available_is_not_offered(self):
        assert self.offers(MARKET_A, "EUR", only=["cash"]) == []

    def test_an_inactive_method_is_never_offered(self):
        doc = set_at(commerce_doc(), "payment.methods.0.status", "inactive")
        settings = parse_settings(doc)
        assert "online-card" not in [m for m, _ in self.offers(MARKET_A, "EUR", settings=settings)]

    def test_no_payment_configuration_means_nothing_to_offer(self):
        doc = set_at(commerce_doc(), "payment.methods", [])
        doc["conversion"] = {}  # a workflow with a payment step could not be valid without one
        assert self.offers(MARKET_A, "EUR", settings=parse_settings(doc)) == []

    def test_a_method_can_be_named_by_the_step(self):
        assert self.offers(MARKET_A, "EUR", only=["bank-transfer"]) == [
            ("bank-transfer", "default")
        ]

    def test_declared_order_settles_equal_matches(self):
        doc = set_at(commerce_doc(), "payment.methods.1.instructions", [
            {"id": "one", "applies_to": {"markets": ["market-a"]}, "text": "First"},
            {"id": "two", "applies_to": {"countries": ["Country A"]}, "text": "Second"}])  # fmt: skip
        # a country is more specific than a market
        settings = parse_settings(doc)
        assert dict(self.offers(COUNTRY_IN_A, "EUR", settings=settings))["bank-transfer"] == "two"


class TestPaymentValuesAreNeverRewritten:
    def offer(self, place, currency, method):
        found = applicable_methods(
            SETTINGS.payment, product_id="alpha", place=place, currency=currency, only=[method]
        )
        return found[0].instruction

    def test_fields_and_text_are_kept_character_for_character(self):
        text = render_instruction(self.offer(MARKET_A, "EUR", "bank-transfer"), "en", "en")
        assert text == f"Transfer to:\nAccount name: {ACCOUNT_NAME}\nAccount: {ACCOUNT_NUMBER}"
        assert "Acme  Test" in text  # the double space is part of the value

    def test_the_instruction_is_given_in_the_message_language(self):
        text = render_instruction(self.offer(MARKET_A, "EUR", "bank-transfer"), "fr", "en")
        assert text.startswith("Virement vers :\nTitulaire: ")
        assert text.endswith(ACCOUNT_NUMBER)  # the value itself is not translated

    def test_a_language_without_text_falls_back_to_the_clients_default(self):
        text = render_instruction(self.offer(MARKET_A, "EUR", "bank-transfer"), "ar", "en")
        assert text.startswith("Transfer to:")

    def test_an_online_instruction_carries_its_url_untouched(self):
        text = render_instruction(self.offer(MARKET_A, "EUR", "online-card"), "en", "en")
        assert text == f"Pay securely here:\n{PAY_URL}"

    def test_the_place_specific_account_is_the_one_given(self):
        text = render_instruction(self.offer(MARKET_B, "GBP", "bank-transfer"), "en", "en")
        assert OTHER_ACCOUNT_NUMBER in text and ACCOUNT_NUMBER not in text

    def test_rendering_does_not_depend_on_anything_but_the_configuration(self):
        first = render_instruction(self.offer(MARKET_A, "EUR", "bank-transfer"), "en", "en")
        second = render_instruction(self.offer(MARKET_A, "EUR", "bank-transfer"), "en", "en")
        assert first == second


class TestPaymentValuesNeverReachTheModel:
    def test_no_account_number_or_payment_link_is_in_anything_the_model_receives(self):
        from commerce_support import at_payment

        brain, llm = make_brain(
            commerce_doc(), answer(message="See [[payment:bank-transfer]]", language="fr")
        )
        from commerce_support import ONGOING

        brain.decide(request(facts=at_payment(), **ONGOING))
        everything = "\n".join(m.content for m in llm.calls[0].messages)
        for secret in (
            ACCOUNT_NUMBER,
            OTHER_ACCOUNT_NUMBER,
            ACCOUNT_NAME,
            PAY_URL,
            "pay.example.org",
        ):
            assert secret not in everything
        assert "[[payment:bank-transfer]]" in everything  # only the way to point at it

    def test_the_context_for_an_operator_also_hides_them(self):
        ctx = context_for(commerce_doc(), facts=[interested(), country("Country A")])
        assert ACCOUNT_NUMBER not in repr(ctx.plan.workflow.progress)
