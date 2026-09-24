"""The catalog, product selection and product interest: zero, one or many products, unknown to
confirmed interest, several interests at once."""

import pytest
from brain_support import (
    CONFIRMED,
    DISCOVERED,
    HYPOTHESIS,
    REPLY,
    answer,
    context_for,
    decide,
    doc_with,
    fact,
    handoff,
    make_brain,
    payload_of,
    request,
    set_at,
    settings_doc,
    with_products,
    without_catalog,
)

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError
from sales_brain_kit.consultative.catalog import ProductCatalog
from sales_brain_kit.consultative.interest import InterestLevel
from sales_brain_kit.consultative.selection import SelectionBasis
from sales_brain_kit.consultative.strategy import Objective


def catalog() -> ProductCatalog:
    return ProductCatalog(parse_settings(settings_doc()).catalog, "en")


class TestTheCatalog:
    def test_finds_a_product_by_id_name_or_alias_ignoring_case(self):
        products = catalog()
        assert products.resolve("alpha").id == "alpha"
        assert products.resolve("  ALPHA course ").id == "alpha"
        assert products.resolve("cours alpha").id == "alpha"  # a localised name
        assert products.resolve("Beta").id == "beta"  # an alias
        assert products.resolve("unknown") is None
        assert products.resolve("   ") is None

    def test_a_reference_can_be_limited_to_some_kinds_of_match(self):
        assert catalog().resolve("Cours Alpha", ("id",)) is None
        assert catalog().resolve("Cours Alpha", ("name",)).id == "alpha"
        assert catalog().resolve("Beta", ("aliases",)).id == "beta"

    def test_says_why_a_product_may_not_be_offered(self):
        products = catalog()
        assert products.exclusion(products.get("delta"), "en") == "inactive"
        assert products.exclusion(products.get("gamma"), "en") is None  # coming soon may be shown
        assert products.exclusion(products.get("alpha"), "en") is None

    def test_a_product_restricted_to_some_languages_is_not_offered_in_others(self):
        doc = set_at(settings_doc(), "catalog.products.1.restrictions", {"languages": ["fr"]})
        products = ProductCatalog(parse_settings(doc).catalog, "en")
        beta = products.get("beta")
        assert products.exclusion(beta, "en") == "not offered in en"
        assert products.exclusion(beta, "fr") is None
        assert products.exclusion(beta, "fr-CA") is None  # same language, other region


class TestZeroProducts:
    def test_a_client_without_a_catalog_is_valid_and_has_no_focus(self):
        ctx = context_for(without_catalog())
        assert ctx.selection.basis is SelectionBasis.NO_CATALOG
        assert ctx.selection.focus == () and ctx.selection.candidates == ()
        assert ctx.plan.may_present is False and ctx.plan.must_present is False

    def test_it_can_still_open_a_conversation(self):
        decision = decide(answer(), doc=without_catalog())
        assert decision.message.text == "Hello! Glad to help."
        assert decision.metadata["objective"] == "open_conversation"

    def test_it_never_invents_a_product_to_present(self):
        with pytest.raises(BrainOutputError) as error:
            decide(
                answer(presented_products=["alpha"], selected_product="alpha"),
                answer(presented_products=["alpha"], selected_product="alpha"),
                doc=without_catalog(),
            )
        assert "not in the catalog" in str(error.value)

    def test_there_is_nothing_to_recommend_so_nothing_is_asked_to_choose_between(self):
        ctx = context_for(without_catalog())
        assert ctx.plan.discovery.status.value == "not_needed"
        assert ctx.plan.discovery.target_keys == ()


class TestOneProduct:
    def doc(self):
        return with_products("beta")

    def test_a_sole_offer_is_the_offer_even_without_a_known_interest(self):
        ctx = context_for(self.doc())
        assert ctx.selection.basis is SelectionBasis.SOLE_OFFER
        assert [p.id for p in ctx.selection.focus] == ["beta"]
        assert ctx.selection.interests == ()  # it is not an interest, and never becomes one
        assert ctx.plan.objective is Objective.PRESENT_OFFER

    def test_nothing_is_asked_when_there_is_nothing_to_choose(self):
        assert context_for(self.doc()).plan.discovery.status.value == "not_needed"

    def test_the_message_must_present_the_product(self):
        with pytest.raises(BrainOutputError) as error:
            decide(answer(), answer(), doc=self.doc())
        assert "must present a product" in str(error.value)
        decision = decide(answer(selected_product="beta"), doc=self.doc())
        assert decision.metadata["selected_product"] == "beta"
        assert decision.metadata["presented_products"] == "beta"

    def test_one_inactive_product_means_no_suitable_product(self):
        ctx = context_for(with_products("delta"))
        assert ctx.selection.basis is SelectionBasis.NONE_SUITABLE


