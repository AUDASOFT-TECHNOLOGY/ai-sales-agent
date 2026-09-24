"""Where the prospect stands in the client's conversion workflow, worked out from configuration and
what is known: nothing is stored and nothing is assumed.

The workflow is an ordered list of steps (see `settings/conversion.py`). A step is *done* when what
the client said about it holds: the discovery ladder's requirement for `recommend` and `qualify`,
the facts and stage of `completes_when` for the others. The current step is the first one that is
not done, after following any prerequisite steps it names. From it the state derives everything the
rest of the Brain needs: what to ask for, whether a person is involved, what exact values (payment
methods, a booking link) may be given now, and what happens when the step cannot proceed.

A step is *unavailable* when the resource it needs is not configured for this prospect (no payment
method applies, no appointment is configured); its `fallback` then decides: hand over, close, wait,
skip the step or go to another step. The default fallback is a handoff, or waiting when handoffs
are switched off.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sales_brain_kit.consultative.applicability import Place
from sales_brain_kit.consultative.discovery import StepStatus
from sales_brain_kit.consultative.facts import KnownFacts
from sales_brain_kit.consultative.payment import MethodOffer, applicable_methods
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_brain_kit.settings.catalog import Appointment, Product
from sales_brain_kit.settings.conversion import (
    PAYMENT_KINDS,
    Completion,
    Workflow,
    WorkflowStep,
)
from sales_brain_kit.settings.market import Market
from sales_core.contracts import DecisionType, FactStatus

# Steps that are the discovery ladder or informational: they never take over the conversation.
LADDER_KINDS = frozenset({"recommend", "qualify", "inform"})


class WorkflowStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"  # the current step needs a fact that is not known yet
    UNAVAILABLE = "unavailable"  # the step's resource is not configured: its fallback applies
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class StepProgress:
    step: WorkflowStep
    done: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowState:
    workflow: Workflow
    progress: tuple[StepProgress, ...]
    current: WorkflowStep | None  # None once every step is done
    status: WorkflowStatus
    reason: str
    human: str = "none"  # what the current step asks of a person: none, optional or required
    ask_keys: tuple[str, ...] = ()  # facts the current step still needs from the prospect
    blocked_by: tuple[str, ...] = ()
    fallback: str | None = None  # handoff, close or park, when the step is unavailable
    forced: frozenset[DecisionType] = frozenset()  # what the configuration insists on, if anything
    forced_reason: str | None = None
    methods: tuple[MethodOffer, ...] = ()  # payment methods that apply, for a payment step
    appointment: Appointment | None = None  # for an appointment or callback step

    @property
    def advancing(self) -> bool:
        """The current step is a conversion step, which takes over what the conversation is for."""
        return (
            self.current is not None
            and self.current.kind not in LADDER_KINDS
            and self.status is not WorkflowStatus.COMPLETED
        )


def select_workflow(
    settings: ConsultativeSalesSettings, product: Product | None, market: Market | None
) -> Workflow | None:
    """The product's workflow, else the market's, else the client's default."""
    conversion = settings.conversion
    by_id = {workflow.id: workflow for workflow in conversion.workflows}
    for name in (
        product.workflow if product else None,
        market.workflow if market else None,
        conversion.default_workflow,
    ):
        if name is not None and name in by_id:
            return by_id[name]
    return None


