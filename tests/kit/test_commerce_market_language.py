"""Slice 6b, markets and language: a prospect's country counts only when it is explicit and trusted,
it influences language, register, catalog and cadence only through the client's own rules, and the
language is chosen by a configured priority, never by stereotype."""

from datetime import timedelta
from typing import ClassVar

import pytest
from brain_support import NOW, context_for, decide, set_at
from commerce_support import (
    CONFIRMED,
    DISCOVERED,
    answer,
    commerce_doc,
    country,
    fact,
    known,
    make_brain,
    payload_of,
    request,
)
from factories import make_brain_request, make_prospect

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError, build_context
from sales_brain_kit.consultative.language import LanguageBasis
from sales_core.contracts import ContactInfo, FactSource, FactStatus

HYPOTHESIS = FactStatus.HYPOTHESIS


def market_of(*facts, doc=None, **options):
    return context_for(doc or commerce_doc(), facts=list(facts), **options).market


class TestTheMarketComesFromAnExplicitTrustedCountry:
    def test_a_confirmed_country_places_the_prospect_in_its_market(self):
        market = market_of(country("Country A"))
        assert market.market.id == "market-a" and market.country == "Country A"

    def test_any_spelling_the_market_lists_matches_ignoring_case_and_spacing(self):
        assert market_of(country("  ca ")).market.id == "market-a"
        assert market_of(country("COUNTRY   b")).market.id == "market-b"

    def test_no_country_means_no_market_and_nothing_is_assumed(self):
        market = market_of()
        assert market.market is None and market.country is None
        assert "not known" in market.reason

    def test_a_country_in_no_configured_market_is_known_but_in_no_market(self):
        market = market_of(country("Country Z"))
        assert market.market is None and market.country == "Country Z"
        assert market.place.country == "country z" and market.place.market_id is None

    def test_a_country_stated_by_the_prospect_counts_even_if_only_discovered(self):
        assert (
            market_of(country("Country B", DISCOVERED, FactSource.PROSPECT)).market.id == "market-b"
        )

    def test_a_country_in_the_clients_own_lead_data_counts(self):
        market = market_of(attributes={"Country": "Country B"})
        assert market.market.id == "market-b"

    def test_a_country_recorded_by_a_person_or_the_platform_counts(self):
        for source in (FactSource.HUMAN, FactSource.SYSTEM):
            assert market_of(country("Country A", DISCOVERED, source)).market.id == "market-a"

    def test_a_country_read_from_the_exchange_without_being_stated_is_not_trusted_by_default(self):
        market = market_of(country("Country A", DISCOVERED, FactSource.CONVERSATION))
        assert market.market is None and market.country is None
        assert "not from a trusted source" in market.reason

    def test_unless_the_client_chooses_to_trust_that_source(self):
        doc = set_at(commerce_doc(), "markets.trusted_sources", ["prospect", "conversation"])
        market = market_of(country("Country A", DISCOVERED, FactSource.CONVERSATION), doc=doc)
        assert market.market.id == "market-a"

    def test_a_discovered_country_with_no_provenance_is_not_trusted(self):
        assert market_of(fact("country", "Country A", DISCOVERED)).market is None

    def test_but_a_confirmed_one_is_trusted_whatever_its_provenance(self):
        assert market_of(fact("country", "Country A", CONFIRMED)).market.id == "market-a"

    def test_a_guess_is_never_a_country_however_confident(self):
        guess = fact("country", "Country A", HYPOTHESIS, confidence=0.99)
        assert market_of(guess).market is None

    def test_lead_data_can_be_switched_off_as_a_trusted_source(self):
        doc = set_at(commerce_doc(), "markets.trusted_sources", ["prospect"])
        assert market_of(attributes={"Country": "Country B"}, doc=doc).market is None


