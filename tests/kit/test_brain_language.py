"""Language and tone come from settings and from what the prospect actually did, never from
nationality and never from hard-coded rules."""

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
)

from sales_brain_kit.consultative import BrainOutputError
from sales_brain_kit.consultative.language import LanguageBasis, localize, match_language
from sales_core.contracts import FactSource


def policy(doc=None, **options):
    return context_for(doc, **options).language


def refused(*answers, **options) -> str:
    with pytest.raises(BrainOutputError) as error:
        decide(*answers, *([answers[-1]] if len(answers) == 1 else []), **options)
    return str(error.value)


class TestLanguageTags:
    def test_a_tag_matches_exactly_or_by_primary_language(self):
        assert match_language("fr", ["en", "fr"]) == "fr"
        assert match_language("FR", ["en", "fr"]) == "fr"
        assert match_language("fr-CA", ["en", "fr"]) == "fr"
        assert match_language("ar", ["en", "ar-TN"]) == "ar-TN"
        assert match_language("ar_TN", ["ar-TN", "ar"]) == "ar-TN"
        assert match_language("de", ["en", "fr"]) is None
        assert match_language(None, ["en"]) is None and match_language("  ", ["en"]) is None

    def test_texts_are_picked_by_language_then_default_then_any(self):
        texts = {"en": "Hello", "fr": "Bonjour"}
        assert localize(texts, "fr", "en") == "Bonjour"
        assert localize(texts, "ar", "en") == "Hello"
        assert localize(texts, "ar", "de") in texts.values()
        assert localize("plain", "fr") == "plain"
        assert localize(None, "fr") is None


class TestWhichLanguageIsUsed:
    def test_the_safe_configured_default_when_nothing_else_is_known(self):
        chosen = policy()
        assert chosen.language == "en" and chosen.basis is LanguageBasis.CLIENT_DEFAULT

    def test_the_default_is_whatever_the_client_configured(self):
        doc = set_at(settings_doc(), "communication.languages.default", "fr")
        assert policy(doc).language == "fr"

    def test_the_locale_the_client_holds_for_the_prospect(self):
        chosen = policy(locale="fr-BE")
        assert chosen.language == "fr" and chosen.basis is LanguageBasis.PROSPECT_DATA

    def test_a_locale_the_client_does_not_support_is_ignored(self):
        assert policy(locale="de").language == "en"

    def test_a_confirmed_preference_is_explicit_and_locked(self):
        chosen = policy(
            locale="fr", facts=[fact("language", "ar", CONFIRMED, source=FactSource.PROSPECT)]
        )
        assert chosen.language == "ar"
        assert chosen.basis is LanguageBasis.EXPLICIT_PREFERENCE and chosen.locked

    def test_a_language_learned_from_the_conversation_outranks_the_locale(self):
        chosen = policy(
            locale="fr", facts=[fact("language", "ar", DISCOVERED, source=FactSource.CONVERSATION)]
        )
        assert chosen.language == "ar" and chosen.basis is LanguageBasis.CONVERSATION
        assert not chosen.locked

    def test_a_hypothesis_about_language_is_not_used(self):
        chosen = policy(facts=[fact("language", "ar", HYPOTHESIS, confidence=0.99)])
        assert chosen.language == "en"

    def test_an_unsupported_preference_falls_back_to_the_next_best(self):
        chosen = policy(locale="fr", facts=[fact("language", "de", CONFIRMED)])
        assert chosen.language == "fr"

    def test_the_language_reaches_the_model(self):
        brain, llm = make_brain(None, answer(language="fr"))
        brain.decide(request(locale="fr"))
        language = payload_of(llm)["language"]
        assert language["use"] == "fr" and language["basis"] == "prospect_data"
        assert language["supported"] == ["en", "fr", "ar"]


class TestNoShortcutFromNationality:
    @pytest.mark.parametrize("key", ["country", "nationality", "city", "region"])
    def test_where_the_prospect_is_from_never_decides_the_language(self, key):
        assert policy(facts=[fact(key, "somewhere", CONFIRMED)]).language == "en"
        assert policy(attributes={"Country": "somewhere"}).language == "en"

    def test_nothing_in_the_code_maps_a_country_to_a_language(self):
        first = policy(facts=[fact("country", "aaa", CONFIRMED)])
        second = policy(facts=[fact("country", "bbb", CONFIRMED)])
        assert first.language == second.language == "en"

    def test_a_locale_region_is_not_read_as_a_nationality(self):
        chosen = policy(locale="en-XX")  # a region the client never mentioned
        assert chosen.language == "en"


