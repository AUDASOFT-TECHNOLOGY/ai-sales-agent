"""The model is injected and untrusted: its answer becomes a decision only after it is parsed and
validated, and an unusable answer never becomes an unsafe one."""

import json

import pytest
from brain_support import (
    CONFIRMED,
    REPLY,
    answer,
    closing,
    fact,
    make_brain,
    parsed_response,
    payload_of,
    request,
    set_at,
    settings_doc,
    turns,
)
from factories import NOW

from sales_app.wiring.brains import load_brain
from sales_brain_kit import (
    BrainOutputError,
    ConsultativeSalesBrain,
    SettingsInvalidError,
    create_brain,
)
from sales_brain_kit.consultative.context import build_context
from sales_brain_kit.consultative.output import parse_output, response_schema
from sales_core.contracts import DecisionType, LLMResponse, LLMRole
from sales_core.engine.applier import validate_decision
from sales_core.ports import (
    Brain,
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMRateLimitError,
)
from sales_integrations.fakes import FakeLLMProvider

ONGOING = {
    "action": REPLY,
    "messages": turns(("out", "Hello, this is Acme Academy."), ("in", "Tell me more")),
}


def rejected(*answers, max_calls=None, **options) -> BrainOutputError:
    brain, llm = make_brain(None, *answers)
    with pytest.raises(BrainOutputError) as error:
        brain.decide(request(**options))
    if max_calls is not None:
        assert len(llm.calls) == max_calls
    return error.value


class TestAValidStructuredResponse:
    def test_it_becomes_a_brain_decision(self):
        decision = make_brain(
            None, answer(stage="qualifying", rationale="Opened the conversation.")
        )[0].decide(request())
        assert decision.decision_type is DecisionType.SEND_MESSAGE
        assert decision.message.text == "Hello! Glad to help." and decision.message.language == "en"
        assert decision.new_stage == "qualifying"
        assert decision.rationale == "Opened the conversation."
        assert decision.metadata["brain"] == "consultative_sales"

    def test_a_provider_that_already_parsed_the_answer_is_used_as_is(self):
        llm = FakeLLMProvider([parsed_response(message="From the parsed field")])
        brain = ConsultativeSalesBrain(llm, create_brain(llm, settings_doc()).settings)
        assert brain.decide(request()).message.text == "From the parsed field"

    def test_a_json_answer_wrapped_in_a_code_fence_is_accepted(self):
        fenced = "```json\n" + json.dumps(answer()) + "\n```"
        brain, _ = make_brain(None, fenced)
        assert brain.decide(request()).decision_type is DecisionType.SEND_MESSAGE

    def test_missing_optional_parts_take_safe_defaults(self):
        minimal = {"decision": "send_message", "message": "Hi there", "language": "en"}
        brain, _ = make_brain(None, minimal)
        assert brain.decide(request()).message.text == "Hi there"

    def test_the_rationale_is_kept_short_and_is_not_the_message(self):
        long_reason = "word " * 200
        decision = make_brain(None, answer(rationale=long_reason))[0].decide(request())
        assert len(decision.rationale) <= 300
        assert decision.rationale not in decision.message.text

    def test_the_brain_holds_no_hidden_reasoning_in_the_decision(self):
        decision = make_brain(None, answer(rationale="short"))[0].decide(request())
        assert set(decision.metadata) <= {
            "brain", "situation", "objective", "origin", "discovery", "language",
            "follow_up_situation", "qualification_sufficient",
        }  # fmt: skip
        assert "chain" not in json.dumps(dict(decision.metadata)).lower()

    def test_the_decision_passes_the_cores_own_check(self):
        decision = make_brain(None, answer())[0].decide(request())
        validate_decision(decision, NOW)  # a follow-up must lie in the future


