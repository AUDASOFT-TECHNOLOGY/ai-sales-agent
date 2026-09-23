"""OpenAI implementation of the LLMProvider port. The only place that knows OpenAI's API."""

import json
from typing import Any

from sales_core.contracts import LLMRequest, LLMResponse

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 2  # the SDK retries connection errors, 429 and 5xx with backoff


class LLMOutputError(ValueError):
    """The model answered, but not with the structured output that was requested."""


class OpenAIProvider:
    """Implements `sales_core.ports.LLMProvider` on the Chat Completions API.

    SDK exceptions (rate limit, auth, connection...) propagate to the caller: the Brain decides
    what a failed model call means and the Engine isolates the failure per action.
    Pass `client` to inject any object with the SDK's `chat.completions.create` (used by tests).
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        self._model = model
        if client is None:
            from openai import OpenAI  # imported lazily: no SDK object exists until needed

            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
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

        response = self._client.chat.completions.create(**arguments)
        text = response.choices[0].message.content or ""
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
