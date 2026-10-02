"""The OpenAI adapter, against a stand-in for the SDK client (no network)."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from sales_core.contracts import LLMMessage, LLMRequest, LLMRole
from sales_core.ports import (
    LLMAuthError,
    LLMError,
    LLMModelError,
    LLMProvider,
    LLMRateLimitError,
    LLMTimeoutError,
)
from sales_integrations.openai import LLMOutputError, OpenAIProvider


class FakeSDK:
    """Mimics `client.chat.completions.create(...)` and records its arguments."""

    def __init__(self, content='{"ok": true}', model="gpt-test", usage=(11, 7)):
        self.calls: list[dict] = []
        self._content, self._model, self._usage = content, model, usage
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(prompt_tokens=self._usage[0], completion_tokens=self._usage[1])
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)], model=self._model, usage=usage
        )


def request(**kw) -> LLMRequest:
    messages = (
        LLMMessage(role=LLMRole.SYSTEM, content="Be brief."),
        LLMMessage(role=LLMRole.USER, content="Hello"),
    )
    return LLMRequest(messages=messages, **kw)


def provider(sdk: FakeSDK) -> OpenAIProvider:
    return OpenAIProvider(model="gpt-test", client=sdk)


def test_satisfies_the_port():
    assert isinstance(provider(FakeSDK()), LLMProvider)


def test_sends_messages_and_maps_the_response():
    sdk = FakeSDK(content="Hi!", model="gpt-test-2025")
    response = provider(sdk).complete(request())
    assert sdk.calls == [
        {
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hello"},
            ],
        }
    ]
    assert response.text == "Hi!" and response.parsed is None
    assert response.model == "gpt-test-2025"
    assert (response.input_tokens, response.output_tokens) == (11, 7)


def test_optional_parameters_are_sent_only_when_given():
    sdk = FakeSDK()
    provider(sdk).complete(request(temperature=0.2, max_output_tokens=300))
    call = sdk.calls[0]
    assert call["temperature"] == 0.2 and call["max_completion_tokens"] == 300
    assert "response_format" not in call


def test_a_schema_requests_strict_structured_output_and_parses_it():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    sdk = FakeSDK(content=json.dumps({"ok": True}))
    response = provider(sdk).complete(request(response_schema=schema))
    assert sdk.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": schema, "strict": True},
    }
    assert response.parsed == {"ok": True}


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"text"', ""])
def test_structured_output_that_is_not_a_json_object_is_an_error(content):
    with pytest.raises(LLMOutputError):
        provider(FakeSDK(content=content)).complete(request(response_schema={"type": "object"}))


def test_empty_content_without_a_schema_is_an_empty_answer():
    assert provider(FakeSDK(content=None)).complete(request()).text == ""


def test_sdk_errors_propagate_to_the_caller():
    class Failing:
        chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(TimeoutError("slow"))
            )
        )

    with pytest.raises(TimeoutError):
        OpenAIProvider(model="m", client=Failing()).complete(request())


def test_a_model_name_is_required():
    with pytest.raises(ValueError, match="model"):
        OpenAIProvider(model=" ", client=FakeSDK())


def test_the_real_sdk_client_is_built_lazily_and_offline():
    assert isinstance(OpenAIProvider(model="gpt-test", api_key="sk-test"), LLMProvider)


# ---- neutral errors, defensive reading of the answer ------------------------------------------

KEY = "sk-LIVE-very-secret-marker-123"
HTTP_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def status_error(cls, status):
    """An SDK status error whose own message quotes the API key, as OpenAI's 401 does."""
    response = httpx.Response(status, request=HTTP_REQUEST)
    return cls(f"Incorrect API key provided: {KEY}", response=response, body=None)


def raising(error) -> OpenAIProvider:
    def create(**kwargs):
        raise error

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return OpenAIProvider(model="gpt-test", client=sdk)


