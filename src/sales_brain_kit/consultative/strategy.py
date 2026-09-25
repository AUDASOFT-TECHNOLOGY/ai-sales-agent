"""The conversation strategy: what this interaction is for and what the model may do in it.

The strategy is decided here, from configuration and known facts, *before* the model is asked. The
model is then given a plan, not a blank page: the situation, the objective, whether a question is
allowed and about what, whether a product may or must be presented, and which decisions are open.
It reads the prospect's message and writes the wording; it does not invent the strategy.

Some situations need no model at all and end here as a terminal outcome: a follow-up that is due
after the configured cadence has been used up simply parks, closes or hands over, as configured.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sales_brain_kit.consultative.discovery import (
    DiscoveryPlan,
    StepStatus,
    applicable_fields,
    evaluate_steps,
    not_needed,
    plan_discovery,
)
from sales_brain_kit.consultative.facts import KnownFacts
from sales_brain_kit.consultative.follow_up import FollowUpPolicy
from sales_brain_kit.consultative.history import HistoryView
from sales_brain_kit.consultative.interest import InterestLevel
from sales_brain_kit.consultative.market import MarketContext
from sales_brain_kit.consultative.payment import RECEIPT_MEDIA
from sales_brain_kit.consultative.pricing import resolve_price
from sales_brain_kit.consultative.selection import ProductSelection, SelectionBasis
from sales_brain_kit.consultative.workflow import (
    WorkflowState,
    evaluate_workflow,
    select_workflow,
)
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_brain_kit.settings.catalog import Product
from sales_core.contracts import (
    BrainRequest,
    ContactOrigin,
    DecisionType,
    Direction,
    SalesActionType,
    payment_progress,
)


class CommercialSituation(StrEnum):
    FIRST_CONTACT = "first_contact"  # we write first; nothing has been said yet
    INBOUND_OPENING = "inbound_opening"  # the prospect wrote first; we have not spoken yet
    CONTINUING = "continuing"  # the prospect replied in a conversation already under way
    FOLLOW_UP = "follow_up"  # coming back after silence
    REMINDER = "reminder"  # a reminder we scheduled is due
    CALLBACK = "callback"  # a callback we promised is due


class Objective(StrEnum):
    OPEN_CONVERSATION = "open_conversation"  # start well: be relevant, learn what matters
    UNDERSTAND_NEED = "understand_need"  # what is still missing decides what to offer
    PRESENT_OFFER = "present_offer"  # put the right product(s) in front of the prospect
    PROPOSE_NEXT_STEP = "propose_next_step"  # interest is established: invite the next step
    RE_ENGAGE = "re_engage"  # the prospect went quiet: bring something worth answering
    HAND_OVER = "hand_over"  # a human carries this on (a promised callback is due)
    ADVANCE_WORKFLOW = "advance_workflow"  # carry out the current step of the conversion workflow


_FIRST_TOUCH = (CommercialSituation.FIRST_CONTACT, CommercialSituation.INBOUND_OPENING)
_ALL_DECISIONS = frozenset(DecisionType)
_THEN_DECISION = {
    "park": DecisionType.NO_ACTION,
    "close": DecisionType.CLOSE,
    "handoff": DecisionType.HANDOFF_TO_HUMAN,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalOutcome:
    """A decision the configuration makes by itself, without asking the model."""

    decision_type: DecisionType
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationPlan:
    situation: CommercialSituation
    origin: ContactOrigin
    objective: Objective
    allowed_decisions: frozenset[DecisionType]
    discovery: DiscoveryPlan
    steps: Mapping[str, StepStatus]
    may_present: bool  # a product may be presented in this message
    must_present: bool  # a message must present one
    no_product_mode: str | None  # how to proceed while no product is in focus
    greeting: bool  # open with a greeting
    follow_up_intent: str | None = None  # what a follow-up now is meant to do, if configured
    terminal: TerminalOutcome | None = None
    workflow: WorkflowState | None = None  # where the conversion workflow stands, if there is one

    @property
    def qualification_sufficient(self) -> bool | None:
        """Whether what the client requires to qualify is known; None if it requires nothing."""
        step = self.steps.get("qualify")
        return step.satisfied if step is not None else None


def classify(request: BrainRequest, history: HistoryView) -> CommercialSituation:
    match request.action.action_type:
        case SalesActionType.REPLY:
            if history.has_prior_outbound:
                return CommercialSituation.CONTINUING
            return CommercialSituation.INBOUND_OPENING
        case SalesActionType.FOLLOW_UP:
            return CommercialSituation.FOLLOW_UP
        case SalesActionType.REMINDER:
            return CommercialSituation.REMINDER
        case SalesActionType.CALLBACK:
            return CommercialSituation.CALLBACK
        case _:
            return CommercialSituation.FIRST_CONTACT


def plan_conversation(
    settings: ConsultativeSalesSettings,
    *,
    request: BrainRequest,
    history: HistoryView,
    facts: KnownFacts,
    selection: ProductSelection,
    language: str,
    follow_up: FollowUpPolicy,
    market: MarketContext,
) -> ConversationPlan:
    now = request.now
    situation = classify(request, history)
    origin = _effective_origin(request.conversation.origin, history)
    primary = selection.focus[0] if selection.focus else None
    by_person = situation is CommercialSituation.CALLBACK and _callback_by_person(settings, primary)

    steps = evaluate_steps(
        settings, applicable_fields(settings, selection.focus), facts, selection, now
    )
    workflow = None
    chosen = select_workflow(settings, primary, market.market)
    if chosen is not None:
        workflow = evaluate_workflow(
            settings,
            chosen,
            facts=facts,
            stage=request.prospect.stage,
            ladder=steps,
            focus=selection.focus,
            place=market.place,
            currency=resolve_price(primary, market.place).currency if primary else None,
            payment=payment_progress(request.facts),
            receipt_in_hand=request.action.action_type is SalesActionType.REPLY
            and _is_receipt(history),
        )
    if by_person:
        discovery = not_needed("a promised callback is not the moment to ask questions")
    else:
        discovery = plan_discovery(
            settings,
            selection=selection,
            facts=facts,
            history=history,
            language=language,
            now=now,
            advance_keys=workflow.ask_keys if workflow is not None else (),
        )

    terminal = None
    if situation is CommercialSituation.FOLLOW_UP:
        then = follow_up.exhausted_then(history.unanswered, primary)
        if then is not None:
            terminal = TerminalOutcome(
                decision_type=_THEN_DECISION[then],
                reason=f"the configured follow-up steps are used up: {then}",
            )

    mode = _no_product_mode(settings, origin)
    objective = _objective(
        situation, selection, discovery, steps, settings, mode, workflow, by_person
    )
    presenting = objective in (Objective.PRESENT_OFFER, Objective.PROPOSE_NEXT_STEP)
    no_products = selection.basis in (SelectionBasis.NO_CATALOG, SelectionBasis.NONE_SUITABLE)
    forbidden = (
        no_products
        or objective is Objective.HAND_OVER
        or (not selection.focus and mode == "ask_open_question")
    )
    return ConversationPlan(
        situation=situation,
        origin=origin,
        objective=objective,
        allowed_decisions=_allowed_decisions(situation, settings),
        discovery=discovery,
        steps=steps,
        may_present=not forbidden,
        must_present=presenting and not forbidden,
        no_product_mode=None if selection.focus else mode,
        greeting=_greeting(settings, history),
        follow_up_intent=(
            follow_up.step_intent(history.unanswered, primary)
            if situation is CommercialSituation.FOLLOW_UP
            else None
        ),
        terminal=terminal,
        workflow=workflow,
    )


def _is_receipt(history: HistoryView) -> bool:
    """The message being answered is media that may be a receipt (the prospect just sent it)."""
    last = history.last_inbound
    return last is not None and last.metadata.get("type") in RECEIPT_MEDIA


def _callback_by_person(settings: ConsultativeSalesSettings, primary: Product | None) -> bool:
    """A promised callback that falls due is carried out by a person, unless the client says the
    agent does it. (The product's own appointment settings win over the client's default.)"""
    appointment = primary.appointment if primary is not None else None
    if appointment is None or not appointment.supported:
        appointment = settings.conversion.appointment
    return appointment is None or appointment.handled_by == "human"


def _effective_origin(origin: ContactOrigin, history: HistoryView) -> ContactOrigin:
    """The Engine marks every reply as `inbound`, even in a conversation the agent started. The
    history settles it: if our own message came first, the agent started it. Nothing is guessed for
    an origin that does not say."""
    if origin is ContactOrigin.INBOUND and history.first_direction is Direction.OUTBOUND:
        return ContactOrigin.OUTBOUND
    return origin


def _no_product_mode(settings: ConsultativeSalesSettings, origin: ContactOrigin) -> str:
    """How to proceed while no product is in focus. A conversation that does not say who started
    it is handled as outbound: nothing about the prospect's own initiative is assumed."""
    if origin is ContactOrigin.INBOUND:
        return settings.incoming.no_product
    return settings.discovery.when_no_product.outbound


