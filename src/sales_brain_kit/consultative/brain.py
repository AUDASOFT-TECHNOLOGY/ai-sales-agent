"""The consultative sales Brain: the generic commercial intelligence, client-agnostic.

`decide` only orchestrates. Everything it does is delegated to a module with one job:

    build_context      what is known, which products, which language, the strategy (no I/O)
    terminal_decision  a decision the configuration makes without a model (cadence used up)
    build_llm_request  the system rules and structured context the model is given
    parse_output       the model's answer as a structured model, or a clear failure
    validate_output    is the answer acceptable here? nothing is repaired, only accepted or refused
    build_decision     the Core's `BrainDecision`

The model may propose; it never controls the Core. An unusable answer is refused, the model is
told why and given one more try; if that fails too, `BrainOutputError` is raised and no decision
exists. The Engine records the failure against the action and applies nothing. An `LLMOutputError`
(the provider returned nothing usable) counts as one such attempt. Other errors of the provider
itself (network, quota, ...) propagate untouched, for the Engine to isolate.
"""

from collections.abc import Mapping

from sales_brain_kit.consultative.context import CommercialContext, build_context
from sales_brain_kit.consultative.decision import build_decision, terminal_decision
from sales_brain_kit.consultative.errors import BrainOutputError
from sales_brain_kit.consultative.knowledge import KnowledgeSource, retrieve_knowledge
from sales_brain_kit.consultative.output import parse_output
from sales_brain_kit.consultative.prompt import build_llm_request
from sales_brain_kit.consultative.validation import validate_output
from sales_brain_kit.learning.reflector import Reflection
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_brain_kit.spec import parse_settings
from sales_core.contracts import (
    BrainDecision,
    BrainRequest,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMRole,
)
from sales_core.ports import LLMOutputError, LLMProvider

DEFAULT_ATTEMPTS = 2  # the first answer, and one more after being told what was wrong


class ConsultativeSalesBrain(Reflection):
    """Implements `sales_core.ports.Brain` for any client that supplies settings."""

    def __init__(
        self,
        llm: LLMProvider,
        settings: ConsultativeSalesSettings,
        *,
        max_attempts: int = DEFAULT_ATTEMPTS,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._llm = llm
        self._settings = settings
        self._max_attempts = max_attempts
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._knowledge: KnowledgeSource | None = None

    def attach_knowledge(self, source: KnowledgeSource | None) -> None:
        """Lets the Brain consult the client's own documents (see `knowledge.py`). Optional: without
        a source, or if it fails, the Brain answers from its settings alone."""
        self._knowledge = source

    @property
    def settings(self) -> ConsultativeSalesSettings:
        return self._settings

    def decide(self, request: BrainRequest) -> BrainDecision:
        context = build_context(request, self._settings)
        if context.plan.terminal is not None:
            return terminal_decision(context)
        return self._ask(context)

    def _ask(self, context: CommercialContext) -> BrainDecision:
        llm_request = build_llm_request(
            context,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            knowledge=retrieve_knowledge(context, self._knowledge),
        )
        problems: tuple[str, ...] = ()
        for _ in range(self._max_attempts):
            try:
                response = self._llm.complete(llm_request)
            except LLMOutputError:
                # No usable answer came back (empty, refused, cut off, not JSON): the same request
                # is tried again. Every other provider error propagates untouched.
                problems = ("the model did not return a usable answer",)
                continue
            try:
                output = parse_output(response)
                return build_decision(validate_output(output, context), context)
            except BrainOutputError as error:
                problems = error.problems
                llm_request = _with_feedback(llm_request, response, problems)
        raise BrainOutputError(problems)


def _with_feedback(
    request: LLMRequest, response: LLMResponse, problems: tuple[str, ...]
) -> LLMRequest:
    """The same request, plus the refused answer and what was wrong with it."""
    listing = "\n".join(f"- {problem}" for problem in problems)
    return LLMRequest(
        messages=(
            *request.messages,
            LLMMessage(role=LLMRole.ASSISTANT, content=response.text or "(empty answer)"),
            LLMMessage(
                role=LLMRole.USER,
                content=(
                    "Your answer was refused:\n"
                    f"{listing}\n"
                    "Return a corrected JSON object that follows the response schema and the rules."
                ),
            ),
        ),
        temperature=request.temperature,
        max_output_tokens=request.max_output_tokens,
        response_schema=request.response_schema,
    )


def create_brain(llm: LLMProvider, settings: Mapping[str, object]) -> ConsultativeSalesBrain:
    """A `BrainFactory`: what a client's Brain package exposes to be built from its settings.

    `settings` are the client's validated Brain settings; invalid ones raise
    `SettingsInvalidError`, which the application reports as a Brain that cannot be built.
    """
    return ConsultativeSalesBrain(llm, parse_settings(settings))
