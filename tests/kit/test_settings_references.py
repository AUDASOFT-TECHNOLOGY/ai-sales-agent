"""Rules that need the whole document: references between sections, and guardrails."""

import pytest
from kit_support import edited, full, minimal, problems, with_product


def mentions(found, *needles):
    return any(all(n in problem for n in needles) for problem in found)


def with_fields(*fields, **sections):
    document = minimal()
    document["discovery"] = {"fields": list(fields), **sections.pop("discovery", {})}
    document.update(sections)
    return document


class TestIdentifiersAreUnique:
    def test_the_full_example_has_none_twice(self):
        assert problems(full()) == []

    @pytest.mark.parametrize(
        "where, path, value",
        [
            ("catalog.products", "catalog.products.1.id", "leadership-essentials"),
            ("objectives.goals", "objectives.goals.1.id", "enrol"),
            ("stages", "stages.1.id", "new"),
            ("templates", "templates", None),
            ("adaptation.packs", "adaptation.packs", None),
        ],
    )
    def test_duplicates_are_reported_with_where(self, where, path, value):
        document = full()
        if path == "templates":
            document["templates"].append(dict(document["templates"][0]))
        elif path == "adaptation.packs":
            document["adaptation"]["packs"].append(dict(document["adaptation"]["packs"][0]))
        else:
            document = edited(document, path, value)
        assert mentions(problems(document), where, "must be unique")

    def test_sales_arguments_objections_and_rules_are_unique_within_a_product(self):
        document = full()
        product = document["catalog"]["products"][0]
        product["sales_arguments"].append(dict(product["sales_arguments"][0]))
        product["objections"].append(dict(product["objections"][0]))
        product["qualification"]["fit"].append(dict(product["qualification"]["fit"][0]))
        found = problems(document)
        assert mentions(found, "sales_arguments", "'practical'")
        assert mentions(found, "objections", "'price'")
        assert mentions(found, "qualification", "'leads-people'")

    def test_global_objections_are_unique(self):
        document = full()
        document["conversation"]["objections"].append(
            dict(document["conversation"]["objections"][0])
        )
        assert mentions(problems(document), "conversation.objections")

    def test_the_primary_goal_must_exist(self):
        assert mentions(
            problems(edited(full(), "objectives.primary", "nope")), "objectives.primary"
        )
        assert problems(edited(full(), "objectives.primary", ...)) == []

    def test_a_field_key_is_declared_once_across_global_and_product_fields(self):
        document = full()
        document["catalog"]["products"][0]["discovery"]["extra_fields"][0]["key"] = "goal"
        assert mentions(problems(document), "declared twice")


class TestProtectedAttributes:
    @pytest.mark.parametrize("key", ["gender", "religion", "age", "ethnicity", "nationality"])
    def test_collecting_one_must_be_declared_sensitive(self, key):
        assert mentions(problems(with_fields({"key": key})), "protected attribute", "sensitive")
        assert problems(with_fields({"key": key, "sensitive": True})) == []

    def test_a_client_may_protect_more_attributes(self):
        document = with_fields({"key": "income"})
        document["adaptation"] = {"extra_protected_attributes": ["income"]}
        assert mentions(problems(document), "protected attribute")


class TestProductIdentity:
    def test_a_name_or_alias_cannot_belong_to_two_products(self):
        document = with_product()
        document["catalog"]["products"].append(
            {"id": "course-b", "name": "Other", "summary": "s", "aliases": ["course a"]}
        )
        assert mentions(problems(document), "identifies several products", "course-a", "course-b")

    def test_the_comparison_ignores_case_and_covers_ids_and_translations(self):
        document = with_product(name={"en": "Shared Name"})
        document["catalog"]["products"].append(
            {"id": "course-b", "name": {"fr": "SHARED name"}, "summary": "s"}
        )
        assert mentions(problems(document), "identifies several products")

    def test_source_columns_are_not_repeated(self):
        document = edited(full(), "catalog.source_interest.columns", ["Product", "product"])
        assert mentions(problems(document), "source_interest.columns")