class TestNothingIsInferred:
    """No hidden inference of country or nationality, from anything."""

    def test_not_from_the_prospects_name(self):
        prospect = make_prospect(display_name="Country A Trader")
        ctx = build_context(make_brain_request(prospect=prospect), parse_settings(commerce_doc()))
        assert ctx.market.market is None and ctx.market.country is None

    def test_not_from_the_phone_number(self):
        for phone in ("+10000000000", "+33123456789", "+21612345678", "+213551234567"):
            contact = ContactInfo(phone=phone)
            prospect = make_prospect(contact=contact)
            ctx = build_context(
                make_brain_request(prospect=prospect), parse_settings(commerce_doc())
            )
            assert ctx.market.market is None, phone

    def test_not_from_the_region_of_a_locale(self):
        for locale in ("fr-CA", "en-CA", "ar-B", "fr"):
            assert market_of(locale=locale).market is None, locale

    def test_not_from_a_nationality(self):
        assert market_of(known("nationality", "Country A")).market is None
        assert market_of(attributes={"Nationality": "Country A"}).market is None

    def test_not_from_a_column_nobody_declared_as_the_country(self):
        assert market_of(attributes={"City": "Country A", "Origin": "Country A"}).market is None

    def test_not_from_what_the_model_says(self):
        brain, llm = make_brain(commerce_doc(), answer())
        brain.decide(request())
        market = payload_of(llm)["market"]
        assert market["country_known"] is False and "country" not in market

    def test_a_country_field_that_allows_guessing_cannot_be_configured(self):
        from sales_brain_kit import SettingsInvalidError

        doc = set_at(commerce_doc(), "discovery.fields.3.infer", {"allowed": True})
        with pytest.raises(SettingsInvalidError):
            parse_settings(doc)


def language_of(*facts, doc=None, **options):
    return context_for(doc or commerce_doc(), facts=list(facts), **options).language


def prefers(language: str):
    return known("language", language, CONFIRMED, FactSource.PROSPECT)


def seen_in_conversation(language: str):
    return known("language", language, DISCOVERED, FactSource.CONVERSATION)


def held_by_client(language: str):
    return known("language", language, DISCOVERED, FactSource.CLIENT_DATA)


class TestLanguageSelection:
    def test_the_client_default_when_nothing_is_known(self):
        chosen = language_of()
        assert chosen.language == "en" and chosen.basis is LanguageBasis.CLIENT_DEFAULT

    def test_an_explicit_preference_is_used_and_locked(self):
        chosen = language_of(prefers("ar"))
        assert chosen.language == "ar" and chosen.locked
        assert chosen.basis is LanguageBasis.EXPLICIT_PREFERENCE

    def test_the_language_the_prospect_is_writing_in(self):
        chosen = language_of(seen_in_conversation("ar"))
        assert chosen.language == "ar" and chosen.basis is LanguageBasis.CONVERSATION

    def test_a_language_in_the_prospects_profile_or_locale(self):
        assert language_of(held_by_client("fr")).basis is LanguageBasis.PROSPECT_DATA
        chosen = language_of(locale="fr")
        assert chosen.language == "fr" and chosen.basis is LanguageBasis.PROSPECT_DATA

    def test_a_country_with_a_configured_language_rule(self):
        chosen = language_of(country("Country A"))
        assert chosen.language == "fr" and chosen.basis is LanguageBasis.MARKET
        assert language_of(country("Country B")).language == "ar"

    def test_a_country_with_no_language_rule_changes_nothing(self):
        doc = set_at(commerce_doc(), "markets.markets.0.languages", None)
        assert language_of(country("Country A"), doc=doc).language == "en"

    def test_the_country_alone_never_decides_when_it_is_not_known(self):
        assert (
            language_of(country("Country A", DISCOVERED, FactSource.CONVERSATION)).language == "en"
        )

    def test_the_country_of_the_prospect_never_forces_anything_without_a_rule(self):
        assert language_of(country("Country Z")).language == "en"

    def test_a_nationality_never_changes_the_language(self):
        chosen = language_of(
            known("nationality", "Country B"), attributes={"Nationality": "Country B"}
        )
        assert chosen.language == "en"


class TestConflictingSignalsAndConfigurablePriority:
    """A prospect has one `language` fact at a time (an explicit preference, or what the
    conversation showed); it competes with the profile locale and the market rule."""

    def test_the_default_priority_trusts_what_the_prospect_asked_for_first(self):
        chosen = language_of(prefers("en"), country("Country B"), locale="fr")
        assert chosen.language == "en" and chosen.basis is LanguageBasis.EXPLICIT_PREFERENCE

    def test_then_what_the_conversation_showed(self):
        chosen = language_of(seen_in_conversation("en"), country("Country A"), locale="fr")
        assert chosen.language == "en" and chosen.basis is LanguageBasis.CONVERSATION

    def test_then_the_profile_then_the_market(self):
        chosen = language_of(country("Country A"), locale="en")
        assert chosen.language == "en" and chosen.basis is LanguageBasis.PROSPECT_DATA
        assert language_of(country("Country A")).basis is LanguageBasis.MARKET

    def test_the_client_can_put_the_market_above_the_conversation(self):
        doc = set_at(
            commerce_doc(),
            "communication.languages.priority",
            ["explicit_preference", "market", "conversation", "profile", "default"],
        )
        chosen = language_of(seen_in_conversation("en"), country("Country A"), doc=doc)
        assert chosen.language == "fr" and chosen.basis is LanguageBasis.MARKET

    def test_or_the_market_above_the_profile_when_it_prefers_to(self):
        doc = set_at(
            commerce_doc(),
            "communication.languages.priority",
            ["explicit_preference", "market", "profile", "conversation", "default"],
        )
        assert language_of(country("Country A"), locale="en", doc=doc).language == "fr"
        assert language_of(country("Country A"), locale="en").language == "en"

    def test_the_client_can_ignore_the_market_altogether(self):
        doc = set_at(
            commerce_doc(), "communication.languages.priority", ["explicit_preference", "default"]
        )
        assert language_of(country("Country A"), locale="ar", doc=doc).language == "en"

    def test_an_explicit_preference_always_stays_in_the_priority(self):
        from sales_brain_kit import SettingsInvalidError

        with pytest.raises(SettingsInvalidError):
            parse_settings(set_at(commerce_doc(), "communication.languages.priority", ["market"]))

    def test_the_outcome_is_deterministic(self):
        signals = (seen_in_conversation("en"), country("Country A"))
        outcomes = {language_of(*signals, locale="fr").language for _ in range(5)}
        assert outcomes == {"en"}