def evaluate_workflow(
    settings: ConsultativeSalesSettings,
    workflow: Workflow,
    *,
    facts: KnownFacts,
    stage: str | None,
    ladder: Mapping[str, StepStatus],
    focus: tuple[Product, ...],
    place: Place,
    currency: str | None,
) -> WorkflowState:
    by_id = {step.id: step for step in workflow.steps}
    order = {step.id: index for index, step in enumerate(workflow.steps)}
    primary = focus[0] if focus else None

    def successor(step: WorkflowStep) -> WorkflowStep | None:
        if step.next is not None:
            return by_id.get(step.next)
        following = order[step.id] + 1
        return workflow.steps[following] if following < len(workflow.steps) else None

    def completion_holds(completion: Completion) -> bool:
        for condition in completion.facts:
            fact = facts.settled(condition.key)
            if fact is None:
                return False
            if condition.min_status == "confirmed" and fact.status is not FactStatus.CONFIRMED:
                return False
            if condition.equals and fact.value.strip().casefold() not in {
                value.strip().casefold() for value in condition.equals
            }:
                return False
        return completion.stage is None or stage == completion.stage

    def done(step: WorkflowStep) -> bool:
        match step.kind:
            case "recommend":
                status = ladder.get("recommend")
                return bool(focus) and (status is None or status.satisfied)
            case "qualify":
                status = ladder.get("qualify")
                return status is None or status.satisfied
            case "handoff" | "close":
                return False
            case _:
                return completion_holds(step.completes_when) if step.completes_when else True

    def resources(
        step: WorkflowStep,
    ) -> tuple[tuple[MethodOffer, ...], Appointment | None, str | None]:
        """The methods and appointment settings the step can use, or why it cannot proceed."""
        if step.kind in PAYMENT_KINDS:
            methods = applicable_methods(
                settings.payment,
                product_id=primary.id if primary else None,
                place=place,
                currency=currency,
                only=step.payment.methods if step.payment else (),
            )
            return methods, None, None if methods else "no payment method applies to this prospect"
        if step.kind in ("appointment", "callback"):
            appointment = step.appointment
            if appointment is None and primary is not None and primary.appointment is not None:
                appointment = primary.appointment if primary.appointment.supported else None
            appointment = appointment or settings.conversion.appointment
            if appointment is None or not appointment.supported:
                return (), None, "no appointment is configured for this prospect"
            return (), appointment, None
        return (), None, None

    handoff_enabled = settings.handoff.enabled
    passed: set[str] = set()
    visited: set[str] = set()
    step: WorkflowStep | None = workflow.steps[0]
    state: WorkflowState | None = None

    while step is not None and state is None:
        if step.id in visited:  # a fallback loop; validation forbids `next` loops
            state = _state(workflow, passed, step, WorkflowStatus.IN_PROGRESS, "the steps loop")
            break
        visited.add(step.id)
        if done(step):
            passed.add(step.id)
            step = successor(step)
            continue
        pending = [
            by_id[other]
            for other in (step.requires.steps if step.requires else ())
            if other in by_id and other not in passed and not done(by_id[other])
        ]
        if pending and pending[0].id not in visited:
            step = pending[0]
            continue

        methods, appointment, unavailable = resources(step)
        if unavailable is not None:
            fallback = step.fallback or ("handoff" if handoff_enabled else "park")
            if fallback == "skip":
                passed.add(step.id)
                step = successor(step)
                continue
            if fallback in by_id:
                step = by_id[fallback]
                continue
            forced = {
                "handoff": frozenset({DecisionType.HANDOFF_TO_HUMAN}),
                "close": frozenset({DecisionType.CLOSE}),
                "park": frozenset({DecisionType.NO_ACTION, DecisionType.SCHEDULE_FOLLOW_UP}),
            }[fallback]
            state = _state(
                workflow,
                passed,
                step,
                WorkflowStatus.UNAVAILABLE,
                unavailable,
                human=_human(step, handoff_enabled),
                fallback=fallback,
                forced=forced,
                forced_reason=f"step {step.id!r} cannot proceed ({unavailable}); fallback: {fallback}",
            )
            break

        missing_required = tuple(
            key
            for key in (step.requires.facts if step.requires else ())
            if facts.settled(key) is None
        )
        to_collect = tuple(
            key
            for key in (step.registration.collects if step.registration else ())
            if facts.settled(key) is None and key not in missing_required
        )
        forced, forced_reason = _forced(step, appointment, handoff_enabled)
        state = _state(
            workflow,
            passed,
            step,
            WorkflowStatus.BLOCKED if missing_required else WorkflowStatus.IN_PROGRESS,
            (
                f"step {step.id!r} needs: {', '.join(missing_required)}"
                if missing_required
                else f"step {step.id!r} is in progress"
            ),
            human=_human(step, handoff_enabled),
            ask_keys=() if forced else (*missing_required, *to_collect),
            blocked_by=missing_required,
            forced=forced,
            forced_reason=forced_reason,
            methods=methods,
            appointment=appointment,
        )

    if state is None:
        state = _state(
            workflow, passed, None, WorkflowStatus.COMPLETED, "every step of the workflow is done"
        )
    return state


def _human(step: WorkflowStep, handoff_enabled: bool) -> str:
    return step.effective_human if handoff_enabled else "none"


def _forced(
    step: WorkflowStep, appointment: Appointment | None, handoff_enabled: bool
) -> tuple[frozenset[DecisionType], str | None]:
    """What the configuration requires at this step, regardless of what the model would prefer."""
    if step.kind == "close":
        return frozenset({DecisionType.CLOSE}), f"step {step.id!r} closes the conversation"
    person = frozenset({DecisionType.HANDOFF_TO_HUMAN})
    if step.kind == "handoff" or (handoff_enabled and step.effective_human == "required"):
        return person, f"step {step.id!r} is carried out by a person"
    if step.registration is not None and step.registration.mode == "human_assisted":
        return person, f"registration at step {step.id!r} is assisted by a person"
    if appointment is not None and appointment.mode == "human_scheduling":
        return person, f"a person arranges the appointment at step {step.id!r}"
    return frozenset(), None


def _state(
    workflow: Workflow,
    passed: set[str],
    current: WorkflowStep | None,
    status: WorkflowStatus,
    reason: str,
    **details: object,
) -> WorkflowState:
    progress = tuple(StepProgress(step=s, done=s.id in passed) for s in workflow.steps)
    return WorkflowState(
        workflow=workflow,
        progress=progress,
        current=current,
        status=status,
        reason=reason,
        **details,  # type: ignore[arg-type]
    )