class TestManyProducts:
    def test_with_no_known_interest_the_choice_is_unresolved(self):
        ctx = context_for()
        assert ctx.selection.basis is SelectionBasis.UNRESOLVED
        assert ctx.selection.focus == ()
        assert [p.id for p in ctx.selection.candidates] == ["alpha", "beta", "gamma"]
        assert ctx.selection.excluded == {"delta": "inactive"}

    def test_the_strongest_interest_decides_the_focus(self):
        ctx = context_for(facts=[fact("interest", "beta", CONFIRMED)])
        assert ctx.selection.basis is SelectionBasis.INTEREST
        assert [p.id for p in ctx.selection.focus] == ["beta"]
        assert ctx.selection.candidates[0].id == "beta"  # ranked first among candidates

    def test_a_weaker_interest_stays_a_candidate_but_not_the_focus(self):
        ctx = context_for(
            facts=[fact("interest", "beta", CONFIRMED), fact("interest", "alpha", DISCOVERED)]
        )
        assert [p.id for p in ctx.selection.focus] == ["beta"]
        assert [p.id for p in ctx.selection.candidates][:2] == ["beta", "alpha"]

    def test_the_focus_is_capped_by_the_presentation_limit(self):
        doc = set_at(settings_doc(), "catalog.presentation.max_products_per_message", 1)
        ctx = context_for(
            doc, facts=[fact("interest", "alpha", CONFIRMED), fact("interest", "beta", CONFIRMED)]
        )
        assert [p.id for p in ctx.selection.focus] == ["alpha"]

    def test_the_message_may_present_only_what_the_presentation_settings_allow(self):
        facts = [fact("interest", "alpha", CONFIRMED)]
        too_many = answer(presented_products=["alpha", "beta", "gamma"], selected_product="alpha")
        with pytest.raises(BrainOutputError) as error:
            decide(too_many, too_many, facts=facts)
        assert "at most 2 product(s)" in str(error.value)

        doc = set_at(settings_doc(), "catalog.presentation.style", "single_best")
        two = answer(presented_products=["alpha", "beta"], selected_product="alpha")
        with pytest.raises(BrainOutputError) as error:
            decide(two, two, doc=doc, facts=facts)
        assert "at most 1 product(s)" in str(error.value)

    def test_an_inactive_product_is_never_offered(self):
        facts = [fact("interest", "alpha", CONFIRMED)]
        bad = answer(presented_products=["delta"], selected_product="delta")
        with pytest.raises(BrainOutputError) as error:
            decide(bad, bad, facts=facts)
        assert "'delta' may not be offered (inactive)" in str(error.value)

    def test_a_coming_soon_product_may_be_presented(self):
        facts = [fact("interest", "gamma", CONFIRMED)]
        decision = decide(answer(selected_product="gamma"), facts=facts)
        assert decision.metadata["selected_product"] == "gamma"

    def test_a_product_language_restriction_applies_to_the_reply_language(self):
        doc = set_at(settings_doc(), "catalog.products.1.restrictions", {"languages": ["fr"]})
        facts = [fact("interest", "beta", CONFIRMED)]
        ctx = context_for(doc, facts=facts)
        assert ctx.selection.excluded["beta"] == "not offered in en"
        assert ctx.selection.interests == ()  # an interest in what cannot be offered is set aside
        assert ctx.selection.focus == ()


class TestNoSuitableProduct:
    def doc(self):
        return with_products("delta")

    def test_nothing_may_be_presented(self):
        ctx = context_for(self.doc())
        assert ctx.plan.may_present is False
        bad = answer(presented_products=["delta"], selected_product="delta")
        with pytest.raises(BrainOutputError):
            decide(bad, bad, doc=self.doc())

    def test_the_brain_can_still_talk_to_the_prospect_or_step_aside(self):
        assert decide(answer(), doc=self.doc()).decision_type.value == "send_message"
        assert decide(handoff(), doc=self.doc()).decision_type.value == "handoff_to_human"

    def test_an_interest_in_a_product_that_cannot_be_offered_does_not_count(self):
        ctx = context_for(facts=[fact("interest", "delta", CONFIRMED)])
        assert ctx.selection.interests == ()
        assert ctx.selection.basis is SelectionBasis.UNRESOLVED


