"""Minimum necessary discovery: ask only what a next step needs and nothing else.

Before any question the planner asks: *will the answer change what we do next?* It answers from
configuration alone. The client's `discovery.sufficiency` says what each next step requires; a
question is proposed only for something such a step still lacks, and only if the question budget
allows it. So:

- what is already known (confirmed, discovered, or held in the lead's data) is never asked again;
- the moment the requirement of the step being worked toward is met, discovery stops, however
  much else could be learned; nothing is a checklist;
- a hypothesis does not satisfy a requirement, but it may be offered to the prospect to confirm;
- asking is limited per message, in a row and per conversation, and a product may only tighten
  those limits.

The planner proposes candidates; the model writes the question and validation holds it to them.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sales_brain_kit.consultative.facts import INTEREST_KEY, KnownFacts
from sales_brain_kit.consultative.history import HistoryView
from sales_brain_kit.consultative.interest import InterestLevel
from sales_brain_kit.consultative.language import match_language
from sales_brain_kit.consultative.selection import ProductSelection, SelectionBasis
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_brain_kit.settings.catalog import Product
from sales_brain_kit.settings.discovery import DiscoveryField, Requirement

# The order in which next steps are worked toward. `advance` is not configured under `sufficiency`:
# it is what the current step of the conversion workflow still needs from the prospect.
LADDER = ("recommend", "qualify", "propose_next_step")
ADVANCE = "advance"
_IMPORTANCE = {"critical": 0, "useful": 1, "optional": 2}
MAX_TARGETS = 5


class DiscoveryStatus(StrEnum):
    NOT_NEEDED = "not_needed"  # enough is known for the next step: do not ask
    ASK = "ask"  # something the next step needs is missing: one natural question may be asked
    BLOCKED = "blocked"  # something is missing but the question budget is used up


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscoveryTarget:
    key: str  # a declared field, or `interest` (which offer suits the prospect)
    label: str | None = None
    importance: str = "useful"
    changes: tuple[str, ...] = ()  # what the answer would change
    hints: tuple[str, ...] = ()  # example phrasings in the message language, never scripts
    hypothesis: str | None = None  # a guess that may be offered for confirmation
    recheck: bool = False  # known once, but old enough to be worth confirming again


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscoveryPlan:
    status: DiscoveryStatus
    reason: str  # a short, human-readable explanation, for the audit trail
    aim: str | None = None  # the next step the answers are needed for
    targets: tuple[DiscoveryTarget, ...] = ()
    max_questions: int = 0  # for the next message
    give_before_ask: bool = True

    @property
    def target_keys(self) -> tuple[str, ...]:
        return tuple(target.key for target in self.targets)


def not_needed(reason: str) -> DiscoveryPlan:
    return DiscoveryPlan(status=DiscoveryStatus.NOT_NEEDED, reason=reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class StepStatus:
    """Whether one next step has what it requires."""

    step: str
    satisfied: bool
    missing_all: tuple[str, ...] = ()  # every one of these is needed
    missing_any: tuple[str, ...] = ()  # any one of these would do


def applicable_fields(
    settings: ConsultativeSalesSettings, focus: Sequence[Product]
) -> dict[str, DiscoveryField]:
    """Global fields plus the extra fields of the products in focus."""
    fields = {f.key: f for f in settings.discovery.fields}
    for product in focus:
        if product.discovery is not None:
            fields.update({f.key: f for f in product.discovery.extra_fields})
    return fields


def evaluate_steps(
    settings: ConsultativeSalesSettings,
    fields: Mapping[str, DiscoveryField],
    facts: KnownFacts,
    selection: ProductSelection,
    now: datetime,
) -> dict[str, StepStatus]:
    """For each configured next step: does the Brain already know what it requires?"""
    statuses: dict[str, StepStatus] = {}
    for step, requirement in settings.discovery.sufficiency.items():
        satisfied = _satisfied_test(requirement, step, fields, facts, selection, now)
        missing_all = tuple(
            key for key in requirement.all_of if _applies(key, fields) and not satisfied(key)
        )
        options = tuple(key for key in requirement.any_of if _applies(key, fields))
        missing_any = options if options and not any(satisfied(key) for key in options) else ()
        statuses[step] = StepStatus(
            step=step,
            satisfied=not missing_all and not missing_any,
            missing_all=missing_all,
            missing_any=missing_any,
        )
    return statuses


def _applies(key: str, fields: Mapping[str, DiscoveryField]) -> bool:
    """A key declared only for a product that is not in focus asks nothing of this conversation."""
    return key == INTEREST_KEY or key in fields


def _satisfied_test(
    requirement: Requirement,
    step: str,
    fields: Mapping[str, DiscoveryField],
    facts: KnownFacts,
    selection: ProductSelection,
    now: datetime,
) -> Callable[[str], bool]:
    accepted = InterestLevel(requirement.accept)

    def satisfied(key: str) -> bool:
        if key == INTEREST_KEY:
            strongest = selection.strongest
            if strongest is not None and strongest.at_least(accepted):
                return True
            # With a single offer there is nothing to choose between, so recommending it needs
            # no discovery (it is still not an interest, and never becomes one).
            return step == "recommend" and selection.basis is SelectionBasis.SOLE_OFFER
        return _is_known(fields[key], facts, now)

    return satisfied


def _is_known(field: DiscoveryField, facts: KnownFacts, now: datetime) -> bool:
    fact = facts.settled(field.key)
    if fact is None:
        return False
    if field.recheck_after_days is not None and fact.observed_at is not None:
        return now - fact.observed_at <= timedelta(days=field.recheck_after_days)
    return True


def _budget(settings: ConsultativeSalesSettings, focus: Sequence[Product]) -> tuple[int, int, int]:
    """(per message, in a row, per conversation): the global limits, tightened by products."""
    base = settings.discovery.budget
    per_message = min(base.max_questions_per_message, settings.communication.message.max_questions)
    in_a_row = base.max_consecutive_questions
    total = base.max_questions_per_conversation
    for product in focus:
        override = product.discovery.budget if product.discovery else None
        if override is None:
            continue
        if override.max_questions_per_message is not None:
            per_message = min(per_message, override.max_questions_per_message)
        if override.max_consecutive_questions is not None:
            in_a_row = min(in_a_row, override.max_consecutive_questions)
        if override.max_questions_per_conversation is not None:
            total = min(total, override.max_questions_per_conversation)
    return per_message, in_a_row, total


def plan_discovery(
    settings: ConsultativeSalesSettings,
    *,
    selection: ProductSelection,
    facts: KnownFacts,
    history: HistoryView,
    language: str,
    now: datetime,
    advance_keys: Sequence[str] = (),
) -> DiscoveryPlan:
    focus = selection.focus
    if not selection.candidates:
        return not_needed("there is no product to recommend")
    fields = applicable_fields(settings, focus)
    statuses = evaluate_steps(settings, fields, facts, selection, now)
    strongest = selection.strongest
    advance = tuple(key for key in advance_keys if key in fields)

    shortcut_reason = None
    for shortcut in settings.discovery.shortcuts:
        wanted = shortcut.when.interest
        if (
            wanted is not None
            and strongest is not None
            and strongest.at_least(InterestLevel(wanted))
            and shortcut.do in ("propose_next_step", "recommend_now")
        ):
            shortcut_reason = f"shortcut: interest is {strongest} ({shortcut.do})"
            break
    if shortcut_reason is not None:
        # The shortcut skips the ladder, not what the conversion workflow still needs to know.
        statuses = {}
        if not advance:
            return not_needed(shortcut_reason)

    aim, keys = _first_gap(statuses, fields, advance)
    if aim is None:
        return not_needed("enough is known for the next step")

    per_message, in_a_row, total = _budget(settings, focus)
    blocked = None
    if per_message <= 0:
        blocked = "questions are not allowed in a message"
    elif history.questions_asked >= total:
        blocked = "the questions allowed in this conversation are used up"
    elif history.consecutive_questions >= in_a_row:
        blocked = "too many questions in a row: give something before asking again"
    if blocked is not None:
        return DiscoveryPlan(
            status=DiscoveryStatus.BLOCKED, reason=blocked, aim=aim, give_before_ask=True
        )

    targets = tuple(_target(key, fields, facts, language, now) for key in keys[:MAX_TARGETS])
    return DiscoveryPlan(
        status=DiscoveryStatus.ASK,
        reason=f"{aim} still needs: {', '.join(keys)}",
        aim=aim,
        targets=targets,
        max_questions=per_message,
        give_before_ask=settings.discovery.budget.give_before_ask,
    )


def _first_gap(
    statuses: Mapping[str, StepStatus],
    fields: Mapping[str, DiscoveryField],
    advance: Sequence[str] = (),
) -> tuple[str | None, list[str]]:
    """The first next step that lacks something worth asking for, and what would fill the gap."""
    for step in LADDER:
        status = statuses.get(step)
        if status is None or status.satisfied:
            continue
        alternatives = sorted(
            status.missing_any, key=lambda key: _importance(fields.get(key))
        )  # stable: declared order among equals
        keys = [*status.missing_all, *alternatives]
        if step == "propose_next_step":
            # What a proposal lacks is the prospect's interest, which is not asked about: the
            # proposal itself invites it. Only real fields are worth a question here.
            keys = [key for key in keys if key != INTEREST_KEY]
        if keys:
            return step, keys
    if advance:
        return ADVANCE, list(advance)
    return None, []


def _importance(field: DiscoveryField | None) -> int:
    return _IMPORTANCE[field.importance] if field is not None else _IMPORTANCE["critical"]


def _target(
    key: str,
    fields: Mapping[str, DiscoveryField],
    facts: KnownFacts,
    language: str,
    now: datetime,
) -> DiscoveryTarget:
    field = fields.get(key)
    if field is None:  # `interest`: which offer suits the prospect
        return DiscoveryTarget(key=key, importance="critical", changes=("product_selection",))
    guess = facts.get(key)
    hints_by_language = field.ask_hints
    matched = match_language(language, hints_by_language)
    stale = facts.settled(key) is not None and not _is_known(field, facts, now)
    return DiscoveryTarget(
        key=key,
        label=field.label,
        importance=field.importance,
        changes=tuple(field.changes),
        hints=tuple(hints_by_language[matched]) if matched else (),
        hypothesis=guess.value if guess is not None and not guess.settled else None,
        recheck=stale,
    )
