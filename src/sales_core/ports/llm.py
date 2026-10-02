"""Port for language-model providers (OpenAI first)."""

from typing import Protocol, runtime_checkable

from sales_core.contracts import LLMRequest, LLMResponse


class LLMError(Exception):
    """A model call failed. Provider-neutral: adapters translate their SDK's errors into these.

    Messages are fixed sentences. They never carry a credential or the provider's own text.
    """


class LLMAuthError(LLMError):
    """The provider refused the credentials (missing, wrong, revoked or not allowed)."""


class LLMRateLimitError(LLMError):
    """The provider is throttling or the account's quota is used up."""


class LLMTimeoutError(LLMError):
    """The provider did not answer in time."""


class LLMModelError(LLMError):
    """The model does not exist for this account, or it refused the request's parameters."""


class LLMOutputError(LLMError):
    """The model answered, but not with a usable answer (empty, refused, cut off, not the
    requested structure). A Brain may ask again."""


@runtime_checkable
class LLMProvider(Protocol):
    """Injected into Brains. The Core itself never calls a model."""

    def complete(self, request: LLMRequest) -> LLMResponse: ...
