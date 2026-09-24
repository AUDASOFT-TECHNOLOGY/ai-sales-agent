"""Slice 6b, settings: markets, prices per place, payment, register rules, handoff availability and
conversion workflows are configuration, validated once and completely, and nothing about them is
specific to a client."""

from copy import deepcopy

import pytest
import yaml
from commerce_support import commerce_doc
from kit_support import SPEC, edited, minimal, problems
from tests_paths import DOCS

from sales_brain_kit import SettingsInvalidError, check_readiness, parse_settings

STEP = "conversion.workflows.0.steps"


def person_free(doc):
    """The document with everything that would hand a conversation to a person taken out."""
    doc = deepcopy(doc)
    doc["handoff"] = {"enabled": False}
    intents = doc.get("conversation", {}).get("intents", {})
    doc.setdefault("conversation", {})["intents"] = {
        k: v for k, v in intents.items() if v != "handoff"
    }
    doc["incoming"] = {"out_of_scope": {"reply": "polite_redirect", "then": "none"}}
    for product in doc.get("catalog", {}).get("products", []):
        for rule in (product.get("qualification") or {}).get("disqualifiers", []):
            rule["effect"] = "not_a_fit"
    return doc


def rejected(doc, fragment: str) -> None:
    found = problems(doc)
    assert any(fragment in p for p in found), found
    with pytest.raises(SettingsInvalidError):
        parse_settings(doc)


class TestTheAdditionsAreBackwardCompatible:
    def test_a_document_without_the_new_sections_is_still_valid(self):
        assert problems(minimal()) == []

    def test_the_new_sections_default_to_nothing(self):
        prepared = SPEC.prepare(minimal(), 1)
        assert prepared["markets"]["markets"] == [] and prepared["payment"]["methods"] == []
        assert prepared["conversion"]["workflows"] == []
        assert prepared["handoff"]["enabled"] is True
        assert prepared["communication"]["languages"]["priority"] == [
            "explicit_preference", "conversation", "profile", "market", "default",
        ]  # fmt: skip

    def test_the_commerce_document_is_valid_and_normalises_to_a_fixed_point(self):
        assert problems(commerce_doc()) == []
        once = SPEC.prepare(commerce_doc(), 1)
        assert SPEC.prepare(once, 1) == once

    def test_the_json_schema_offers_the_new_sections_to_a_form_builder(self):
        properties = SPEC.json_schema()["properties"]
        assert {"markets", "payment", "conversion"} <= set(properties)


class TestPricePerPlace:
    def test_a_variant_needs_a_market_or_a_country(self):
        doc = edited(commerce_doc(), "catalog.products.0.pricing.variants.0.applies_to", {})
        rejected(doc, "needs at least one market or country")

    @pytest.mark.parametrize("amount", [0, -5])
    def test_a_priced_variant_must_cost_something(self, amount):
        doc = edited(commerce_doc(), "catalog.products.0.pricing.variants.0.amount", amount)
        assert problems(doc)

    def test_a_default_price_of_zero_is_refused_too(self):
        doc = edited(minimal(), "catalog", {"products": [{
            "id": "p", "name": "P", "summary": "S",
            "pricing": {"model": "fixed", "amount": 0, "currency": "USD"}}]})  # fmt: skip
        rejected(doc, "must be above zero")

    def test_free_is_how_a_product_costs_nothing(self):
        doc = edited(commerce_doc(), "catalog.products.0.pricing.variants.0", {
            "id": "market-a-price", "applies_to": {"markets": ["market-a"]}, "model": "free"})  # fmt: skip
        assert problems(doc) == []

    def test_a_variant_needs_its_currency(self):
        doc = edited(commerce_doc(), "catalog.products.0.pricing.variants.0.currency", ...)
        rejected(doc, "needs 'amount' and 'currency'")

    def test_two_variants_with_one_id(self):
        doc = edited(commerce_doc(), "catalog.products.0.pricing.variants.1.id", "market-a-price")
        rejected(doc, "used 2 times")

    def test_one_place_priced_twice(self):
        doc = edited(
            commerce_doc(),
            "catalog.products.0.pricing.variants.1.applies_to",
            {"markets": ["market-a"]},
        )
        rejected(doc, "priced twice")

    def test_a_variant_for_a_market_that_does_not_exist(self):
        doc = edited(
            commerce_doc(),
            "catalog.products.0.pricing.variants.0.applies_to",
            {"markets": ["nowhere"]},
        )
        rejected(doc, "'nowhere' is not a market")

    def test_a_range_still_needs_its_maximum(self):
        doc = edited(commerce_doc(), "catalog.products.0.pricing.variants.2.max_amount", 5)
        rejected(doc, "'max_amount' of at least 'amount'")


