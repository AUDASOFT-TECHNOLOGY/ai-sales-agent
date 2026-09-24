"""The settings schema: shape, formats, defaults and the rules each model enforces on itself."""

import json

import pytest
from kit_support import SPEC, edited, full, minimal, problems, with_product

from sales_brain_kit import ConsultativeSalesSettings, parse_settings
from sales_brain_kit.settings.common import duration_seconds


def prepared(document):
    return SPEC.prepare(document, 1)


class TestValidDocuments:
    def test_the_smallest_document_needs_only_a_name_and_languages(self):
        assert problems(minimal()) == []

    def test_a_catalog_may_start_empty(self):
        assert parse_settings(minimal()).catalog.products == ()

    def test_a_product_needs_only_an_id_a_name_and_a_summary(self):
        assert problems(with_product()) == []

    def test_the_full_example_is_valid(self):
        assert problems(full()) == []

    def test_defaults_are_written_out_and_are_the_cautious_ones(self):
        settings = prepared(minimal())
        assert settings["communication"]["message"] == {
            "max_chars": 600, "max_questions": 1, "emoji": "light", "greeting": "first_message_only",
        }  # fmt: skip
        assert settings["discovery"]["budget"] == {
            "max_questions_per_message": 1, "max_consecutive_questions": 2,
            "max_questions_per_conversation": 5, "give_before_ask": True,
        }  # fmt: skip
        assert settings["discovery"]["hypotheses"] == {
            "min_confidence_to_use": 0.5,
            "never_state_as_fact": True,
        }
        assert settings["discovery"]["sufficiency"]["propose_next_step"] == {
            "all_of": ["interest"],
            "any_of": [],
            "accept": "confirmed",
        }
        assert settings["conversation"]["recommendation"]["claims"] == "only_from_catalog"
        assert settings["follow_up"]["limits"] == {"max_touches": 4, "min_gap": "P1D"}
        assert list(settings["follow_up"]["situations"]) == ["no_response"]
        assert settings["catalog"]["products"] == []

    def test_preparing_is_idempotent_and_json_safe(self):
        once = prepared(full())
        assert prepared(once) == once
        assert json.loads(json.dumps(once)) == once

    def test_nothing_is_lost_when_preparing(self):
        once = prepared(full())
        product = once["catalog"]["products"][0]
        assert product["id"] == "leadership-essentials"
        assert product["name"] == {"en": "Leadership Essentials", "fr": "Leadership Essentiel"}
        assert product["pricing"]["amount"] == 450 and product["pricing"]["currency"] == "TND"
        assert product["audience"]["suited_for"] == ["managers"]
        assert product["follow_up"]["situations"]["no_response"]["steps"] == [
            {"after": "P3D", "intent": "add_value"}
        ]
        assert [p["id"] for p in once["catalog"]["products"]] == [
            "leadership-essentials", "sales-management", "digital-marketing",
        ]  # fmt: skip

    def test_any_number_of_products_is_accepted_up_to_the_limit(self):
        document = minimal()
        document["catalog"] = {
            "products": [{"id": f"p{i}", "name": f"P{i}", "summary": "s"} for i in range(200)]
        }
        assert problems(document) == []
        document["catalog"]["products"].append({"id": "p200", "name": "P200", "summary": "s"})
        assert problems(document)

    def test_the_typed_model_is_immutable(self):
        settings = parse_settings(full())
        assert isinstance(settings, ConsultativeSalesSettings)
        with pytest.raises(Exception):  # noqa: B017 - a frozen pydantic model
            settings.business = None