class TestMalformedResponses:
    def test_text_that_is_not_json_is_refused_and_asked_again(self):
        brain, llm = make_brain(None, "I would say hello", answer())
        decision = brain.decide(request())
        assert decision.message.text == "Hello! Glad to help."
        assert len(llm.calls) == 2

    def test_the_second_try_is_told_what_was_wrong(self):
        brain, llm = make_brain(None, "not json", answer())
        brain.decide(request())
        retry = llm.calls[1].messages
        assert [m.role for m in retry[-2:]] == [LLMRole.ASSISTANT, LLMRole.USER]
        assert retry[-2].content == "not json"
        assert "not valid JSON" in retry[-1].content
        assert retry[:2] == llm.calls[0].messages  # the original request is kept intact

    def test_two_bad_answers_end_in_an_error_and_no_decision(self):
        error = rejected("nope", "still nope", max_calls=2)
        assert "not valid JSON" in str(error)

    def test_it_can_be_told_to_try_only_once(self):
        brain, llm = make_brain(None, "nope", answer(), max_attempts=1)
        with pytest.raises(BrainOutputError):
            brain.decide(request())
        assert len(llm.calls) == 1

    def test_a_json_value_that_is_not_an_object_is_refused(self):
        assert "single JSON object" in str(rejected("[1, 2]", "[3]"))

    def test_an_answer_of_the_wrong_shape_is_refused_with_the_field_named(self):
        bad = answer()
        bad["analysis"] = "not an object"
        assert "analysis" in str(rejected(bad, bad))

    def test_unknown_keys_are_refused_rather_than_ignored(self):
        with_extra = {**answer(), "run_this": "rm -rf"}
        assert "run_this" in str(rejected(with_extra, with_extra))

    def test_a_missing_decision_is_refused(self):
        assert "decision" in str(rejected({"message": "hi"}, {"message": "hi"}))

    def test_an_empty_answer_is_refused_and_the_retry_says_so(self):
        brain, llm = make_brain(None, "", answer())
        brain.decide(request())
        assert "(empty answer)" in llm.calls[1].messages[-2].content

    def test_a_provider_that_already_parsed_something_wrong_is_refused(self):
        response = LLMResponse(text="", parsed={"decision": 42})
        with pytest.raises(BrainOutputError):
            parse_output(response)

    def test_a_failure_of_the_provider_itself_is_not_swallowed(self):
        llm = FakeLLMProvider([RuntimeError("provider is down")])
        brain = ConsultativeSalesBrain(llm, create_brain(llm, settings_doc()).settings)
        with pytest.raises(RuntimeError, match="provider is down"):
            brain.decide(request())
        assert len(llm.calls) == 1  # no blind retry of an infrastructure failure

    def test_attempts_below_one_make_no_sense(self):
        llm = FakeLLMProvider()
        with pytest.raises(ValueError):
            ConsultativeSalesBrain(llm, create_brain(llm, settings_doc()).settings, max_attempts=0)