class TestPaymentSettings:
    def test_an_online_method_needs_a_link(self):
        doc = edited(commerce_doc(), "payment.methods.0.instructions.0.url", ...)
        rejected(doc, "needs an instruction with a 'url'")

    def test_an_offline_method_needs_text_or_fields(self):
        doc = edited(
            commerce_doc(),
            "payment.methods.2.instructions.0",
            {"id": "default", "url": "https://x.example.org/p"},
        )
        rejected(doc, "needs an instruction with 'text' or 'fields'")

    def test_an_instruction_must_say_something(self):
        doc = edited(commerce_doc(), "payment.methods.2.instructions.0", {"id": "default"})
        rejected(doc, "give at least one of 'text', 'fields' or 'url'")

    def test_a_method_needs_an_instruction(self):
        doc = edited(commerce_doc(), "payment.methods.2.instructions", [])
        assert problems(doc)

    def test_a_payment_link_must_be_https(self):
        doc = edited(
            commerce_doc(), "payment.methods.0.instructions.0.url", "http://pay.example.org"
        )
        assert problems(doc)

    def test_a_blank_exact_value_is_refused(self):
        doc = edited(commerce_doc(), "payment.methods.1.instructions.0.fields.1.value", "   ")
        rejected(doc, "must not be blank")

    def test_exact_values_are_kept_exactly_as_written(self):
        odd = "  XX00  Test-Case 0042 é "  # spaces, case, an accent: all part of the value
        doc = edited(commerce_doc(), "payment.methods.1.instructions.0.fields.1.value", odd)
        prepared = SPEC.prepare(doc, 1)
        assert prepared["payment"]["methods"][1]["instructions"][0]["fields"][1]["value"] == odd

    def test_a_scope_naming_an_unknown_product_or_market(self):
        doc = edited(
            commerce_doc(),
            "payment.methods.0.applies_to",
            {"products": ["ghost"], "markets": ["nowhere"]},
        )
        found = problems(doc)
        assert any("'ghost' is not a product" in p for p in found)
        assert any("'nowhere' is not a market" in p for p in found)

    def test_duplicate_method_ids(self):
        rejected(edited(commerce_doc(), "payment.methods.1.id", "online-card"), "used 2 times")

    def test_duplicate_instruction_ids_in_one_method(self):
        rejected(
            edited(commerce_doc(), "payment.methods.1.instructions.1.id", "default"), "used 2 times"
        )

    def test_two_instructions_for_the_same_scope_are_ambiguous(self):
        doc = edited(commerce_doc(), "payment.methods.1.instructions.1.applies_to", {})
        rejected(doc, "same scope")

    def test_a_language_used_in_a_payment_text_must_be_supported(self):
        doc = edited(
            commerce_doc(), "payment.methods.1.instructions.0.text", {"en": "Transfer", "de": "x"}
        )
        rejected(doc, "language 'de' is not in communication.languages")

    def test_no_key_needed_to_describe_payment_looks_like_a_secret(self):
        """The ConfigStore refuses secret-looking keys; payment details are data, not credentials."""
        from sales_app.config.resolution import _secret_paths

        assert _secret_paths(commerce_doc()) == []