class TestAllowedLanguages:
    def test_a_language_the_market_does_not_allow_is_not_picked_from_the_profile(self):
        chosen = language_of(country("Country A"), locale="ar")  # market A allows fr and en only
        assert chosen.language == "fr" and chosen.basis is LanguageBasis.MARKET

    def test_nor_from_the_conversation(self):
        chosen = language_of(country("Country A"), seen_in_conversation("ar"))
        assert chosen.language == "fr"

    def test_an_explicit_request_still_stands(self):
        chosen = language_of(country("Country A"), prefers("ar"))
        assert chosen.language == "ar" and chosen.locked

    def test_a_language_just_asked_for_wins_over_the_market(self):
        policy = language_of(country("Country A"))
        assert policy.expected(requested="ar") == "ar"

    def test_mirroring_only_follows_a_language_the_market_allows(self):
        policy = language_of(country("Country A"))
        assert policy.expected(written="ar") == "fr" and policy.expected(written="en") == "en"

    def test_without_a_market_mirroring_follows_any_supported_language(self):
        assert language_of().expected(written="ar") == "ar"

    def test_an_unsupported_language_is_never_used(self):
        assert language_of(country("Country A")).expected(written="de", requested="de") == "fr"

    def test_the_market_language_reaches_the_model_as_a_decision_not_a_hint(self):
        brain, llm = make_brain(commerce_doc(), answer(language="fr", message="Bonjour"))
        brain.decide(request(facts=[country("Country A")]))
        language = payload_of(llm)["language"]
        assert language["use"] == "fr" and language["basis"] == "market"
        assert sorted(language["allowed_here"]) == ["en", "fr"]

    def test_a_message_in_a_language_the_market_rule_does_not_expect_is_refused(self):
        wrong = answer(language="en", message="Hello there")
        brain, _ = make_brain(commerce_doc(), wrong, wrong)
        with pytest.raises(BrainOutputError, match="must be in 'fr'"):
            brain.decide(request(facts=[country("Country A")]))


def register_of(*facts, doc=None, **options):
    return context_for(doc or commerce_doc(), facts=list(facts), **options).register


