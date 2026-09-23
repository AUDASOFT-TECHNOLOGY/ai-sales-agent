"""Provider-neutral contracts for language-model calls. No vendor types."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from sales_core.contracts._validation import freeze_mapping, require_text
from sales_core.contracts.enums import LLMRole


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMMessage:
    role: LLMRole
    content: str

    def __post_init__(self) -> None:
        require_text(self.content, "content")


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMRequest:
    messages: tuple[LLMMessage, ...]
    temperature: float | None = None
    max_output_tokens: int | None = None
    response_schema: Mapping[str, object] | None = None  # JSON Schema for structured output

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        if not self.messages:
            raise ValueError("messages must not be empty")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.response_schema is not None:
            object.__setattr__(self, "response_schema", freeze_mapping(self.response_schema))


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMResponse:
    text: str
    parsed: Mapping[str, object] | None = None  # set when a response_schema was requested
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.parsed is not None:
            object.__setattr__(self, "parsed", freeze_mapping(self.parsed))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