class TestMarketSettings:
    def test_a_country_belongs_to_one_market(self):
        doc = edited(commerce_doc(), "markets.markets.1.countries", ["country a"])
        rejected(doc, "a country belongs to one market")

    def test_markets_need_a_declared_country_field(self):
        doc = commerce_doc()
        doc["discovery"]["fields"] = [
            f for f in doc["discovery"]["fields"] if f["key"] != "country"
        ]
        doc["discovery"]["sufficiency"] = {"recommend": {"any_of": ["goal", "interest"]}}
        rejected(doc, "'country' is not a declared discovery field")

    def test_the_country_is_never_guessed(self):
        doc = edited(commerce_doc(), "discovery.fields.3.infer", {"allowed": True})
        rejected(doc, "never guessed")

    def test_a_market_cannot_rest_on_nationality(self):
        doc = edited(commerce_doc(), "markets.country_fact", "nationality")
        rejected(doc, "protected attribute")

    def test_a_market_language_must_be_supported(self):
        doc = edited(
            commerce_doc(), "markets.markets.0.languages", {"allowed": ["de"], "default": "de"}
        )
        rejected(doc, "language 'de' is not in communication.languages")

    def test_the_market_default_must_be_allowed(self):
        doc = edited(
            commerce_doc(), "markets.markets.0.languages", {"allowed": ["fr"], "default": "en"}
        )
        assert problems(doc)

    def test_a_market_catalog_names_real_products(self):
        doc = edited(commerce_doc(), "markets.markets.0.catalog", {"only": ["ghost"]})
        rejected(doc, "'ghost' is not a product")

    def test_a_product_cannot_be_both_offered_and_excluded(self):
        doc = edited(
            commerce_doc(), "markets.markets.0.catalog", {"only": ["alpha"], "exclude": ["alpha"]}
        )
        assert problems(doc)

    def test_a_market_workflow_must_exist(self):
        doc = edited(commerce_doc(), "markets.markets.0.workflow", "ghost")
        rejected(doc, "'ghost' is not a workflow")

    def test_market_follow_up_is_bounded_like_any_other(self):
        doc = edited(commerce_doc(), "markets.markets.0.follow_up", {"situations": {
            "no_response": {"steps": [{"after": "PT2H"}], "then": "park"}}})  # fmt: skip
        rejected(doc, "shorter than follow_up.limits.min_gap")

    def test_an_unused_country_field_is_not_demanded(self):
        assert problems(minimal()) == []

    def test_an_untrusted_source_cannot_be_configured(self):
        doc = edited(commerce_doc(), "markets.trusted_sources", ["brain_inference"])
        assert problems(doc)


class TestRegisterRulesAndLanguagePriority:
    def test_a_rule_needs_a_condition(self):
        doc = edited(commerce_doc(), "communication.register_rules.0.when", {})
        rejected(doc, "needs at least one market or language")

    def test_a_rule_needs_an_effect(self):
        doc = edited(
            commerce_doc(),
            "communication.register_rules.1",
            {"id": "b-any", "when": {"markets": ["market-b"]}},
        )
        rejected(doc, "give at least one of 'formality'")

    def test_a_rule_must_name_real_markets_and_supported_languages(self):
        doc = edited(
            commerce_doc(),
            "communication.register_rules.0.when",
            {"markets": ["ghost"], "languages": ["de"]},
        )
        found = problems(doc)
        assert any("'ghost' is not a market" in p for p in found)
        assert any("language 'de' is not in communication.languages" in p for p in found)

    def test_a_rule_id_is_unique(self):
        rejected(
            edited(commerce_doc(), "communication.register_rules.1.id", "a-in-fr"), "used 2 times"
        )

    def test_a_priority_lists_each_signal_once(self):
        doc = edited(
            minimal(),
            "communication.languages.priority",
            ["explicit_preference", "default", "default"],
        )
        rejected(doc, "lists a signal twice")

    def test_what_a_prospect_asked_for_is_never_left_out_of_the_priority(self):
        doc = edited(minimal(), "communication.languages.priority", ["market", "default"])
        rejected(doc, "'priority' must include 'explicit_preference'")

    def test_a_priority_can_be_reordered(self):
        doc = edited(
            minimal(),
            "communication.languages.priority",
            ["explicit_preference", "market", "profile", "default"],
        )
        assert problems(doc) == []


