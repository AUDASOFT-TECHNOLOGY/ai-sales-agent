"""Minimum necessary discovery: ask only what a next step needs, never twice, never a form."""

import pytest
from brain_support import (
    CONFIRMED,
    DISCOVERED,
    HYPOTHESIS,
    REPLY,
    answer,
    context_for,
    decide,
    fact,
    make_brain,
    payload_of,
    request,
    set_at,
    settings_doc,
    turns,
    with_products,
)

from sales_brain_kit.consultative import BrainOutputError
from sales_brain_kit.consultative.discovery import DiscoveryStatus


def plan(doc=None, **options):
    return context_for(doc, **options).plan.discovery


def refused(*answers, **options) -> str:
    with pytest.raises(BrainOutputError) as error:
        decide(*answers, *([answers[-1]] if len(answers) == 1 else []), **options)
    return str(error.value)


class TestKnownInformationIsNotAskedAgain:
    def test_a_confirmed_fact_is_not_asked(self):
        assert (
            plan(facts=[fact("goal", "lead a team", CONFIRMED)]).status
            is DiscoveryStatus.NOT_NEEDED
        )

    def test_a_discovered_fact_is_not_asked(self):
        assert (
            plan(facts=[fact("goal", "lead a team", DISCOVERED)]).status
            is DiscoveryStatus.NOT_NEEDED
        )

    def test_a_value_the_client_holds_in_its_lead_data_is_not_asked(self):
        doc = set_at(settings_doc(), "discovery.sufficiency.recommend", {"all_of": ["role"]})
        assert plan(doc, attributes={"Job": "manager"}).status is DiscoveryStatus.NOT_NEEDED
        assert plan(doc).target_keys == ("role",)

    def test_a_known_product_interest_is_not_asked_either(self):
        assert (
            plan(facts=[fact("interest", "alpha", DISCOVERED)]).status is DiscoveryStatus.NOT_NEEDED
        )

    def test_the_model_cannot_ask_about_something_already_known(self):
        facts = [fact("goal", "lead a team", CONFIRMED)]
        problem = refused(answer(discovery_key="goal", message="What is your goal?"), facts=facts)
        assert "no question is needed or allowed now" in problem


