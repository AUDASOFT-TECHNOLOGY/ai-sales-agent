"""Deciding whether the model's answer may become a decision.

The model proposes; this module disposes. An answer is accepted only if it is consistent with the
plan, the client's configuration and what is known, and every reason it is not is collected so the
model can be told what to fix. Nothing is repaired or reinterpreted on the way: an unusable answer
is rejected, never quietly turned into some other behaviour.

What is checked, in short: the decision is a known one and open in this situation; a message exists
exactly when one is sent, fits the limits and is in the language the policy expects; products exist,
may be offered, and are presented as the plan requires; a question is asked only when discovery
allows it and about something it proposed; configured rules (handoff, close, out-of-scope,
disqualification) are followed; a hypothesis never disqualifies; and any follow-up has a valid time,
computed from configuration.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sales_brain_kit.consultative.context import CommercialContext
from sales_brain_kit.consultative.discovery import DiscoveryStatus
from sales_brain_kit.consultative.errors import BrainOutputError
from sales_brain_kit.consultative.follow_up import NextTouch
from sales_brain_kit.consultative.history import count_questions
from sales_brain_kit.consultative.language import match_language
from sales_brain_kit.consultative.output import STOP_SIGNALS, Analysis, BrainOutput
from sales_brain_kit.settings.catalog import Disqualifier, Objection, Product
from sales_core.contracts import DecisionType

MAX_REQUESTED_HORIZON = timedelta(days=366)
MAX_RATIONALE_CHARS = 300


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedOutput:
    """An answer that passed every check, with what was worked out along the way."""

    decision_type: DecisionType
    analysis: Analysis
    message: str | None = None
    language: str | None = None  # the language of the message
    selected: Product | None = None
    presented: tuple[Product, ...] = ()
    candidates: tuple[str, ...] = ()
    discovery_key: str | None = None
    touch: NextTouch | None = None
    stage: str | None = None
    handoff_reason: str | None = None
    handoff_briefing: str | None = None
    close_reason: str | None = None
    rationale: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Required:
    """A decision the configuration insists on, and why."""

    decisions: frozenset[DecisionType]
    reason: str
    no_follow_up: bool = False


def validate_output(output: BrainOutput, context: CommercialContext) -> ValidatedOutput:
    """The answer, checked. Raises `BrainOutputError` listing every problem otherwise."""
    problems: list[str] = []
    try:
        decision = DecisionType(output.decision)
    except ValueError:
        allowed = ", ".join(sorted(d.value for d in context.plan.allowed_decisions))
        raise BrainOutputError(
            [f"decision {output.decision!r} is not supported (use one of: {allowed})"]
        ) from None
    if decision not in context.plan.allowed_decisions:
        allowed = ", ".join(sorted(d.value for d in context.plan.allowed_decisions))
        problems.append(
            f"decision {decision} is not open in this situation (use one of: {allowed})"
        )

    analysis = output.analysis
    disqualified = _check_analysis(analysis, context, problems)
    required = _required(analysis, disqualified, output, context)
    if required is not None and decision not in required.decisions:
        wanted = ", ".join(sorted(d.value for d in required.decisions))
        problems.append(f"the configuration requires {wanted}: {required.reason}")

    language = context.language.expected(analysis.prospect_language, analysis.requested_language)
    message = _check_message(decision, output, language, context, problems)
    selected, presented = _check_products(
        decision, output, analysis, disqualified, language, context, problems
    )
    _check_discovery(decision, output, message, context, problems)
    stage = _check_stage(output, context, problems)
    touch = _check_follow_up(decision, output, selected, required, context, problems)
    _check_reasons(decision, output, context, problems)

    if problems:
        raise BrainOutputError(problems)
    return ValidatedOutput(
        decision_type=decision,
        analysis=analysis,
        message=message,
        language=language if message is not None else None,
        selected=selected,
        presented=presented,
        candidates=tuple(dict.fromkeys(output.product_candidates)),
        discovery_key=output.discovery_key,
        touch=touch,
        stage=stage,
        handoff_reason=_clean(output.handoff_reason),
        handoff_briefing=_clean(output.handoff_briefing),
        close_reason=_clean(output.close_reason),
        rationale=" ".join(output.rationale.split())[:MAX_RATIONALE_CHARS],
    )


def _clean(text: str | None) -> str | None:
    return text.strip() or None if text is not None else None


# ---- what the model understood ------------------------------------------------------------------


def _check_analysis(
    analysis: Analysis, context: CommercialContext, problems: list[str]
) -> dict[str, Disqualifier]:
    """Checks the references in the analysis. Returns the disqualifiers it triggers, by product."""
    settings = context.settings
    if analysis.intent is not None and analysis.intent not in settings.conversation.intents:
        problems.append(f"analysis.intent: {analysis.intent!r} is not a configured intent")
    if analysis.objection is not None and not _objection_codes(context) & {analysis.objection}:
        problems.append(f"analysis.objection: {analysis.objection!r} is not a configured objection")
    for signal in analysis.stop_signals:
        if signal not in STOP_SIGNALS:
            problems.append(f"analysis.stop_signals: {signal!r} is not a known signal")

    triggered: dict[str, Disqualifier] = {}
    for item in analysis.disqualified:
        product = context.catalog.get(item.product_id)
        qualification = product.qualification if product else None
        rule = next(
            (r for r in qualification.disqualifiers if r.id == item.rule_id)
            if qualification
            else (),
            None,
        )
        if rule is None:
            problems.append(
                f"analysis.disqualified: {item.product_id}/{item.rule_id} is not a configured rule"
            )
        elif context.facts.settled(rule.fact) is None:
            problems.append(
                f"analysis.disqualified: {item.rule_id} rests on {rule.fact!r}, which is not known "
                "(a hypothesis or a missing fact cannot disqualify a prospect)"
            )
        else:
            triggered[item.product_id] = rule
    return triggered


def _objection_codes(context: CommercialContext) -> set[str]:
    codes = {o.code for o in context.settings.conversation.objections}
    for product in context.settings.catalog.products:
        codes |= {o.code for o in product.objections}
    return codes


def _objection(code: str, context: CommercialContext, product: Product | None) -> Objection | None:
    """The product's own objection handling wins over the client's."""
    pools = [product.objections if product else (), context.settings.conversation.objections]
    return next((o for pool in pools for o in pool if o.code == code), None)


# ---- rules the configuration makes ----------------------------------------------------------------


def _required(
    analysis: Analysis,
    disqualified: dict[str, Disqualifier],
    output: BrainOutput,
    context: CommercialContext,
) -> _Required | None:
    """The decision configuration insists on, if any. Handing over outranks closing, closing
    outranks staying silent, and staying silent outranks replying."""
    settings = context.settings
    behaviour = settings.conversation.intents.get(analysis.intent) if analysis.intent else None
    handoff = settings.handoff.triggers
    on_product = context.catalog.get(output.selected_product or "") or (
        context.selection.focus[0] if context.selection.focus else None
    )

    if behaviour == "handoff" or any(t.intent == analysis.intent for t in handoff if t.intent):
        return _Required(
            decisions=frozenset({DecisionType.HANDOFF_TO_HUMAN}),
            reason=f"intent {analysis.intent!r} is handed to a human",
        )
    for rule in disqualified.values():
        if rule.effect == "handoff" or any(t.disqualified == rule.id for t in handoff):
            return _Required(
                decisions=frozenset({DecisionType.HANDOFF_TO_HUMAN}),
                reason=f"disqualifier {rule.id!r} is handed to a human",
            )
    objection = _objection(analysis.objection, context, on_product) if analysis.objection else None
    if objection is not None and objection.max_rebuttals == 0 and objection.then == "handoff":
        return _Required(
            decisions=frozenset({DecisionType.HANDOFF_TO_HUMAN}),
            reason=f"objection {objection.code!r} is handed to a human without a rebuttal",
        )
    if behaviour == "honour_and_close":
        return _Required(
            decisions=frozenset({DecisionType.CLOSE}),
            reason=f"intent {analysis.intent!r} is honoured by closing",
        )
    if analysis.explicit_refusal and "explicit_no" in settings.follow_up.stop_conditions:
        return _Required(
            decisions=frozenset(
                {DecisionType.CLOSE, DecisionType.NO_ACTION, DecisionType.SEND_MESSAGE}
            ),
            reason="the prospect said no; only close, stop or a last courteous message (with no "
            "follow-up) are allowed",
            no_follow_up=True,
        )
    if behaviour == "ignore":
        return _Required(
            decisions=frozenset({DecisionType.NO_ACTION}),
            reason=f"intent {analysis.intent!r} is ignored",
        )
    if analysis.out_of_scope:
        scope = settings.incoming.out_of_scope
        if scope.then == "handoff":
            return _Required(
                decisions=frozenset({DecisionType.HANDOFF_TO_HUMAN}),
                reason="an out-of-scope message is handed to a human",
            )
        wanted = DecisionType.NO_ACTION if scope.reply == "ignore" else DecisionType.SEND_MESSAGE
        return _Required(
            decisions=frozenset({wanted}),
            reason=f"an out-of-scope message is met with {scope.reply}",
        )
    return None


# ---- the message ------------------------------------------------------------------------------------


def _check_message(
    decision: DecisionType,
    output: BrainOutput,
    language: str,
    context: CommercialContext,
    problems: list[str],
) -> str | None:
    text = (output.message or "").strip()
    if decision is not DecisionType.SEND_MESSAGE:
        if text:
            problems.append(f"message: only send_message carries a message, not {decision}")
        return None
    limits = context.settings.communication.message
    if not text:
        problems.append("message: send_message needs a non-empty message")
        return None
    if len(text) > limits.max_chars:
        problems.append(f"message: {len(text)} characters exceed the limit of {limits.max_chars}")
    if count_questions(text) > limits.max_questions:
        problems.append(f"message: more than {limits.max_questions} question(s)")
    if output.language is None:
        problems.append("language: send_message must declare the language of the message")
    elif match_language(output.language, [language]) is None:
        problems.append(
            f"language: the message must be in {language!r} "
            f"(reason: {context.language.basis}), not {output.language!r}"
        )
    return text


# ---- products -----------------------------------------------------------------------------------


def _check_products(
    decision: DecisionType,
    output: BrainOutput,
    analysis: Analysis,
    disqualified: dict[str, Disqualifier],
    language: str,
    context: CommercialContext,
    problems: list[str],
) -> tuple[Product | None, tuple[Product, ...]]:
    catalog, plan = context.catalog, context.plan
    named = [*output.presented_products, *output.product_candidates]
    if output.selected_product:
        named.append(output.selected_product)
    for product_id in dict.fromkeys(named):
        if catalog.get(product_id) is None:
            problems.append(f"products: {product_id!r} is not in the catalog")

    selected = catalog.get(output.selected_product) if output.selected_product else None
    listed = [catalog.get(i) for i in dict.fromkeys(output.presented_products)]
    presented = tuple(p for p in listed if p is not None)
    if selected is not None and presented and selected not in presented:
        problems.append("products: selected_product must be one of presented_products")
    if selected is not None and not presented and decision is DecisionType.SEND_MESSAGE:
        presented = (selected,)

    if presented and decision is not DecisionType.SEND_MESSAGE:
        problems.append("products: nothing is presented without a message")
    offered = {p.id: p for p in (*presented, *([selected] if selected else []))}
    for product in offered.values():
        reason = catalog.exclusion(product, language)
        if reason is not None:
            problems.append(f"products: {product.id!r} may not be offered ({reason})")
        if product.id in disqualified and disqualified[product.id].effect == "not_a_fit":
            problems.append(f"products: {product.id!r} is not a fit for this prospect")

    presentation = context.settings.catalog.presentation
    limit = 1 if presentation.style == "single_best" else presentation.max_products_per_message
    if len(presented) > limit:
        problems.append(f"products: at most {limit} product(s) may be presented in a message")
    if presented and not plan.may_present:
        mode = plan.no_product_mode or plan.objective
        problems.append(f"products: no product may be presented now ({mode})")

    behaviour = (
        context.settings.conversation.intents.get(analysis.intent) if analysis.intent else None
    )
    # Answering a question (or a price request) still presents the product; only a redirect, a
    # refusal or an out-of-scope message can do without.
    exempt = (
        analysis.out_of_scope
        or analysis.explicit_refusal
        or (behaviour is not None and behaviour != "answer_then_continue")
    )
    if decision is DecisionType.SEND_MESSAGE and plan.must_present and not presented and not exempt:
        problems.append(
            f"products: the objective is {plan.objective}; the message must present a product "
            "(set selected_product)"
        )
    return selected, presented


# ---- questions ------------------------------------------------------------------------------------


def _check_discovery(
    decision: DecisionType,
    output: BrainOutput,
    message: str | None,
    context: CommercialContext,
    problems: list[str],
) -> None:
    key = output.discovery_key
    if key is None:
        return
    discovery = context.plan.discovery
    if decision is not DecisionType.SEND_MESSAGE:
        problems.append("discovery_key: a question needs a message")
        return
    if discovery.status is not DiscoveryStatus.ASK:
        problems.append(f"discovery_key: no question is needed or allowed now ({discovery.reason})")
        return
    if key not in discovery.target_keys:
        problems.append(
            f"discovery_key: {key!r} is not needed for {discovery.aim} "
            f"(ask only about: {', '.join(discovery.target_keys)})"
        )
    signalled = set(output.analysis.stop_signals) & set(context.settings.discovery.stop_signals)
    if signalled:
        problems.append(
            f"discovery_key: the prospect signalled {', '.join(sorted(signalled))}: answer, "
            "do not ask"
        )
    if _shortcut_skips_questions(output.analysis, context):
        problems.append(f"discovery_key: intent {output.analysis.intent!r} skips discovery")
    asked = count_questions(message) if message else 0
    if asked == 0:
        problems.append(f"discovery_key: {key!r} declared, but the message asks no question")
    elif asked > discovery.max_questions:
        problems.append(
            f"message: {asked} questions, but at most {discovery.max_questions} allowed"
        )


def _shortcut_skips_questions(analysis: Analysis, context: CommercialContext) -> bool:
    return analysis.intent is not None and any(
        shortcut.when.intent == analysis.intent
        and shortcut.do in ("propose_next_step", "recommend_now")
        for shortcut in context.settings.discovery.shortcuts
    )


# ---- stage, follow-up and reasons ----------------------------------------------------------------------


def _check_stage(
    output: BrainOutput, context: CommercialContext, problems: list[str]
) -> str | None:
    if output.stage is None:
        return None
    if output.stage not in {stage.id for stage in context.settings.stages}:
        problems.append(f"stage: {output.stage!r} is not a configured stage")
        return None
    return output.stage


def _check_follow_up(
    decision: DecisionType,
    output: BrainOutput,
    selected: Product | None,
    required: _Required | None,
    context: CommercialContext,
    problems: list[str],
) -> NextTouch | None:
    choice = output.follow_up
    policy = context.follow_up
    primary = selected or (context.selection.focus[0] if context.selection.focus else None)

    if decision in (DecisionType.HANDOFF_TO_HUMAN, DecisionType.CLOSE):
        if choice.situation is not None or choice.requested_at is not None:
            problems.append(f"follow_up: nothing is scheduled with {decision}")
        return None
    if required is not None and required.no_follow_up and choice.situation is not None:
        problems.append("follow_up: no follow-up after the prospect said no")
        return None

    situation = choice.situation
    if situation is None:
        if decision is DecisionType.SCHEDULE_FOLLOW_UP:
            problems.append("follow_up.situation: schedule_follow_up must name the situation")
        if choice.requested_at is not None:
            problems.append("follow_up.requested_at: given without a situation")
        if decision is not DecisionType.SEND_MESSAGE or (required and required.no_follow_up):
            return None
        situation = policy.default_situation(primary)
        if situation is None:
            return None

    table = policy.table(situation, primary)
    if table is None:
        problems.append(f"follow_up.situation: {situation!r} is not configured")
        return None

    requested = _requested_time(choice.requested_at, context, problems)
    takes_time = table.use == "prospect_preference" or situation == "appointment_reminder"
    if choice.requested_at is not None and not takes_time:
        problems.append(f"follow_up.requested_at: {situation!r} takes its time from configuration")
    elif requested is not None:
        _check_notice(situation, requested, primary, context, problems)

    touch = policy.next_touch(
        situation,
        product=primary,
        unanswered=context.history.unanswered,
        now=context.now,
        requested_at=requested if takes_time else None,
    )
    if touch.problem is not None:
        if choice.requested_at is None or requested is not None:
            problems.append(f"follow_up: {touch.problem}")
        return None
    if decision is DecisionType.SCHEDULE_FOLLOW_UP and touch.due_at is None:
        problems.append(
            f"follow_up: nothing more can be scheduled for {situation!r} "
            f"(the steps are used up; then: {touch.then})"
        )
    return touch


def _requested_time(
    text: str | None, context: CommercialContext, problems: list[str]
) -> datetime | None:
    if text is None:
        return None
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        problems.append(f"follow_up.requested_at: {text!r} is not an ISO 8601 date and time")
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        problems.append("follow_up.requested_at: must include a UTC offset")
        return None
    if moment <= context.now:
        problems.append("follow_up.requested_at: must be in the future")
        return None
    if moment - context.now > MAX_REQUESTED_HORIZON:
        problems.append("follow_up.requested_at: too far ahead")
        return None
    return moment


def _check_notice(
    situation: str,
    requested: datetime,
    product: Product | None,
    context: CommercialContext,
    problems: list[str],
) -> None:
    """A callback carried out by a human needs the notice the product asks for."""
    appointment = product.appointment if product else None
    if situation != "callback_promised" or appointment is None or not appointment.supported:
        return
    earliest = context.now + timedelta(hours=appointment.min_notice_hours)
    if requested < earliest:
        problems.append(
            f"follow_up.requested_at: the product needs {appointment.min_notice_hours}h notice"
        )


def _check_reasons(
    decision: DecisionType, output: BrainOutput, context: CommercialContext, problems: list[str]
) -> None:
    if decision is DecisionType.HANDOFF_TO_HUMAN:
        if not _clean(output.handoff_reason):
            problems.append("handoff_reason: a handoff must say why")
        if context.settings.handoff.briefing and not _clean(output.handoff_briefing):
            problems.append("handoff_briefing: a handoff needs a short briefing for the human")
    if decision is DecisionType.CLOSE and not _clean(output.close_reason):
        problems.append("close_reason: closing must say why")