class TestHandoffCanBeSwitchedOff:
    def off(self):
        return person_free(minimal())

    def test_a_client_that_never_hands_over_is_valid(self):
        assert problems(self.off()) == []

    def test_a_document_that_still_hands_over_is_refused_when_handoff_is_off(self):
        doc = edited(commerce_doc(), "handoff.enabled", False)
        assert any("handoff.enabled is false" in p for p in problems(doc))

    def test_but_it_cannot_also_configure_a_handoff(self):
        doc = edited(self.off(), "handoff.triggers", [{"after_rebuttals": 2}])
        rejected(doc, "handoff.triggers: hands over to a person but handoff.enabled is false")

    def test_nor_an_intent_that_hands_over(self):
        doc = edited(self.off(), "conversation.intents", {"human_requested": "handoff"})
        rejected(doc, "conversation.intents.human_requested")

    def test_nor_a_cadence_that_ends_in_a_handoff(self):
        doc = self.off()
        doc["follow_up"] = {
            "situations": {"no_response": {"steps": [{"after": "P2D"}], "then": "handoff"}}
        }
        rejected(doc, "follow_up.situations.no_response.then")

    def test_nor_a_workflow_step_that_needs_a_person(self):
        doc = person_free(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][1]["human"] = "required"
        rejected(doc, "a person is required but handoff.enabled is false")

    def test_nor_an_appointment_a_person_arranges(self):
        doc = self.off()
        doc["conversion"] = {"appointment": {"supported": True, "mode": "human_scheduling"}}
        rejected(doc, "conversion.appointment.mode")

    def test_a_workflow_without_any_person_is_valid(self):
        assert problems(person_free(commerce_doc())) == []