class TestOnlyNecessaryQuestions:
    def test_a_missing_field_no_next_step_needs_is_never_asked(self):
        targets = plan().target_keys
        assert "sector" not in targets and "budget" not in targets and "country" not in targets
        assert "role" not in targets  # useful, but nothing requires it

    def test_the_question_is_about_what_the_next_step_lacks(self):
        discovery = plan()
        assert discovery.status is DiscoveryStatus.ASK
        assert discovery.aim == "recommend"
        assert discovery.target_keys == ("goal", "interest")  # any one of them would do

    def test_the_model_cannot_ask_about_something_no_step_needs(self):
        problem = refused(answer(discovery_key="sector", message="What sector are you in?"))
        assert "'sector' is not needed for recommend" in problem
        assert "ask only about: goal, interest" in problem

    def test_a_needed_question_is_accepted(self):
        decision = decide(answer(discovery_key="goal", message="What would you like to improve?"))
        assert decision.metadata["discovery_key"] == "goal"
        assert decision.metadata["discovery"] == "ask"

    def test_the_question_may_be_about_the_interest_itself(self):
        decision = decide(
            answer(discovery_key="interest", message="Which of our courses caught your eye?")
        )
        assert decision.metadata["discovery_key"] == "interest"

    def test_a_field_the_step_requires_is_asked_whatever_its_importance(self):
        doc = set_at(settings_doc(), "discovery.sufficiency.recommend", {"all_of": ["sector"]})
        assert plan(doc).target_keys == ("sector",)

    def test_alternatives_are_offered_most_important_first(self):
        doc = set_at(
            settings_doc(), "discovery.sufficiency.recommend", {"any_of": ["sector", "goal"]}
        )
        assert plan(doc).target_keys == ("goal", "sector")

    def test_ask_hints_come_in_the_message_language_and_are_examples(self):
        (goal, _interest) = plan().targets
        assert goal.hints == ("What would you like to improve?",)
        french = plan(facts=[fact("language", "fr", CONFIRMED)])
        assert french.targets[0].hints == ("Qu'aimeriez-vous améliorer ?",)

    def test_the_hints_reach_the_model_as_example_phrasings_not_scripts(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        ask = payload_of(llm)["discovery"]["ask_about"][0]
        assert ask["key"] == "goal" and ask["example_phrasings"] == [
            "What would you like to improve?"
        ]
        assert "examples, not scripts" in llm.calls[0].messages[0].content


class TestDiscoveryStopsWhenSufficient:
    def test_it_stops_as_soon_as_the_requirement_is_met(self):
        # Only the goal is known: role, sector, country, budget are all still unknown.
        discovery = plan(facts=[fact("goal", "lead a team", DISCOVERED)])
        assert discovery.status is DiscoveryStatus.NOT_NEEDED
        assert discovery.targets == ()

    def test_a_sale_can_proceed_without_a_full_profile(self):
        facts = [fact("interest", "alpha", CONFIRMED)]
        ctx = context_for(facts=facts)
        assert ctx.plan.discovery.status is DiscoveryStatus.NOT_NEEDED
        assert ctx.plan.objective.value == "propose_next_step"
        decision = decide(answer(selected_product="alpha"), facts=facts)
        assert decision.metadata["objective"] == "propose_next_step"

    def test_no_question_may_be_asked_once_it_has_stopped(self):
        facts = [fact("interest", "alpha", CONFIRMED)]
        problem = refused(answer(discovery_key="goal", message="And your goal?"), facts=facts)
        assert "no question is needed or allowed now" in problem

    def test_a_single_offer_needs_no_discovery_to_be_recommended(self):
        assert plan(with_products("beta")).status is DiscoveryStatus.NOT_NEEDED

    def test_a_shortcut_skips_discovery_once_interest_is_established(self):
        doc = set_at(
            settings_doc(),
            "discovery.shortcuts",
            [{"when": {"interest": "probable"}, "do": "recommend_now"}],
        )
        discovery = plan(doc, facts=[fact("interest", "alpha", DISCOVERED)])
        assert discovery.status is DiscoveryStatus.NOT_NEEDED and "shortcut" in discovery.reason

    def test_a_hypothetical_interest_satisfies_a_step_only_if_the_client_says_so(self):
        facts = [fact("interest", "alpha", HYPOTHESIS, confidence=0.9)]
        assert plan(facts=facts).status is DiscoveryStatus.ASK
        doc = set_at(settings_doc(), "discovery.sufficiency.recommend.accept", "possible")
        assert plan(doc, facts=facts).status is DiscoveryStatus.NOT_NEEDED

    def test_qualification_is_worked_toward_only_after_the_offer_is_clear(self):
        doc = set_at(settings_doc(), "discovery.sufficiency.qualify", {"any_of": ["role"]})
        with_interest = [fact("interest", "alpha", DISCOVERED)]
        assert plan(doc, facts=with_interest).aim == "qualify"
        assert plan(doc, facts=with_interest).target_keys == ("role",)
        assert plan(doc).aim == "recommend"  # first things first
        known_role = [*with_interest, fact("role", "manager", DISCOVERED)]
        assert plan(doc, facts=known_role).status is DiscoveryStatus.NOT_NEEDED
        assert context_for(doc, facts=known_role).plan.qualification_sufficient is True
        assert context_for(doc, facts=with_interest).plan.qualification_sufficient is False

    def test_qualification_sufficiency_is_unknown_when_the_client_requires_nothing(self):
        assert context_for().plan.qualification_sufficient is None

    def test_a_field_only_a_product_declares_is_asked_only_for_that_product(self):
        doc = set_at(
            settings_doc(),
            "catalog.products.1.discovery",
            {
                "extra_fields": [
                    {"key": "team_size", "importance": "useful", "changes": ["qualification"]}
                ]
            },
        )
        doc = set_at(doc, "discovery.sufficiency.qualify", {"all_of": ["team_size"]})
        assert plan(doc, facts=[fact("interest", "beta", DISCOVERED)]).target_keys == ("team_size",)
        assert (
            plan(doc, facts=[fact("interest", "alpha", DISCOVERED)]).status
            is DiscoveryStatus.NOT_NEEDED
        )


class TestNotAQuestionnaire:
    def test_at_most_the_configured_number_of_questions_per_message(self):
        two_questions = answer(
            discovery_key="goal", message="What would you like to improve? And in which sector?"
        )
        assert "questions" in refused(two_questions)

    def test_a_message_that_declares_a_question_must_ask_one(self):
        assert "asks no question" in refused(answer(discovery_key="goal", message="Hello there."))

    def test_the_question_budget_of_the_conversation_is_respected(self):
        history = turns(
            *[pair for i in range(5) for pair in (("out", f"Question {i}?"), ("in", "ok"))]
        )
        discovery = plan(action=REPLY, messages=history)
        assert discovery.status is DiscoveryStatus.BLOCKED
        assert "used up" in discovery.reason
        assert "no question is needed or allowed now" in refused(
            answer(discovery_key="goal", message="One more?"), action=REPLY, messages=history
        )

    def test_questions_in_a_row_are_limited_and_something_must_come_between(self):
        history = turns(
            ("out", "First question?"), ("in", "ok"), ("out", "Second question?"), ("in", "hm")
        )
        discovery = plan(action=REPLY, messages=history)
        assert discovery.status is DiscoveryStatus.BLOCKED and "in a row" in discovery.reason
        gave_something = turns(
            ("out", "Question?"), ("in", "ok"), ("out", "Here is what we offer."), ("in", "hm")
        )
        assert plan(action=REPLY, messages=gave_something).status is DiscoveryStatus.ASK

    def test_a_product_can_only_tighten_the_budget(self):
        doc = set_at(
            settings_doc(),
            "catalog.products.0.discovery",
            {"budget": {"max_questions_per_conversation": 1}},
        )
        doc = set_at(doc, "discovery.sufficiency.qualify", {"any_of": ["role"]})
        facts = [fact("interest", "alpha", DISCOVERED)]
        history = turns(
            ("out", "One question?"), ("in", "ok"), ("out", "A statement."), ("in", "hm")
        )
        assert (
            plan(doc, facts=facts, action=REPLY, messages=history).status is DiscoveryStatus.BLOCKED
        )
        assert (
            plan(
                set_at(doc, "catalog.products.0.discovery", {}),
                facts=facts,
                action=REPLY,
                messages=history,
            ).status
            is DiscoveryStatus.ASK
        )

    def test_no_questions_at_all_when_the_budget_is_zero(self):
        doc = set_at(settings_doc(), "communication.message.max_questions", 0)
        doc = set_at(doc, "discovery.budget.max_questions_per_message", 0)
        assert plan(doc).status is DiscoveryStatus.BLOCKED

    def test_a_prospect_who_asks_for_a_price_is_answered_not_questioned(self):
        signalled = answer(
            discovery_key="goal",
            message="What would you like to improve?",
            analysis={"stop_signals": ["price_request"]},
        )
        assert "signalled price_request" in refused(signalled)

    def test_an_intent_that_skips_discovery_is_not_met_with_a_question(self):
        doc = set_at(
            settings_doc(),
            "discovery.shortcuts",
            [{"when": {"intent": "price_request"}, "do": "recommend_now"}],
        )
        asking = answer(
            discovery_key="goal",
            message="What would you like to improve?",
            analysis={"intent": "price_request"},
        )
        assert "skips discovery" in refused(asking, doc=doc)

    def test_a_call_to_action_question_is_not_a_discovery_question(self):
        facts = [fact("interest", "alpha", CONFIRMED)]
        decision = decide(
            answer(selected_product="alpha", message="Shall I send you the details?"), facts=facts
        )
        assert decision.metadata.get("discovery_key") is None

    def test_the_planner_hands_the_model_at_most_a_short_list_of_targets(self):
        keys = [f"field-{i}" for i in range(8)]
        doc = settings_doc()
        doc["discovery"]["fields"] += [{"key": key} for key in keys]
        doc = set_at(doc, "discovery.sufficiency.recommend", {"all_of": keys})
        assert len(plan(doc).targets) == 5