class TestLanguages:
    def test_a_text_in_an_unsupported_language_is_reported_with_its_path(self):
        document = edited(full(), "catalog.products.1.summary", {"de": "Zusammenfassung"})
        assert mentions(problems(document), "catalog.products[1].summary", "'de'")

    def test_business_and_handoff_texts_are_checked_too(self):
        document = edited(full(), "business.summary", {"de": "x"})
        document = edited(document, "handoff.message", {"es": "x"})
        found = problems(document)
        assert mentions(found, "business.summary", "'de'") and mentions(
            found, "handoff.message", "'es'"
        )

    def test_templates_hints_and_restrictions_must_use_supported_languages(self):
        document = edited(full(), "templates.0.language", "de")
        document = edited(document, "discovery.fields.0.ask_hints", {"de": ["Wie?"]})
        document = edited(document, "catalog.products.0.restrictions.languages", ["de"])
        found = problems(document)
        assert mentions(found, "templates[0].language")
        assert mentions(found, "ask_hints") and mentions(found, "restrictions.languages")

    def test_supported_languages_pass(self):
        assert problems(edited(full(), "catalog.products.1.summary", {"ar": "ملخص"})) == []


class TestFactsThatAreReferenced:
    def test_sufficiency_may_use_declared_fields_and_interest_only(self):
        document = edited(full(), "discovery.sufficiency.qualify", {"any_of": ["nope"]})
        assert mentions(problems(document), "discovery.sufficiency.qualify", "'nope'")
        assert (
            problems(edited(full(), "discovery.sufficiency.qualify", {"any_of": ["interest"]}))
            == []
        )

    def test_product_extra_fields_count_as_declared(self):
        document = edited(full(), "discovery.sufficiency.qualify", {"any_of": ["team_size"]})
        assert problems(document) == []

    def test_qualification_and_sales_arguments_refer_to_declared_fields(self):
        document = full()
        product = document["catalog"]["products"][0]
        product["qualification"]["fit"][0]["fact"] = "nope"
        product["sales_arguments"][0]["use_when"] = {"missing": ["x"]}
        found = problems(document)
        assert mentions(found, "qualification.leads-people", "'nope'")
        assert mentions(found, "use_when", "'missing'")

    def test_interest_is_not_a_field_you_can_qualify_on(self):
        document = full()
        document["catalog"]["products"][0]["qualification"]["fit"][0]["fact"] = "interest"
        assert mentions(problems(document), "qualification", "'interest'")


class TestIntentsAndHandoff:
    def test_a_shortcut_intent_must_be_declared(self):
        document = edited(full(), "discovery.shortcuts.1.when.intent", "unknown_intent")
        assert mentions(problems(document), "shortcuts[1].when.intent")

    def test_handoff_triggers_must_refer_to_real_things(self):
        document = edited(full(), "handoff.triggers.0.intent", "ghost")
        document = edited(document, "handoff.triggers.2.disqualified", "ghost")
        found = problems(document)
        assert mentions(found, "handoff.triggers[0].intent", "'ghost'")
        assert mentions(found, "handoff.triggers[2].disqualified", "'ghost'")

    def test_a_disqualifier_of_any_product_can_trigger_a_handoff(self):
        assert problems(full()) == []


class TestAdaptationUsesFactsNotStereotypes:
    def dims(self, **dimensions):
        document = full()
        document["adaptation"]["dimensions"] = dimensions
        return document

    @pytest.mark.parametrize(
        "dimension", ["language", "register", "examples", "depth", "practical"]
    )
    @pytest.mark.parametrize("key", ["gender", "religion", "nationality", "age"])
    def test_a_protected_attribute_never_drives_any_dimension(self, dimension, key):
        assert mentions(
            problems(self.dims(**{dimension: {"inputs": [key]}})), "protected attribute"
        )

    def test_a_client_added_protected_attribute_is_enforced_too(self):
        document = self.dims(examples={"inputs": ["income_level"]})
        assert mentions(problems(document), "protected attribute")

    @pytest.mark.parametrize("key", ["country", "city", "region"])
    def test_place_only_adjusts_practical_matters(self, key):
        document = full()
        if key not in {f["key"] for f in document["discovery"]["fields"]}:
            document["discovery"]["fields"].append({"key": key})
        document["adaptation"]["dimensions"] = {"register": {"inputs": [key]}}
        assert mentions(problems(document), "practical matters")
        document["adaptation"]["dimensions"] = {"practical": {"inputs": [key]}}
        assert problems(document) == []

    def test_inputs_must_be_declared_or_built_in(self):
        assert mentions(problems(self.dims(examples={"inputs": ["mystery"]})), "'mystery'")
        assert problems(self.dims(language={"inputs": ["language", "locale"]})) == []

    def test_covers_belong_to_the_practical_dimension_only(self):
        found = problems(self.dims(examples={"inputs": ["sector"], "covers": ["currency"]}))
        assert mentions(found, "covers")

    @pytest.mark.parametrize("key", ["gender", "country", "mystery"])
    def test_packs_select_by_observed_facts_only(self, key):
        document = full()
        document["adaptation"]["packs"][0]["applies_when"] = {key: ["x"]}
        assert problems(document)

    def test_packs_may_select_by_declared_fields(self):
        document = full()
        document["adaptation"]["packs"][0]["applies_when"] = {
            "sector": ["retail"],
            "role": ["owner"],
        }
        assert problems(document) == []