class TestWorkflowSettings:
    def test_a_step_that_cannot_know_it_is_done_says_how(self):
        rejected(edited(commerce_doc(), f"{STEP}.1.completes_when", ...), "needs 'completes_when'")

    def test_the_last_steps_end_the_workflow(self):
        doc = edited(
            commerce_doc(), f"{STEP}.4", {"id": "done", "kind": "close", "next": "register"}
        )
        rejected(doc, "ends the workflow")

    def test_a_handoff_step_cannot_say_no_person_is_involved(self):
        doc = edited(
            commerce_doc(), f"{STEP}.4", {"id": "done", "kind": "handoff", "human": "none"}
        )
        rejected(doc, "hands the conversation to a person")

    def test_settings_of_the_wrong_kind_of_step(self):
        doc = edited(commerce_doc(), f"{STEP}.2.registration", {"mode": "agent_assisted"})
        rejected(doc, "'registration' settings belong to a 'register' step")

    def test_self_service_registration_needs_a_link(self):
        doc = edited(commerce_doc(), f"{STEP}.1.registration", {"mode": "self_service"})
        rejected(doc, "'self_service' registration needs a 'url'")

    def test_human_assisted_registration_cannot_say_no_person_is_involved(self):
        doc = edited(commerce_doc(), f"{STEP}.1", {
            "id": "register", "kind": "register", "human": "none",
            "registration": {"mode": "human_assisted"},
            "completes_when": {"facts": [{"key": "registration_status"}]}})  # fmt: skip
        rejected(doc, "involves a person")

    def test_unknown_references(self):
        doc = edited(commerce_doc(), f"{STEP}.2", {
            "id": "pay", "kind": "payment", "next": "ghost", "fallback": "nowhere",
            "requires": {"steps": ["nope"], "facts": ["nothing"]},
            "completes_when": {"facts": [{"key": "unknown"}], "stage": "ghost-stage"}})  # fmt: skip
        found = " | ".join(problems(doc))
        for fragment in (
            "'ghost' is not a step", "'nowhere' is neither a step", "'nope' is not a step",
            "'nothing' is not a declared field", "'unknown' is not a declared field",
            "'ghost-stage' is not a stage",
        ):  # fmt: skip
            assert fragment in found, fragment

    def test_steps_that_loop_back(self):
        doc = edited(commerce_doc(), f"{STEP}.2.next", "register")
        rejected(doc, "the steps loop back to 'register'")

    def test_a_step_id_cannot_be_a_fallback_keyword(self):
        doc = edited(commerce_doc(), f"{STEP}.4.id", "handoff")
        rejected(doc, "is reserved")

    def test_duplicate_step_ids(self):
        rejected(edited(commerce_doc(), f"{STEP}.2.id", "register"), "used 2 times")

    def test_a_step_cannot_fall_back_to_itself_or_require_itself(self):
        doc = edited(commerce_doc(), f"{STEP}.2", {
            "id": "pay", "kind": "payment", "fallback": "pay", "requires": {"steps": ["pay"]},
            "completes_when": {"facts": [{"key": "payment_status"}]}})  # fmt: skip
        found = " | ".join(problems(doc))
        assert "cannot fall back to itself" in found and "cannot require itself" in found

    def test_a_payment_step_needs_a_payment_method(self):
        doc = edited(commerce_doc(), "payment.methods", [])
        rejected(doc, "needs at least one active payment method")

    def test_a_payment_step_can_only_name_methods_that_exist_and_are_active(self):
        doc = edited(commerce_doc(), f"{STEP}.2.payment", {"methods": ["ghost"]})
        rejected(doc, "'ghost' is not a payment method")
        doc = edited(commerce_doc(), "payment.methods.0.status", "inactive")
        doc = edited(doc, f"{STEP}.2.payment", {"methods": ["online-card"]})
        rejected(doc, "'online-card' is not active")

    def test_an_appointment_step_needs_an_appointment_somewhere(self):
        doc = edited(commerce_doc(), f"{STEP}.4", {
            "id": "book", "kind": "appointment",
            "completes_when": {"facts": [{"key": "registration_status"}]}})  # fmt: skip
        rejected(doc, "no appointment is configured")

    def test_a_booking_link_mode_needs_its_link(self):
        doc = minimal()
        doc["conversion"] = {"appointment": {"supported": True, "mode": "booking_url"}}
        rejected(doc, "needs a 'booking_url'")
        doc["conversion"]["appointment"]["booking_url"] = "https://book.example.org/x"
        assert problems(doc) == []

    def test_a_link_only_belongs_to_the_booking_mode(self):
        doc = minimal()
        doc["conversion"] = {"appointment": {
            "supported": True, "mode": "callback", "booking_url": "https://book.example.org/x"}}  # fmt: skip
        rejected(doc, "'booking_url' is only for the 'booking_url' mode")

    def test_an_unknown_default_or_product_workflow(self):
        rejected(
            edited(commerce_doc(), "conversion.default_workflow", "ghost"),
            "conversion.default_workflow",
        )
        rejected(
            edited(commerce_doc(), "catalog.products.0.workflow", "ghost"),
            "'ghost' is not a workflow",
        )

    def test_an_unknown_kind_of_step_is_refused(self):
        assert problems(edited(commerce_doc(), f"{STEP}.4.kind", "teleport"))


class TestARequirementMustBeSatisfiable:
    """Moved from a client package: a rule that holds for every client."""

    def silent(self):
        doc = edited(minimal(), "discovery", {
            "fields": [{"key": "goal", "importance": "critical"}],
            "sufficiency": {"qualify": {"all_of": ["goal"]}},
            "budget": {"max_questions_per_message": 0, "max_consecutive_questions": 0}})  # fmt: skip
        return doc

    def test_a_step_cannot_require_what_can_never_be_asked(self):
        rejected(self.silent(), "'goal' can never be learned")

    def test_it_is_fine_when_the_lead_data_provides_the_fact(self):
        doc = edited(self.silent(), "discovery.fields.0.source_columns", ["Goal"])
        assert problems(doc) == []

    def test_a_silent_brain_may_still_rely_on_interest(self):
        doc = edited(
            self.silent(), "discovery.sufficiency", {"recommend": {"any_of": ["interest"]}}
        )
        assert problems(doc) == []