@pytest.mark.parametrize(
    ("sdk_error", "expected"),
    [
        (status_error(openai.AuthenticationError, 401), LLMAuthError),
        (status_error(openai.PermissionDeniedError, 403), LLMAuthError),
        (status_error(openai.RateLimitError, 429), LLMRateLimitError),
        (openai.APITimeoutError(request=HTTP_REQUEST), LLMTimeoutError),
        (status_error(openai.NotFoundError, 404), LLMModelError),
        (status_error(openai.BadRequestError, 400), LLMModelError),
        (status_error(openai.UnprocessableEntityError, 422), LLMModelError),
        (openai.APIConnectionError(request=HTTP_REQUEST), LLMError),
        (status_error(openai.InternalServerError, 500), LLMError),
    ],
)
def test_sdk_errors_become_neutral_llm_errors(sdk_error, expected):
    with pytest.raises(LLMError) as raised:
        raising(sdk_error).complete(request())
    assert type(raised.value) is expected


def test_a_timeout_is_not_reported_as_a_connection_failure():
    with pytest.raises(LLMTimeoutError):
        raising(openai.APITimeoutError(request=HTTP_REQUEST)).complete(request())


@pytest.mark.parametrize(
    "sdk_error",
    [
        status_error(openai.AuthenticationError, 401),
        status_error(openai.RateLimitError, 429),
        status_error(openai.BadRequestError, 400),
        status_error(openai.InternalServerError, 500),
    ],
)
def test_a_translated_error_never_carries_the_key_or_the_sdk_error(sdk_error):
    with pytest.raises(LLMError) as raised:
        raising(sdk_error).complete(request())
    error = raised.value
    assert KEY not in str(error) and KEY not in repr(error)
    assert error.__cause__ is None and error.__suppress_context__


def test_an_unrelated_exception_is_not_translated():
    with pytest.raises(KeyError):
        raising(KeyError("x")).complete(request())


def answering(*, content='{"ok": true}', finish_reason="stop", refusal=None):
    message = SimpleNamespace(content=content, refusal=refusal)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    response = SimpleNamespace(choices=[choice], model="gpt-test", usage=None)
    sdk = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response))
    )
    return OpenAIProvider(model="gpt-test", client=sdk)


@pytest.mark.parametrize("choices", [[], None])
def test_no_choice_is_an_output_error(choices):
    sdk = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(choices=choices, model="m", usage=None)
            )
        )
    )
    with pytest.raises(LLMOutputError, match="no answer"):
        OpenAIProvider(model="m", client=sdk).complete(request())


def test_a_refusal_is_an_output_error_without_its_text():
    provider = answering(content=None, refusal="I cannot help with that, sk-LEAK")
    with pytest.raises(LLMOutputError, match="refused") as raised:
        provider.complete(request())
    assert "sk-LEAK" not in str(raised.value)


def test_a_cut_off_answer_is_an_output_error_even_without_a_schema():
    with pytest.raises(LLMOutputError, match="cut off"):
        answering(content="Hello and wel", finish_reason="length").complete(request())


def test_a_filtered_answer_is_an_output_error():
    with pytest.raises(LLMOutputError, match="content filter"):
        answering(content="", finish_reason="content_filter").complete(request())


def test_invalid_structured_json_is_an_output_error_the_brain_can_retry():
    with pytest.raises(LLMOutputError):
        answering(content="{not json").complete(request(response_schema={"type": "object"}))


def test_output_errors_belong_to_the_neutral_family():
    assert issubclass(LLMOutputError, LLMError)
    assert all(
        issubclass(e, LLMError)
        for e in (LLMAuthError, LLMRateLimitError, LLMTimeoutError, LLMModelError)
    )


def test_temperature_is_not_sent_unless_the_request_names_one():
    sdk = FakeSDK()
    provider(sdk).complete(request(max_output_tokens=50))
    assert "temperature" not in sdk.calls[0]