class TestUnsupportedDecisions:
    def test_an_unknown_decision_is_rejected(self):
        error = rejected(answer(decision="refund_customer"), answer(decision="refund_customer"))
        assert "'refund_customer' is not supported" in str(error)

    def test_a_decision_that_is_not_open_in_this_situation_is_rejected(self):
        error = rejected(closing(), closing())  # a first contact cannot be closed
        assert "not open in this situation" in str(error)

    def test_the_model_is_only_offered_the_decisions_that_are_open(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        schema = llm.calls[0].response_schema
        assert schema["properties"]["decision"]["enum"] == [
            "handoff_to_human", "no_action", "send_message",
        ]  # fmt: skip
        assert payload_of(llm)["plan"]["decisions_open"] == schema["properties"]["decision"]["enum"]

    def test_a_valid_decision_the_model_may_not_use_is_rejected_even_if_the_provider_ignored_the_schema(
        self,
    ):
        error = rejected(
            answer(decision="schedule_follow_up", message=None, language=None,
                   follow_up={"situation": "no_response"}),
            answer(decision="schedule_follow_up", message=None, language=None,
                   follow_up={"situation": "no_response"}),
        )  # fmt: skip
        assert "schedule_follow_up is not open" in str(error)


class TestEmptyAndOversizedMessages:
    @pytest.mark.parametrize("text", ["", "   \n\t ", None])
    def test_send_message_needs_a_real_message(self, text):
        bad = answer(message=text)
        assert "needs a non-empty message" in str(rejected(bad, bad))

    def test_a_message_over_the_configured_length_is_refused(self):
        doc = set_at(settings_doc(), "communication.message.max_chars", 100)
        bad = answer(message="x" * 101)
        brain, _ = make_brain(doc, bad, bad)
        with pytest.raises(BrainOutputError) as error:
            brain.decide(request())
        assert "101 characters exceed the limit of 100" in str(error.value)

    def test_more_questions_than_the_configuration_allows_are_refused(self):
        bad = answer(message="Are you there? Can you hear me?")
        assert "more than 1 question(s)" in str(rejected(bad, bad))

    def test_only_send_message_carries_a_message(self):
        bad = answer(decision="no_action", message="I will do nothing loudly")
        assert "only send_message carries a message" in str(rejected(bad, bad, **ONGOING))

    def test_a_message_with_the_wrong_reasoning_field_is_not_an_escape_hatch(self):
        sneaky = answer(message="Hello", rationale="x" * 3000)
        assert "rationale" in str(rejected(sneaky, sneaky))


class TestTheRequestSentToTheModel:
    def test_two_messages_a_system_one_from_settings_and_a_structured_context(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        system, user = llm.calls[0].messages
        assert system.role is LLMRole.SYSTEM and user.role is LLMRole.USER
        assert "Acme Academy" in system.content
        assert user.content.startswith("Decide the next commercial step")
        assert isinstance(payload_of(llm), dict)

    def test_the_model_is_asked_for_a_structured_answer(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        schema = llm.calls[0].response_schema
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])  # what strict providers need

    def test_every_object_of_the_schema_is_closed_and_fully_required(self):
        ctx = build_context(request(), create_brain(FakeLLMProvider(), settings_doc()).settings)

        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert node["additionalProperties"] is False
                    assert set(node["required"]) == set(node["properties"])
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for item in node:
                    check(item)

        check(response_schema(ctx))

    def test_the_schema_sticks_to_the_keywords_strict_providers_accept(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        unsupported = {"maxLength", "minLength", "default", "format", "pattern", "$ref", "oneOf"}

        def keywords(node):
            if isinstance(node, dict):
                yield from node
                for value in node.values():
                    yield from keywords(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keywords(item)

        assert not unsupported & set(keywords(llm.calls[0].response_schema))

    def test_the_schema_offers_only_the_clients_own_vocabulary(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        properties = llm.calls[0].response_schema["properties"]
        intents = properties["analysis"]["properties"]["intent"]["enum"]
        assert set(intents) == {
            "price_request",
            "human_requested",
            "stop_contact",
            "spam",
            "off_topic",
            None,
        }
        assert properties["stage"]["enum"] == ["new", "qualifying", "won", None]
        assert properties["discovery_key"]["enum"] == ["goal", "interest", None]
        assert set(properties["follow_up"]["properties"]["situation"]["enum"]) >= {
            "no_response",
            None,
        }

    def test_with_nothing_to_choose_from_the_schema_only_allows_null(self):
        doc = settings_doc()
        doc["stages"] = []
        brain, llm = make_brain(doc, answer())
        brain.decide(request())
        assert llm.calls[0].response_schema["properties"]["stage"] == {"type": "null"}

    def test_sampling_options_are_passed_through(self):
        brain, llm = make_brain(None, answer(), temperature=0.2, max_output_tokens=900)
        brain.decide(request())
        assert llm.calls[0].temperature == 0.2 and llm.calls[0].max_output_tokens == 900

    def test_the_brain_uses_only_the_injected_provider(self):
        llm = FakeLLMProvider([json.dumps(answer())])
        brain = ConsultativeSalesBrain(llm, create_brain(llm, settings_doc()).settings)
        brain.decide(request())
        assert len(llm.calls) == 1

    def test_the_model_sees_only_what_a_decision_needs_not_the_whole_catalog_when_one_is_in_focus(
        self,
    ):
        brain, llm = make_brain(None, answer(selected_product="alpha"))
        brain.decide(request(facts=[fact("interest", "alpha", CONFIRMED)]))
        catalog = payload_of(llm)["catalog"]
        assert [p["id"] for p in catalog["products"]] == ["alpha"]
        assert {p["id"] for p in catalog["other_products"]} == {"beta", "gamma"}


class TestTheFactory:
    def test_it_builds_a_brain_from_the_clients_settings(self):
        brain = create_brain(FakeLLMProvider(), settings_doc())
        assert isinstance(brain, ConsultativeSalesBrain) and isinstance(brain, Brain)

    def test_invalid_settings_do_not_build_a_brain(self):
        with pytest.raises(SettingsInvalidError):
            create_brain(FakeLLMProvider(), {"business": {"name": "x"}})

    def test_the_application_can_load_it_by_its_dotted_path(self):
        brain = load_brain("sales_brain_kit:create_brain", FakeLLMProvider(), settings_doc())
        assert isinstance(brain, ConsultativeSalesBrain)

    def test_settings_normalised_by_the_spec_build_the_same_brain(self):
        from sales_brain_kit import ConsultativeSalesSpec

        prepared = ConsultativeSalesSpec().prepare(settings_doc(), 1)
        brain = create_brain(FakeLLMProvider([json.dumps(answer())]), prepared)
        assert brain.decide(request()).decision_type is DecisionType.SEND_MESSAGE


class TestAnUnusableProviderAnswer:
    """`LLMOutputError` (empty, refused, cut off, not JSON) is retried; other errors are not."""

    def test_it_is_retried_and_the_next_answer_is_used(self):
        brain, llm = make_brain(None, LLMOutputError("cut off"), answer())
        assert brain.decide(request()).message.text == "Hello! Glad to help."
        assert len(llm.calls) == 2
        assert llm.calls[0] == llm.calls[1]  # nothing to feed back: the same request is repeated

    def test_it_stops_after_max_attempts(self):
        brain, llm = make_brain(
            None, LLMOutputError("a"), LLMOutputError("b"), LLMOutputError("c"), max_attempts=3
        )
        with pytest.raises(BrainOutputError):
            brain.decide(request())
        assert len(llm.calls) == 3

    def test_the_default_is_the_first_answer_and_one_more(self):
        brain, llm = make_brain(None, LLMOutputError("a"), LLMOutputError("b"), answer())
        with pytest.raises(BrainOutputError):
            brain.decide(request())
        assert len(llm.calls) == 2

    def test_it_mixes_with_answers_the_brain_refuses(self):
        brain, llm = make_brain(None, LLMOutputError("a"), {"decision": "nonsense"}, max_attempts=2)
        with pytest.raises(BrainOutputError):
            brain.decide(request())
        assert len(llm.calls) == 2

    @pytest.mark.parametrize("error", [LLMAuthError("x"), LLMRateLimitError("x"), LLMError("x")])
    def test_other_provider_errors_propagate_without_a_retry(self, error):
        brain, llm = make_brain(None, error, answer())
        with pytest.raises(type(error)):
            brain.decide(request())
        assert len(llm.calls) == 1