class TestTheProspectsOwnLanguage:
    def test_the_reply_follows_the_language_the_prospect_writes_in(self):
        history = turns(("out", "Hello!"), ("in", "Bonjour, je voudrais des infos"))
        mirrored = answer(language="fr", analysis={"prospect_language": "fr"})
        decision = decide(mirrored, action=REPLY, messages=history)
        assert decision.message.language == "fr"
        assert decision.metadata["language"] == "fr"

    def test_replying_in_another_language_is_refused(self):
        history = turns(("out", "Hello!"), ("in", "Bonjour"))
        wrong = answer(language="en", analysis={"prospect_language": "fr"})
        assert "the message must be in 'fr'" in refused(wrong, action=REPLY, messages=history)

    def test_a_language_the_client_does_not_support_is_not_followed(self):
        history = turns(("out", "Hello!"), ("in", "Hallo, ich möchte mehr wissen"))
        stays = answer(language="en", analysis={"prospect_language": "de"})
        assert decide(stays, action=REPLY, messages=history).message.language == "en"

    def test_the_client_can_turn_mirroring_off(self):
        doc = set_at(settings_doc(), "communication.languages.mirror_prospect_language", False)
        history = turns(("out", "Hello!"), ("in", "Bonjour"))
        mirrored = answer(language="fr", analysis={"prospect_language": "fr"})
        assert "the message must be in 'en'" in refused(
            mirrored, doc=doc, action=REPLY, messages=history
        )
        assert (
            decide(
                answer(language="en", analysis={"prospect_language": "fr"}),
                doc=doc,
                action=REPLY,
                messages=history,
            ).message.language
            == "en"
        )

    def test_an_explicit_preference_is_not_overridden_by_the_writing_language(self):
        facts = [fact("language", "ar", CONFIRMED, source=FactSource.PROSPECT)]
        history = turns(("out", "Hello!"), ("in", "Bonjour"))
        following = answer(language="fr", analysis={"prospect_language": "fr"})
        assert "the message must be in 'ar'" in refused(
            following, facts=facts, action=REPLY, messages=history
        )
        decision = decide(
            answer(language="ar", analysis={"prospect_language": "fr"}),
            facts=facts,
            action=REPLY,
            messages=history,
        )
        assert decision.message.language == "ar"

    def test_a_language_the_prospect_just_asked_for_wins_even_over_a_stored_preference(self):
        facts = [fact("language", "ar", CONFIRMED, source=FactSource.PROSPECT)]
        history = turns(("out", "Marhaba"), ("in", "Can we speak French please?"))
        asked = answer(
            language="fr", analysis={"prospect_language": "en", "requested_language": "fr"}
        )
        assert decide(asked, facts=facts, action=REPLY, messages=history).message.language == "fr"

    def test_a_requested_language_the_client_does_not_support_is_not_honoured(self):
        history = turns(("out", "Hello"), ("in", "Deutsch bitte"))
        asked = answer(
            language="en", analysis={"prospect_language": "de", "requested_language": "de"}
        )
        assert decide(asked, action=REPLY, messages=history).message.language == "en"

    def test_a_message_must_declare_its_language(self):
        assert "must declare the language" in refused(answer(language=None))

    def test_the_first_message_uses_the_policy_language_as_there_is_nothing_to_mirror(self):
        assert decide(answer(language="fr"), locale="fr").message.language == "fr"
        assert "the message must be in 'fr'" in refused(answer(language="en"), locale="fr")


class TestToneComesFromSettings:
    def test_the_voice_is_built_from_the_settings_not_from_code(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        system = llm.calls[0].messages[0].content
        assert "Acme Academy" in system and "speaking as Sam" in system
        assert "warm and concise" in system and "Answer the question first" in system
        assert "at most 400 characters" in system and "emoji: none" in system

    def test_a_different_client_gets_a_different_voice(self):
        doc = set_at(
            settings_doc(),
            "communication.tone",
            {
                "style": "brisk and playful",
                "formality": {"default": "informal", "allow_switch": True},
            },
        )
        brain, llm = make_brain(doc, answer())
        brain.decide(request())
        system = llm.calls[0].messages[0].content
        assert "brisk and playful" in system and "informal" in system
        assert "move to the prospect's own register" in system

    def test_no_language_or_dialect_is_named_in_the_instructions(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        system = llm.calls[0].messages[0].content.lower()
        for name in ("arabic", "french", "english", "tunisian", "dialect", "derja"):
            assert name not in system
