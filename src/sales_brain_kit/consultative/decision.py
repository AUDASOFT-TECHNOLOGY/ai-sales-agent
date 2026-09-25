"""From a validated answer, or a terminal outcome, to the Core's `BrainDecision`.

The `BrainDecision` is the only thing the Core sees. The Brain's own understanding (objective,
selected product, the question asked, ...) travels as short `metadata` strings for operations and
debugging; the Core stores or ignores them and never acts on them.
"""

from sales_brain_kit.consultative.context import CommercialContext
from sales_brain_kit.consultative.language import localize
from sales_brain_kit.consultative.payment import RECEIPT_MEDIA
from sales_brain_kit.consultative.validation import ValidatedOutput
from sales_brain_kit.settings.conversion import PAYMENT_KINDS
from sales_core.contracts import (
    PAYMENT_CLAIMED_KEY,
    PAYMENT_PROOF_KEY,
    BrainDecision,
    DecisionType,
    FactProposal,
    FactSource,
    FactStatus,
    MessageDraft,
    MessageId,
    PaymentProgress,
    SalesActionType,
    payment_progress,
)

FAMILY_TAG = "consultative_sales"


def build_decision(validated: ValidatedOutput, context: CommercialContext) -> BrainDecision:
    touch = validated.touch
    due = touch.due_at if touch is not None else None
    message = None
    if validated.decision_type is DecisionType.SEND_MESSAGE and validated.message is not None:
        message = MessageDraft(text=validated.message, language=validated.language)
    return BrainDecision(
        decision_type=validated.decision_type,
        message=message,
        next_follow_up_at=due,
        next_action_type=touch.action_type if touch is not None and due is not None else None,
        new_stage=validated.stage,
        rationale=validated.rationale or f"{context.plan.objective}: {validated.decision_type}",
        metadata=_metadata(validated, context),
        proposed_facts=_payment_proposals(validated, context),
    )


def _payment_proposals(
    validated: ValidatedOutput, context: CommercialContext
) -> tuple[FactProposal, ...]:
    """What the prospect said or sent about paying, proposed as *discovered* facts anchored to
    their own message. Never a confirmation: the Core refuses to record one from a Brain, and a
    person at the client is the only one who confirms. Only a reply carries such a message."""
    request = context.request
    last = context.history.last_inbound
    if request.action.action_type is not SalesActionType.REPLY or last is None:
        return ()
    progress = payment_progress(request.facts)
    if progress is PaymentProgress.PAID:
        return ()
    proposals: list[FactProposal] = []
    if validated.analysis.payment_claimed and progress is PaymentProgress.NONE:
        proposals.append(_statement(PAYMENT_CLAIMED_KEY, "claimed", last.message_id))
    kind = last.metadata.get("type")
    state = context.plan.workflow
    at_payment = (
        state is not None and state.current is not None and state.current.kind in PAYMENT_KINDS
    )
    already = any(
        f.key == PAYMENT_PROOF_KEY
        and f.evidence is not None
        and f.evidence.message_id == last.message_id
        for f in request.facts
    )
    if kind in RECEIPT_MEDIA and at_payment and not already:
        proposals.append(_statement(PAYMENT_PROOF_KEY, kind, last.message_id))
    return tuple(proposals)


def _statement(key: str, value: str, message_id: MessageId) -> FactProposal:
    return FactProposal(
        key=key,
        value=value,
        status=FactStatus.DISCOVERED,
        source=FactSource.PROSPECT,
        message_id=message_id,
    )


def terminal_decision(context: CommercialContext) -> BrainDecision:
    """The decision the configuration makes by itself (no model call)."""
    outcome = context.plan.terminal
    if outcome is None:
        raise ValueError("the plan has no terminal outcome")
    details: dict[str, str | None] = {"terminal": "true"}
    if outcome.decision_type is DecisionType.HANDOFF_TO_HUMAN:
        details["handoff_reason"] = outcome.reason
        details["handoff_briefing"] = (
            f"No reply after {context.history.outbound_count} message(s); "
            "the follow-up cadence is used up."
        )
        details["handoff_message"] = _handoff_message(context)
    elif outcome.decision_type is DecisionType.CLOSE:
        details["close_reason"] = outcome.reason
    return BrainDecision(
        decision_type=outcome.decision_type,
        rationale=outcome.reason,
        metadata=_compact({**_base_metadata(context), **details}),
    )


def _base_metadata(context: CommercialContext) -> dict[str, str | None]:
    plan = context.plan
    return {
        "brain": FAMILY_TAG,
        "situation": str(plan.situation),
        "objective": str(plan.objective),
        "origin": str(plan.origin),
        "discovery": str(plan.discovery.status),
        "qualification_sufficient": _flag(plan.qualification_sufficient),
        "market": context.market.market.id if context.market.market else None,
        "workflow": plan.workflow.workflow.id if plan.workflow else None,
        "workflow_step": (
            plan.workflow.current.id if plan.workflow and plan.workflow.current else None
        ),
    }


def _metadata(validated: ValidatedOutput, context: CommercialContext) -> dict[str, str]:
    analysis = validated.analysis
    touch = validated.touch
    details: dict[str, str | None] = {
        **_base_metadata(context),
        "language": validated.language,
        "selected_product": validated.selected.id if validated.selected else None,
        "presented_products": ",".join(p.id for p in validated.presented) or None,
        "product_candidates": ",".join(validated.candidates) or None,
        "discovery_key": validated.discovery_key,
        "intent": analysis.intent,
        "objection": analysis.objection,
        "follow_up_situation": touch.situation if touch is not None else None,
        "handoff_reason": validated.handoff_reason,
        "handoff_briefing": validated.handoff_briefing,
        "close_reason": validated.close_reason,
        "exact_values": ",".join(validated.exact_values) or None,
    }
    if validated.decision_type is DecisionType.HANDOFF_TO_HUMAN:
        details["handoff_message"] = _handoff_message(context)
    return _compact(details)


def _handoff_message(context: CommercialContext) -> str | None:
    message = context.settings.handoff.message
    default = context.settings.communication.languages.default
    return localize(message, context.language.language, default)


def _flag(value: bool | None) -> str | None:
    return None if value is None else str(value).lower()


def _compact(details: dict[str, str | None]) -> dict[str, str]:
    return {key: value for key, value in details.items() if value}
