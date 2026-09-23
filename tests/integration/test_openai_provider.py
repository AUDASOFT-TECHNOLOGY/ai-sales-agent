"""The OpenAI adapter, against a stand-in for the SDK client (no network)."""

import json
from types import SimpleNamespace

import pytest

from sales_core.contracts import LLMMessage, LLMRequest, LLMRole
from sales_core.ports import LLMProvider
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
