"""OpenAI implementation of the LLMProvider port. The only place that knows OpenAI's API."""

import json
from typing import Any

from sales_core.contracts import LLMRequest, LLMResponse
from sales_core.ports.llm import (
    LLMAuthError,
    LLMError,
    LLMModelError,
    LLMOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
)

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 2  # the SDK retries connection errors, 429 and 5xx with backoff


def _translate(error: Exception) -> LLMError | None:
    """The neutral error for an SDK error, or None if `error` is not the SDK's. The message is a
    fixed sentence: the SDK's own text can quote part of the API key."""
    import openai

    # The more specific classes first: a timeout is also a connection error.
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMAuthError("OpenAI refused the credentials")
    if isinstance(error, openai.RateLimitError):
        return LLMRateLimitError("OpenAI rate limit or quota reached")
    if isinstance(error, openai.APITimeoutError):
        return LLMTimeoutError("OpenAI did not answer in time")
    if isinstance(error, openai.APIConnectionError):
        return LLMError("OpenAI could not be reached")
    if isinstance(
        error,
        (openai.NotFoundError, openai.BadRequestError, openai.UnprocessableEntityError),
    ):
        return LLMModelError("OpenAI rejected the model or one of the request parameters")
    if isinstance(error, openai.APIStatusError):
        return LLMError("OpenAI answered with an error")
    if isinstance(error, openai.OpenAIError):
        return LLMError("OpenAI request failed")
    return None


class OpenAIProvider:
    """Implements `sales_core.ports.LLMProvider` on the Chat Completions API.

    The SDK's errors are translated into the port's neutral `LLMError` family (never carrying the
    SDK's text); anything else propagates. The Brain decides what a failed model call means and
    the Engine isolates the failure per action. Only the parameters a request names are sent
    (no `temperature` unless asked), so a model that rejects one answers with `LLMModelError`.
    Pass `client` to inject any object with the SDK's `chat.completions.create` (used by tests).
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        self._model = model
        if client is None:
            from openai import OpenAI  # imported lazily: no SDK object exists until needed

            client = OpenAI(
                api_key=api_key,
                organization=organization,
                project=project,
                base_url=base_url,
                timeout=timeout,
                max_retries=max_retries,
            )
        self._client = client

    def complete(self, request: LLMRequest) -> LLMResponse:
        arguments: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        }
        if request.temperature is not None:
            arguments["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            arguments["max_completion_tokens"] = request.max_output_tokens
        if request.response_schema is not None:
            arguments["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": dict(request.response_schema),
                    "strict": True,
                },
            }

        try:
            response = self._client.chat.completions.create(**arguments)
        except Exception as error:
            neutral = _translate(error)
            if neutral is None:
                raise
            raise neutral from None  # the SDK error is not chained: it may quote the key
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMOutputError("the model returned no answer")
        choice = choices[0]
        message = choice.message
        if getattr(message, "refusal", None):
            raise LLMOutputError("the model refused to answer")
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise LLMOutputError("the answer was cut off at the output limit")
        if finish_reason == "content_filter":
            raise LLMOutputError("the answer was blocked by the provider's content filter")
        text = message.content or ""
        parsed = self._parse(text) if request.response_schema is not None else None
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            parsed=parsed,
            model=getattr(response, "model", None),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise LLMOutputError(f"structured output is not valid JSON: {error}") from error
        if not isinstance(value, dict):
            raise LLMOutputError("structured output must be a JSON object")
        return value