class TestUnknownKeysAreRejectedEverywhere:
    @pytest.mark.parametrize(
        "path",
        [
            "typo", "business.typo", "communication.typo", "communication.tone.typo",
            "communication.message.typo", "objectives.typo", "objectives.goals.0.typo",
            "stages.0.typo", "catalog.typo", "catalog.presentation.typo",
            "catalog.source_interest.typo", "catalog.products.0.typo",
            "catalog.products.0.pricing.typo", "catalog.products.0.audience.typo",
            "catalog.products.0.objections.0.typo", "catalog.products.0.appointment.typo",
            "catalog.products.0.strategy.cta.typo", "catalog.products.0.discovery.typo",
            "discovery.typo", "discovery.fields.0.typo", "discovery.budget.typo",
            "discovery.hypotheses.typo", "conversation.typo", "follow_up.typo",
            "follow_up.limits.typo", "incoming.typo", "handoff.typo", "adaptation.typo",
            "templates.0.typo",
        ],
    )  # fmt: skip
    def test_a_typo_fails_loudly(self, path):
        result = problems(edited(full(), path, 1))
        assert result and any("typo" in p for p in result)


class TestRequiredParts:
    @pytest.mark.parametrize(
        "path",
        ["business", "business.name", "communication", "communication.languages",
         "communication.languages.default", "catalog.products.0.id", "catalog.products.0.name",
         "catalog.products.0.summary"],
    )  # fmt: skip
    def test_missing(self, path):
        assert problems(edited(full(), path, ...))


class TestFormats:
    @pytest.mark.parametrize("bad", ["Course A", "-a", "a b", "", "A", "a" * 65, "é"])
    def test_ids_are_slugs(self, bad):
        assert problems(with_product(id=bad))

    @pytest.mark.parametrize("bad", ["english", "E", "fr_FR", "1", ""])
    def test_languages_are_tags(self, bad):
        assert problems(edited(minimal(), "communication.languages.default", bad))

    @pytest.mark.parametrize("good", ["ar", "ar-TN", "fr-FR", "zh-Hans"])
    def test_language_tags_are_accepted(self, good):
        document = edited(minimal(), "communication.languages.default", good)
        document["communication"]["languages"]["supported"] = [good]
        assert problems(document) == []

    @pytest.mark.parametrize(
        "bad", ["http://example.org", "example.org", "https://", "https://a b"]
    )
    def test_urls_must_be_https(self, bad):
        assert problems(edited(with_product(), "catalog.products.0.url", bad))

    @pytest.mark.parametrize("bad", ["tnd", "TN", "TNDD", "1USD"])
    def test_currency_is_iso_4217_shaped(self, bad):
        pricing = {"model": "fixed", "amount": 10, "currency": bad}
        assert problems(with_product(pricing=pricing))

    @pytest.mark.parametrize("text", ["P2D", "PT2H", "P1W", "P1DT12H", "PT30M", "P2W3D", "P10D"])
    def test_durations_are_iso_8601_weeks_days_hours_minutes(self, text):
        assert duration_seconds(text) > 0

    @pytest.mark.parametrize("text", ["P", "PT", "2D", "P1Y", "PT1S", "p2d", "", "P-1D", "P1.5D"])
    def test_other_durations_are_refused(self, text):
        with pytest.raises(ValueError):
            duration_seconds(text)

    def test_a_zero_length_duration_is_refused(self):
        assert problems(edited(minimal(), "follow_up", {"limits": {"min_gap": "P0D"}}))

    def test_durations_convert_correctly(self):
        assert duration_seconds("PT2H") == 7200
        assert duration_seconds("P1DT12H") == 129600
        assert duration_seconds("P1W") == 7 * 86400

    @pytest.mark.parametrize("value", ["", "   ", "x" * 301, "bad\x00text", "bell\x07"])
    def test_short_text_is_bounded_and_clean(self, value):
        assert problems(with_product(summary=value))

    def test_text_keeps_newlines_and_tabs(self):
        assert problems(with_product(description="line one\n\tline two")) == []

    def test_long_text_has_a_larger_limit(self):
        assert problems(with_product(description="x" * 3000)) == []
        assert problems(with_product(description="x" * 3001))

    def test_a_language_map_must_not_be_empty_and_uses_language_tags(self):
        assert problems(with_product(summary={}))
        assert problems(with_product(summary={"english": "x"}))
        assert problems(with_product(summary={"en": ""}))

    def test_text_may_be_plain_or_per_language(self):
        assert problems(with_product(summary="plain")) == []
        assert problems(with_product(summary={"en": "plain"})) == []