class TestReadiness:
    """Valid is not finished. The check is generic: it knows no client."""

    def report(self, doc):
        return check_readiness(parse_settings(doc))

    def test_nothing_to_offer_is_reported(self):
        assert any("no active product" in m for m in self.report(minimal()).missing)

    def test_an_incomplete_product_lists_what_it_lacks(self):
        doc = edited(minimal(), "catalog", {"products": [{"id": "p", "name": "P", "summary": "S"}]})
        missing = " ".join(self.report(doc).missing)
        for part in (
            "description",
            "pricing",
            "url",
            "audience",
            "value.benefits",
            "sales_arguments",
        ):
            assert f"catalog.products[p].{part}" in missing

    def test_a_complete_product_and_a_handoff_message_are_ready(self):
        doc = commerce_doc()
        doc["handoff"]["message"] = "A person will follow up."
        alpha = doc["catalog"]["products"][0]
        alpha["audience"] = {"suited_for": ["anyone"]}
        alpha["strategy"] = {"cta": {"kind": "request_info"}}
        doc["catalog"]["products"] = [alpha]
        assert self.report(doc).ready, self.report(doc).missing

    def test_a_person_that_is_never_involved_needs_no_handoff_message(self):
        assert not any("handoff.message" in m for m in self.report(person_free(minimal())).missing)

    def test_unfinished_text_and_links_anywhere_are_reported(self):
        doc = commerce_doc()
        doc["payment"]["methods"][0]["instructions"][0]["url"] = "https://todo.invalid/pay"
        doc["payment"]["methods"][2]["instructions"][0]["text"] = "TODO(fill-in) instructions"
        missing = self.report(doc).missing
        assert "payment.methods[0].instructions[0].url: still a placeholder" in missing
        assert "payment.methods[2].instructions[0].text: still a placeholder" in missing


class TestTheTemplates:
    def load(self, name):
        return yaml.safe_load((DOCS / "templates" / name).read_text(encoding="utf-8"))

    def test_the_product_template_is_valid_when_added_to_a_client(self):
        doc = commerce_doc()
        template = self.load("product.template.yaml")["products"][0]
        template["workflow"] = "sale"
        doc["catalog"]["products"].append(
            {**template, "sales_arguments": [], "qualification": None, "discovery": None}
        )
        doc["catalog"]["products"][-1] = {
            k: v for k, v in doc["catalog"]["products"][-1].items() if v is not None
        }
        assert problems(doc) == []

    def test_the_product_template_can_never_be_offered_by_accident(self):
        assert self.load("product.template.yaml")["products"][0]["status"] == "inactive"

    def test_the_product_template_shows_every_product_field(self):
        from sales_brain_kit.settings.catalog import Product

        assert set(Product.model_fields) <= set(self.load("product.template.yaml")["products"][0])

    def test_the_commerce_template_is_valid_once_what_it_refers_to_exists(self):
        template = self.load("commerce.template.yaml")
        doc = minimal()
        doc["communication"]["languages"] = {"default": "xx", "supported": ["xx"], **{
            k: v for k, v in template["communication"]["languages"].items()}}  # fmt: skip
        doc["communication"]["register_rules"] = template["communication"]["register_rules"]
        doc["discovery"] = {"fields": [
            {"key": "country", "infer": {"allowed": False}},
            {"key": "example-field"}, {"key": "registration-status"}, {"key": "payment-status"}]}  # fmt: skip
        for section in ("markets", "payment", "conversion"):
            doc[section] = template[section]
        assert problems(doc) == [], problems(doc)

    def test_the_commerce_template_is_flagged_as_unfinished(self):
        template = self.load("commerce.template.yaml")
        doc = minimal()
        doc["communication"]["languages"] = {"default": "xx", "supported": ["xx"]}
        doc["communication"]["register_rules"] = template["communication"]["register_rules"]
        doc["discovery"] = {"fields": [
            {"key": "country", "infer": {"allowed": False}},
            {"key": "example-field"}, {"key": "registration-status"}, {"key": "payment-status"}]}  # fmt: skip
        for section in ("markets", "payment", "conversion"):
            doc[section] = template[section]
        missing = check_readiness(parse_settings(doc)).missing
        assert any(m.startswith("payment.methods[0].instructions[0].url") for m in missing)
        assert any(m.startswith("markets.markets[0].label") for m in missing)