class TestQuestionBudgets:
    def test_discovery_cannot_exceed_the_message_level_limit(self):
        document = edited(full(), "communication.message.max_questions", 0)
        assert mentions(problems(document), "max_questions_per_message", "max_questions")

    def test_consecutive_questions_fit_within_the_conversation_total(self):
        document = edited(
            full(),
            "discovery.budget",
            {"max_consecutive_questions": 3, "max_questions_per_conversation": 2},
        )
        assert mentions(problems(document), "max_consecutive_questions")

    @pytest.mark.parametrize(
        "name, value",
        [
            ("max_questions_per_message", 2),
            ("max_consecutive_questions", 3),
            ("max_questions_per_conversation", 6),
        ],
    )
    def test_a_product_may_only_tighten_the_global_limits(self, name, value):
        document = full()
        document["communication"]["message"]["max_questions"] = 2
        document["catalog"]["products"][0]["discovery"]["budget"] = {name: value}
        found = problems(document)
        assert mentions(found, f"discovery.budget.{name}", "only tighten")

    def test_tightening_is_fine(self):
        document = full()
        document["catalog"]["products"][0]["discovery"]["budget"] = {
            "max_questions_per_message": 0, "max_consecutive_questions": 1,
            "max_questions_per_conversation": 2,
        }  # fmt: skip
        assert problems(document) == []


class TestFollowUpGuardrails:
    def test_the_minimum_gap_has_a_floor(self):
        assert mentions(
            problems(edited(full(), "follow_up.limits.min_gap", "PT30M")), "min_gap", "one hour"
        )
        assert problems(edited(full(), "follow_up.limits.min_gap", "PT1H")) == []

    def test_a_step_cannot_come_sooner_than_the_minimum_gap(self):
        document = edited(full(), "follow_up.limits.min_gap", "P3D")
        found = problems(document)
        assert mentions(found, "follow_up.situations.no_response.steps[0].after", "min_gap")

    def test_steps_cannot_exceed_the_touch_limit(self):
        document = edited(full(), "follow_up.limits.max_touches", 2)
        assert mentions(problems(document), "no_response.steps", "max_touches")

    def test_before_is_for_appointment_reminders_only(self):
        document = edited(full(), "follow_up.situations.after_cta", {"steps": [{"before": "P1D"}]})
        assert mentions(problems(document), "after_cta.steps[0].before")

    def test_the_prospects_preference_is_for_callbacks_only(self):
        document = edited(
            full(), "follow_up.situations.no_response", {"use": "prospect_preference"}
        )
        assert mentions(problems(document), "no_response.use", "callback_promised")

    def test_a_products_own_steps_obey_the_same_limits(self):
        document = full()
        document["catalog"]["products"][0]["follow_up"]["situations"]["no_response"]["steps"] = [
            {"after": "PT2H"}
        ]
        found = problems(document)
        assert mentions(
            found, "catalog.products[0].follow_up.situations.no_response.steps[0].after"
        )

    def test_reminders_before_an_appointment_may_be_close(self):
        assert problems(full()) == []  # PT2H before an appointment is fine


class TestEveryProblemIsReportedAtOnce:
    def test_several_independent_problems_come_back_together(self):
        document = full()
        document["objectives"]["primary"] = "nope"
        document["catalog"]["products"][1]["id"] = "leadership-essentials"
        document["handoff"]["triggers"][0]["intent"] = "ghost"
        document["templates"][0]["language"] = "de"
        document["follow_up"]["limits"]["min_gap"] = "PT10M"
        found = problems(document)
        for needle in ("objectives.primary", "must be unique", "ghost", "templates[0]", "min_gap"):
            assert mentions(found, needle), (needle, found)
        assert len(found) >= 5

    def test_a_structural_error_is_reported_without_crashing_the_reference_checks(self):
        found = problems(edited(full(), "catalog.products.0.id", "Bad Id"))
        assert mentions(found, "catalog.products.0.id")