class TestPricing:
    @pytest.mark.parametrize(
        "pricing",
        [
            {"model": "fixed", "amount": 450, "currency": "TND"},
            {"model": "from", "amount": 10.5, "currency": "EUR"},
            {"model": "range", "amount": 100, "max_amount": 200, "currency": "USD"},
            {"model": "on_request"},
            {"model": "free"},
            {},
        ],
    )
    def test_consistent_prices(self, pricing):
        assert problems(with_product(pricing=pricing)) == []

    @pytest.mark.parametrize(
        "pricing",
        [
            {"model": "fixed", "currency": "TND"},
            {"model": "fixed", "amount": 10},
            {"model": "from", "amount": 10},
            {"model": "range", "amount": 100, "currency": "USD"},
            {"model": "range", "amount": 200, "max_amount": 100, "currency": "USD"},
            {"model": "fixed", "amount": 10, "max_amount": 20, "currency": "USD"},
            {"model": "on_request", "amount": 10},
            {"model": "free", "amount": 10},
            {"model": "fixed", "amount": -1, "currency": "USD"},
            {"model": "barter"},
        ],
    )
    def test_inconsistent_prices(self, pricing):
        assert problems(with_product(pricing=pricing))


class TestCallsToAction:
    def test_an_enrolment_link_needs_a_url(self):
        assert problems(with_product(strategy={"cta": {"kind": "enrol_link"}}))
        cta = {"kind": "enrol_link", "url": "https://example.org/enrol"}
        assert problems(with_product(strategy={"cta": cta})) == []

    def test_a_coming_soon_product_cannot_take_enrolments(self):
        cta = {"kind": "enrol_link", "url": "https://example.org/enrol"}
        assert problems(with_product(status="coming_soon", strategy={"cta": cta}))
        assert (
            problems(with_product(status="coming_soon", strategy={"cta": {"kind": "notify_me"}}))
            == []
        )

    def test_there_is_no_calendar_booking_in_the_mvp(self):
        assert problems(with_product(strategy={"cta": {"kind": "book_call"}}))

    @pytest.mark.parametrize(
        "kind", ["request_info", "enrol_link", "request_callback", "notify_me"]
    )
    def test_the_supported_kinds(self, kind):
        cta = {"kind": kind, "url": "https://example.org/x"}
        assert problems(with_product(strategy={"cta": cta})) == []


class TestAppointmentsAreCallbacksForAHuman:
    def test_the_only_handler_is_a_human(self):
        assert (
            parse_settings(with_product(appointment={"supported": True}))
            .catalog.products[0]
            .appointment.handled_by
            == "human"
        )
        assert problems(with_product(appointment={"supported": True, "handled_by": "human"})) == []
        assert problems(with_product(appointment={"handled_by": "brain_proposes"}))
        assert problems(with_product(appointment={"handled_by": "calendar"}))

    def test_notice_is_bounded(self):
        assert problems(with_product(appointment={"min_notice_hours": -1}))
        assert problems(with_product(appointment={"min_notice_hours": 721}))