def _objective(
    situation: CommercialSituation,
    selection: ProductSelection,
    discovery: DiscoveryPlan,
    steps: Mapping[str, StepStatus],
    settings: ConsultativeSalesSettings,
    mode: str,
    workflow: WorkflowState | None,
    callback_by_person: bool,
) -> Objective:
    if situation is CommercialSituation.CALLBACK and callback_by_person:
        return Objective.HAND_OVER
    if situation in (CommercialSituation.FOLLOW_UP, CommercialSituation.REMINDER):
        return Objective.RE_ENGAGE
    if workflow is not None and workflow.advancing:
        return Objective.ADVANCE_WORKFLOW
    if selection.focus:
        proposal = steps.get("propose_next_step")
        ready = (proposal is not None and proposal.satisfied) or _shortcut_proposes(
            settings, selection
        )
        if ready and discovery.aim is None:
            return Objective.PROPOSE_NEXT_STEP
        return Objective.PRESENT_OFFER
    if selection.basis is SelectionBasis.UNRESOLVED and mode == "present_overview":
        return Objective.PRESENT_OFFER
    if situation in _FIRST_TOUCH:
        return Objective.OPEN_CONVERSATION
    return Objective.UNDERSTAND_NEED


def _shortcut_proposes(settings: ConsultativeSalesSettings, selection: ProductSelection) -> bool:
    strongest = selection.strongest
    return strongest is not None and any(
        shortcut.when.interest is not None
        and shortcut.do == "propose_next_step"
        and strongest.at_least(InterestLevel(shortcut.when.interest))
        for shortcut in settings.discovery.shortcuts
    )


def _allowed_decisions(
    situation: CommercialSituation, settings: ConsultativeSalesSettings
) -> frozenset[DecisionType]:
    decisions = _ALL_DECISIONS
    if not settings.handoff.enabled:
        decisions = decisions - {DecisionType.HANDOFF_TO_HUMAN}
    if situation is CommercialSituation.FIRST_CONTACT:
        return decisions - {DecisionType.CLOSE, DecisionType.SCHEDULE_FOLLOW_UP}
    if situation is CommercialSituation.CALLBACK:
        return decisions - {DecisionType.CLOSE}
    return decisions


def _greeting(settings: ConsultativeSalesSettings, history: HistoryView) -> bool:
    match settings.communication.message.greeting:
        case "never":
            return False
        case "every_message":
            return True
        case _:
            return not history.has_prior_outbound
