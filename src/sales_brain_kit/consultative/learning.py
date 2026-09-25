"""From what the model says it learned to the facts the Brain proposes to the Core.

The model only *reports*: a declared key, a value, how sure it is (`stated`, `implied`, `guess`)
and, unless it is a guess, the prospect's exact words. The code decides everything else. A report
becomes a `FactProposal` only if:

- the key is one the client declared (or `interest`, whose value must name a catalog product), and
  never one the Core or a payment claim owns;
- the words quoted really appear in a message the prospect wrote since our last one, and that
  message is the one the proposal is anchored to (a guess is anchored to the latest);
- the provenance follows from the basis, chosen here and never by the model:

      stated   the prospect said it        -> source prospect,       status confirmed
      implied  read from their words       -> source conversation,   status discovered
      guess    the model's own inference   -> source brain_inference, status hypothesis

  A guess needs a confidence, is refused where the client forbids guessing the field, and is never
  made about a sensitive or protected attribute.

Nothing here writes anything: the proposals go to the Core in the decision, which validates them
again (`sales_core.facts.review_proposals`) and is the only one that records facts. What is already
known is not proposed again. The model cannot promote a fact: a hypothesis stays one.

Only a reply is a moment of learning: without a message from the prospect there is nothing to
quote, so nothing is proposed. What the code cannot check is that the value says what the quoted
words mean (only `interest` is matched against the catalog); the excerpt and the Core's own rules
bound the damage, and a person can always read the words the fact rests on.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sales_brain_kit.consultative.context import CommercialContext
from sales_brain_kit.consultative.discovery import is_reserved_fact_key, learnable_fields
from sales_brain_kit.consultative.facts import INTEREST_KEY
from sales_brain_kit.consultative.interest import InterestLevel
from sales_brain_kit.consultative.output import BrainOutput, LearnedFact
from sales_brain_kit.settings.discovery import DiscoveryField
from sales_brain_kit.settings.strategy import PROTECTED_ATTRIBUTES
from sales_core.contracts import (
    Direction,
    FactProposal,
    FactSource,
    FactStatus,
    Message,
    SalesActionType,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Provenance:
    source: FactSource
    status: FactStatus


# The only provenance a report can have. The model picks a basis; it never picks a source or status.
_PROVENANCE = {
    "stated": _Provenance(source=FactSource.PROSPECT, status=FactStatus.CONFIRMED),
    "implied": _Provenance(source=FactSource.CONVERSATION, status=FactStatus.DISCOVERED),
    "guess": _Provenance(source=FactSource.BRAIN_INFERENCE, status=FactStatus.HYPOTHESIS),
}
_STRENGTH = {FactStatus.HYPOTHESIS: 0, FactStatus.DISCOVERED: 1, FactStatus.CONFIRMED: 2}
_INTEREST_LEVEL = {
    FactStatus.CONFIRMED: InterestLevel.CONFIRMED,
    FactStatus.DISCOVERED: InterestLevel.PROBABLE,
    FactStatus.HYPOTHESIS: InterestLevel.POSSIBLE,
}


def prospect_messages(context: CommercialContext) -> tuple[Message, ...]:
    """What the prospect wrote since our last message, oldest first, when we are replying to it.
    These are the only messages a fact can be learned from and anchored to."""
    if context.request.action.action_type is not SalesActionType.REPLY:
        return ()
    run: list[Message] = []
    for message in reversed(context.history.messages):
        if message.direction is not Direction.INBOUND:
            break
        run.append(message)
    return tuple(reversed(run))


def check_learned(
    output: BrainOutput, context: CommercialContext, problems: list[str]
) -> tuple[FactProposal, ...]:
    """The proposals for what the model reports, appending a problem for each report refused."""
    if not output.learned_facts:
        return ()
    messages = prospect_messages(context)
    if not messages:
        problems.append("learned_facts: there is no message from the prospect to learn from now")
        return ()
    proposals: list[FactProposal] = []
    seen: dict[str, str] = {}
    for index, report in enumerate(output.learned_facts):
        where = f"learned_facts[{index}]"
        proposal = _proposal(report, where, messages, context, problems)
        if proposal is None:
            continue
        identity = (
            proposal.key if proposal.key != INTEREST_KEY else f"{INTEREST_KEY}:{proposal.value}"
        )
        earlier = seen.get(identity)
        if earlier is None:
            seen[identity] = _squash(proposal.value)
            proposals.append(proposal)
        elif earlier != _squash(proposal.value):
            problems.append(f"{where}: {proposal.key!r} is reported with two different values")
    return tuple(proposals)


def _proposal(
    report: LearnedFact,
    where: str,
    messages: Sequence[Message],
    context: CommercialContext,
    problems: list[str],
) -> FactProposal | None:
    key = report.key
    if is_reserved_fact_key(key):
        problems.append(
            f"{where}.key: {key!r} is not yours to report (the Core records it; a payment the "
            "prospect claims is reported in analysis.payment_claimed)"
        )
        return None
    fields = learnable_fields(context.settings)
    if key not in fields:
        problems.append(
            f"{where}.key: {key!r} is not a declared field (use one of the keys listed)"
        )
        return None
    field = fields[key]
    value = " ".join(report.value.split())
    if not value:
        problems.append(f"{where}.value: is empty")
        return None
    if key == INTEREST_KEY:
        product = context.catalog.resolve(value)
        if product is None:
            problems.append(f"{where}.value: {value!r} is not a product of the catalog")
            return None
        value = product.id

    provenance = _PROVENANCE[report.basis]
    if report.basis == "guess":
        if not _may_guess(key, field, report, where, context, problems):
            return None
        anchor, excerpt = messages[-1], None
    else:
        found = _quoted(report, where, messages, problems)
        if found is None:
            return None
        anchor, excerpt = found

    if _already_known(key, value, provenance.status, context):
        return None
    return FactProposal(
        key=key,
        value=value,
        status=provenance.status,
        source=provenance.source,
        confidence=report.confidence if report.basis == "guess" else None,
        message_id=anchor.message_id,
        excerpt=excerpt,
    )


def _may_guess(
    key: str,
    field: DiscoveryField | None,
    report: LearnedFact,
    where: str,
    context: CommercialContext,
    problems: list[str],
) -> bool:
    protected = set(PROTECTED_ATTRIBUTES) | set(
        context.settings.adaptation.extra_protected_attributes
    )
    allowed = field is None or field.infer.allowed
    sensitive = (field is not None and field.sensitive) or key in protected
    if not allowed or sensitive:
        problems.append(f"{where}.basis: {key!r} may not be guessed, only stated by the prospect")
        return False
    if report.confidence is None:
        problems.append(f"{where}.confidence: a guess needs a confidence between 0 and 1")
        return False
    # A guess too weak to be used is not worth recording: dropped, not an error.
    return report.confidence >= context.settings.discovery.hypotheses.min_confidence_to_use


def _quoted(
    report: LearnedFact, where: str, messages: Sequence[Message], problems: list[str]
) -> tuple[Message, str] | None:
    """The prospect's message that contains the quoted words (the latest, if several do)."""
    excerpt = " ".join((report.excerpt or "").split())
    if not excerpt:
        problems.append(
            f"{where}.excerpt: a {report.basis!r} fact needs the prospect's exact words; "
            "without them it is only a guess"
        )
        return None
    wanted = _squash(excerpt)
    for message in reversed(messages):
        if wanted in _squash(message.text):
            return message, excerpt
    problems.append(
        f"{where}.excerpt: {excerpt!r} is not in what the prospect wrote (copy their words exactly)"
    )
    return None


def _already_known(key: str, value: str, status: FactStatus, context: CommercialContext) -> bool:
    """Whether something at least as firm is already recorded for this key and value: proposing it
    again would add a duplicate observation and nothing else. A firmer statement is still new."""
    if key == INTEREST_KEY:
        wanted = _INTEREST_LEVEL[status]
        for interest in context.selection.interests:
            if interest.product_id == value and interest.level.at_least(wanted):
                return True
        for fact in context.request.facts:
            product = context.catalog.resolve(fact.value) if fact.key == INTEREST_KEY else None
            if (
                product is not None
                and product.id == value
                and _STRENGTH[fact.status] >= _STRENGTH[status]
            ):
                return True
        return False

    firm = context.facts.settled(key)
    if status is FactStatus.HYPOTHESIS and firm is not None:
        return True  # a guess never competes with what is known
    candidates = [(fact.value, fact.status) for fact in context.request.facts if fact.key == key]
    if firm is not None:
        candidates.append((firm.value, firm.status))
    return any(
        _squash(known) == _squash(value) and _STRENGTH[known_status] >= _STRENGTH[status]
        for known, known_status in candidates
    )


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()