class TestRegisterAdaptsToTheKnownContext:
    def test_a_market_and_language_rule(self):
        register = register_of(country("Country A"))  # market A speaks fr by rule
        assert register.rule_id == "a-in-fr"
        assert (register.formality, register.allow_switch, register.style) == (
            "informal", True, "friendly",
        )  # fmt: skip
        assert register.market_style == "warm and direct"

    def test_the_same_market_in_another_language_takes_the_clients_tone(self):
        register = register_of(country("Country A"), prefers("en"))
        assert register.rule_id is None and register.formality == "neutral"
        assert register.market_style == "warm and direct"

    def test_a_rule_for_a_market_in_any_language(self):
        register = register_of(country("Country B"))
        assert register.rule_id == "b-any" and register.formality == "formal"
        assert register.style == "warm and concise"  # the rule only changed the formality

    def test_a_rule_for_a_language_in_any_market(self):
        doc = set_at(
            commerce_doc(),
            "communication.register_rules",
            [{"id": "ar-formal", "when": {"languages": ["ar"]}, "formality": "formal"}],
        )
        assert register_of(prefers("ar"), doc=doc).rule_id == "ar-formal"
        assert register_of(prefers("en"), doc=doc).rule_id is None

    def test_no_market_known_means_no_market_rule(self):
        register = register_of()
        assert register.rule_id is None and register.market_style is None
        assert register.formality == "neutral"

    def test_the_first_matching_rule_wins(self):
        doc = set_at(
            commerce_doc(),
            "communication.register_rules",
            [
                {"id": "first", "when": {"markets": ["market-a"]}, "formality": "formal"},
                {"id": "second", "when": {"markets": ["market-a"]}, "formality": "informal"},
            ],
        )
        assert register_of(country("Country A"), doc=doc).rule_id == "first"

    def test_a_rule_can_decide_whether_the_register_may_follow_the_prospect(self):
        assert register_of(country("Country A")).allow_switch is True
        assert register_of(country("Country B")).allow_switch is False

    def test_the_prompt_carries_the_register_and_the_market_style(self):
        brain, llm = make_brain(commerce_doc(), answer(language="fr", message="Salut"))
        brain.decide(request(facts=[country("Country A")]))
        system = llm.calls[0].messages[0].content
        assert "Tone: friendly. Formality: informal." in system
        assert "You may move to the prospect's own register." in system
        assert "Style preferred for this prospect's market: warm and direct" in system

    def test_without_a_market_the_prompt_uses_the_clients_tone(self):
        brain, llm = make_brain(commerce_doc(), answer())
        brain.decide(request())
        system = llm.calls[0].messages[0].content
        assert "Tone: warm and concise. Formality: neutral." in system
        assert "Style preferred" not in system

    def test_the_rule_that_decided_is_visible_to_operators(self):
        brain, llm = make_brain(commerce_doc(), answer(language="ar", message="hello"))
        brain.decide(request(facts=[country("Country B")]))
        assert payload_of(llm)["language"]["register"] == {
            "formality": "formal",
            "may_switch": False,
            "rule": "b-any",
        }

    def test_the_market_is_named_in_the_decision_for_the_audit_trail(self):
        brain, _ = make_brain(commerce_doc(), answer(language="fr", message="Salut"))
        decision = brain.decide(request(facts=[country("Country A")]))
        assert decision.metadata["market"] == "market-a"


class TestTheMarketShapesTheCatalogAndTheCadence:
    week: ClassVar[dict] = {
        "situations": {"no_response": {"steps": [{"after": "P7D"}], "then": "park"}}
    }

    def test_a_market_can_leave_a_product_out(self):
        doc = set_at(commerce_doc(), "markets.markets.0.catalog", {"exclude": ["beta"]})
        ctx = context_for(doc, facts=[country("Country A")])
        assert ctx.selection.excluded["beta"] == "not offered in market market-a"
        assert "beta" not in [p.id for p in ctx.selection.candidates]

    def test_or_offer_only_some(self):
        doc = set_at(commerce_doc(), "markets.markets.0.catalog", {"only": ["alpha"]})
        ctx = context_for(doc, facts=[country("Country A")])
        assert [p.id for p in ctx.selection.candidates] == ["alpha"]

    def test_an_unknown_market_leaves_the_catalog_alone(self):
        doc = set_at(commerce_doc(), "markets.markets.0.catalog", {"exclude": ["beta"]})
        assert "beta" in [p.id for p in context_for(doc).selection.candidates]

    def test_a_market_can_have_its_own_follow_up_cadence(self):
        doc = set_at(commerce_doc(), "markets.markets.0.follow_up", self.week)
        in_market = decide(
            answer(language="fr", message="Bonjour"), doc=doc, facts=[country("Country A")]
        )
        elsewhere = decide(answer(), doc=doc)
        assert in_market.next_follow_up_at == NOW + timedelta(days=7)
        assert elsewhere.next_follow_up_at == NOW + timedelta(days=2)

    def test_but_a_products_own_cadence_still_wins_over_the_markets(self):
        doc = set_at(commerce_doc(), "markets.markets.0.follow_up", self.week)
        doc = set_at(
            doc,
            "catalog.products.0.follow_up",
            {"situations": {"no_response": {"steps": [{"after": "P9D"}], "then": "park"}}},
        )
        decision = decide(
            answer(language="fr", message="Bonjour", selected_product="alpha"),
            doc=doc,
            facts=[country("Country A"), fact("interest", "alpha", CONFIRMED)],
        )
        assert decision.next_follow_up_at == NOW + timedelta(days=9)

    def test_only_the_situations_a_market_names_are_overridden(self):
        doc = set_at(commerce_doc(), "markets.markets.0.follow_up", self.week)
        decision = decide(
            answer(language="fr", message="Bonjour", follow_up={"situation": "after_cta"}),
            doc=doc,
            facts=[country("Country A")],
        )
        assert decision.next_follow_up_at == NOW + timedelta(days=1)  # the client's after_cta