class TestProductInterest:
    def levels(self, **options):
        ctx = context_for(**options)
        return {i.product_id: i.level for i in ctx.selection.interests}

    def test_unknown_interest(self):
        ctx = context_for()
        assert ctx.selection.interests == ()
        assert ctx.selection.strongest is None

    def test_a_hypothesis_is_a_possible_interest(self):
        levels = self.levels(facts=[fact("interest", "alpha", HYPOTHESIS, confidence=0.8)])
        assert levels == {"alpha": InterestLevel.POSSIBLE}

    def test_a_discovered_fact_is_a_probable_interest(self):
        assert self.levels(facts=[fact("interest", "alpha", DISCOVERED)]) == {
            "alpha": InterestLevel.PROBABLE
        }

    def test_a_confirmed_fact_is_a_confirmed_interest(self):
        assert self.levels(facts=[fact("interest", "alpha", CONFIRMED)]) == {
            "alpha": InterestLevel.CONFIRMED
        }

    def test_several_products_can_be_of_interest_at_once(self):
        levels = self.levels(
            facts=[
                fact("interest", "alpha", CONFIRMED),
                fact("interest", "beta", CONFIRMED),
                fact("interest", "gamma", HYPOTHESIS, confidence=0.9),
            ]
        )
        assert levels == {
            "alpha": InterestLevel.CONFIRMED,
            "beta": InterestLevel.CONFIRMED,
            "gamma": InterestLevel.POSSIBLE,
        }
        ctx = context_for(
            facts=[fact("interest", "alpha", CONFIRMED), fact("interest", "beta", CONFIRMED)]
        )
        assert [p.id for p in ctx.selection.focus] == ["alpha", "beta"]

    def test_the_strongest_observation_of_a_product_wins(self):
        levels = self.levels(
            facts=[
                fact("interest", "alpha", CONFIRMED),
                fact("interest", "alpha", HYPOTHESIS, confidence=1),
            ]
        )
        assert levels == {"alpha": InterestLevel.CONFIRMED}

    def test_a_hypothesis_never_becomes_a_confirmed_interest(self):
        levels = self.levels(facts=[fact("interest", "alpha", HYPOTHESIS, confidence=1.0)])
        assert levels["alpha"] is InterestLevel.POSSIBLE
        assert levels["alpha"] is not InterestLevel.CONFIRMED

    def test_a_hypothesis_below_the_configured_confidence_is_not_used(self):
        doc = set_at(settings_doc(), "discovery.hypotheses.min_confidence_to_use", 0.7)
        assert self.levels_for(doc, fact("interest", "alpha", HYPOTHESIS, confidence=0.6)) == {}
        assert self.levels_for(doc, fact("interest", "alpha", HYPOTHESIS, confidence=0.7)) != {}
        assert self.levels_for(doc, fact("interest", "alpha", HYPOTHESIS)) == {}  # no confidence

    def levels_for(self, doc, *facts):
        ctx = context_for(doc, facts=facts)
        return {i.product_id: i.level for i in ctx.selection.interests}

    def test_the_interest_can_name_the_product_by_alias_or_name(self):
        assert self.levels(facts=[fact("interest", "Cours Alpha", CONFIRMED)]) == {
            "alpha": InterestLevel.CONFIRMED
        }
        assert self.levels(facts=[fact("interest", "beta", DISCOVERED)]) == {
            "beta": InterestLevel.PROBABLE
        }

    def test_a_value_that_names_no_product_is_ignored(self):
        assert self.levels(facts=[fact("interest", "something else", CONFIRMED)]) == {}

    def test_a_source_column_gives_an_interest_at_the_configured_state(self):
        assert self.levels(attributes={"product": "Alpha"}) == {"alpha": InterestLevel.PROBABLE}
        doc = set_at(settings_doc(), "catalog.source_interest.state", "confirmed")
        assert self.levels_from_lead(doc, {"Product": "beta"}) == {"beta": InterestLevel.CONFIRMED}

    def levels_from_lead(self, doc, attributes):
        ctx = context_for(doc, attributes=attributes)
        return {i.product_id: i.level for i in ctx.selection.interests}

    def test_a_source_cell_may_list_several_products(self):
        levels = self.levels(attributes={"Product": "Alpha; Beta"})
        assert set(levels) == {"alpha", "beta"}

    def test_the_interest_reaches_the_model_with_its_level(self):
        brain, llm = make_brain(None, answer(selected_product="alpha"))
        brain.decide(request(facts=[fact("interest", "alpha", DISCOVERED)]))
        payload = payload_of(llm)
        assert payload["interest"] == [
            {"product": "alpha", "level": "probable", "basis": "fact:discovered"}
        ]
        assert payload["catalog"]["focus"] == ["alpha"]
        assert payload["catalog"]["products"][0]["interest"] == "probable"

    def test_interest_changes_the_objective(self):
        unknown = context_for()
        probable = context_for(facts=[fact("interest", "alpha", DISCOVERED)])
        confirmed = context_for(facts=[fact("interest", "alpha", CONFIRMED)])
        assert unknown.plan.objective is Objective.OPEN_CONVERSATION
        assert probable.plan.objective is Objective.PRESENT_OFFER
        assert confirmed.plan.objective is Objective.PROPOSE_NEXT_STEP

    def test_interest_in_a_product_needs_no_other_signal_to_propose_a_next_step(self):
        ctx = context_for(
            doc_with(),
            facts=[fact("interest", "alpha", CONFIRMED)],
            action=REPLY,
        )
        assert ctx.plan.discovery.status.value == "not_needed"