class TestEvidenceFollowsImportanceNotEveryFact:
    @staticmethod
    def field(**values):
        document = minimal()
        document["discovery"] = {"fields": [{"key": "topic", **values}]}
        return parse_settings(document).discovery.fields[0]

    def test_ordinary_fields_do_not_need_evidence(self):
        assert self.field().evidence == "optional"
        assert self.field(importance="useful").evidence == "optional"
        assert self.field(importance="optional").evidence == "optional"

    def test_important_and_sensitive_fields_do(self):
        assert self.field(importance="critical").evidence == "required"
        assert self.field(sensitive=True).evidence == "required"
        assert self.field(importance="optional", sensitive=True).evidence == "required"

    def test_it_can_be_raised_for_an_ordinary_field(self):
        assert self.field(evidence="required").evidence == "required"

    def test_a_sensitive_field_cannot_waive_it(self):
        document = minimal()
        document["discovery"] = {
            "fields": [{"key": "topic", "sensitive": True, "evidence": "optional"}]
        }
        assert problems(document)

    def test_the_default_is_written_into_the_prepared_settings(self):
        document = minimal()
        document["discovery"] = {"fields": [{"key": "goal", "importance": "critical"}]}
        assert prepared(document)["discovery"]["fields"][0]["evidence"] == "required"


class TestDiscoveryRules:
    def test_reserved_keys_cannot_be_declared(self):
        for key in ("interest", "asked"):
            document = minimal()
            document["discovery"] = {"fields": [{"key": key}]}
            assert any("reserved" in p for p in problems(document))

    def test_a_requirement_needs_something(self):
        assert problems(edited(minimal(), "discovery", {"sufficiency": {"recommend": {}}}))

    def test_a_shortcut_needs_a_condition(self):
        assert problems(
            edited(minimal(), "discovery", {"shortcuts": [{"when": {}, "do": "recommend_now"}]})
        )

    @pytest.mark.parametrize("value", [3, -1])
    def test_a_message_never_carries_more_than_two_questions(self, value):
        assert problems(
            edited(minimal(), "discovery", {"budget": {"max_questions_per_message": value}})
        )
        assert problems(edited(minimal(), "communication.message", {"max_questions": value}))

    def test_a_hypothesis_can_never_be_stated_as_a_fact(self):
        assert problems(
            edited(minimal(), "discovery", {"hypotheses": {"never_state_as_fact": False}})
        )

    def test_claims_come_only_from_the_catalog(self):
        assert problems(
            edited(minimal(), "conversation", {"recommendation": {"claims": "anything"}})
        )

    def test_confidence_is_a_probability(self):
        assert problems(
            edited(minimal(), "discovery", {"hypotheses": {"min_confidence_to_use": 1.5}})
        )

    def test_at_most_five_hints_per_language(self):
        hints = {"en": [f"hint {i}" for i in range(6)]}
        document = minimal()
        document["discovery"] = {"fields": [{"key": "goal", "ask_hints": hints}]}
        assert problems(document)


class TestFollowUpShape:
    def test_a_situation_has_steps_or_uses_the_preference(self):
        assert problems(edited(minimal(), "follow_up", {"situations": {"no_response": {}}}))
        both = {"steps": [{"after": "P2D"}], "use": "prospect_preference"}
        assert problems(edited(minimal(), "follow_up", {"situations": {"callback_promised": both}}))

    def test_a_step_has_exactly_one_timing(self):
        both = {"after": "P2D", "before": "P1D"}
        assert problems(
            edited(minimal(), "follow_up", {"situations": {"no_response": {"steps": [both]}}})
        )
        assert problems(
            edited(minimal(), "follow_up", {"situations": {"no_response": {"steps": [{}]}}})
        )

    def test_situation_names_are_a_closed_set(self):
        step = {"steps": [{"after": "P2D"}]}
        assert problems(edited(minimal(), "follow_up", {"situations": {"whenever": step}}))

    def test_a_step_list_is_bounded(self):
        many = {"steps": [{"after": "P2D"}] * 7}
        assert problems(edited(minimal(), "follow_up", {"situations": {"no_response": many}}))


class TestHandoffTriggers:
    @pytest.mark.parametrize(
        "trigger",
        [{}, {"intent": "a", "after_rebuttals": 2}, {"after_rebuttals": 0}, {"after_rebuttals": 6}],
    )
    def test_exactly_one_condition_in_range(self, trigger):
        assert problems(edited(minimal(), "handoff", {"triggers": [trigger]}))
