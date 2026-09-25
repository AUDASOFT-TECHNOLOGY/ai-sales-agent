"""The Core's decision on facts a Brain proposes: which become observations, and why the rest do not.

Pure: no I/O, no clock. A proposal says what a Brain thinks it learned; this module checks that it
may be recorded and, if so, builds the `Fact` with everything the Brain must not decide: the
provenance is limited to what a Brain can honestly claim, evidence points at a stored message (its
time comes from that message, never from the Brain), and the record's time and author are given
by the caller so the same result processed twice yields the same identity.

Nothing is repaired: a proposal is accepted as it is or refused with a reason. Accepting one never
changes an earlier fact; history is append-only and nothing here promotes a status.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sales_core.contracts import (
    CORE_FACT_KEY_PREFIX,
    UNVERIFIED_CLAIM_KEYS,
    ClientId,
    Conversation,
    Direction,
    Fact,
    FactEvidence,
    FactProposal,
    FactSource,
    FactStatus,
    Message,
    MessageId,
    Prospect,
)

MAX_KEY_LENGTH = 128
MAX_VALUE_LENGTH = 2000

# What a Brain may claim as the origin of a fact, and the statuses each origin can carry. A Brain
# cannot claim the client, the system or a human as its source: those are recorded by others.
# An inference is only ever a hypothesis, so it cannot pass as known (the kit treats a discovered
# fact as settled) and can never be confirmed.
_ALLOWED: dict[FactSource, frozenset[FactStatus]] = {
    FactSource.PROSPECT: frozenset({FactStatus.CONFIRMED, FactStatus.DISCOVERED}),
    FactSource.CONVERSATION: frozenset({FactStatus.DISCOVERED, FactStatus.HYPOTHESIS}),
    FactSource.BRAIN_INFERENCE: frozenset({FactStatus.HYPOTHESIS}),
}
# Facts about what the prospect said or sent (see `UNVERIFIED_CLAIM_KEYS`): from the prospect's own
# message, and discovered at most.
_CLAIM_SOURCES = frozenset({FactSource.PROSPECT, FactSource.CONVERSATION})
_CLAIM_STATUSES = frozenset({FactStatus.DISCOVERED})
# Sources a Brain cannot claim: a settled fact from one of them is not displaced by a Brain.
_AUTHORITATIVE = frozenset({FactSource.SYSTEM, FactSource.HUMAN, FactSource.CLIENT_DATA})


class ProposalRejection(StrEnum):
    WRONG_CLIENT = "wrong_client"  # prospect or conversation is not this client's
    MALFORMED = "malformed"  # empty or oversized key or value, or not a valid fact
    RESERVED_KEY = "reserved_key"  # a key the Core alone writes
    SOURCE_NOT_ALLOWED = "source_not_allowed"  # a Brain cannot claim that origin
    STATUS_NOT_ALLOWED = "status_not_allowed"  # that origin cannot carry that status
    MESSAGE_REQUIRED = "message_required"  # a statement by the prospect needs its message
    UNKNOWN_MESSAGE = "unknown_message"  # not in this prospect's conversation
    NOT_A_PROSPECT_MESSAGE = "not_a_prospect_message"  # the prospect did not write it
    EXCERPT_NOT_IN_MESSAGE = "excerpt_not_in_message"  # the words are not in the message
    CONFLICTS_WITH_AUTHORITATIVE_FACT = "conflicts_with_authoritative_fact"
    NO_WRITER = "no_writer"  # nothing to record facts with (raised by the caller, not here)


@dataclass(frozen=True, slots=True, kw_only=True)
class RejectedProposal:
    proposal: FactProposal
    reason: ProposalRejection


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalReview:
    accepted: tuple[Fact, ...] = ()
    rejected: tuple[RejectedProposal, ...] = ()


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def review_proposals(
    proposals: Sequence[FactProposal],
    *,
    client_id: ClientId,
    prospect: Prospect,
    conversation: Conversation,
    existing: Sequence[Fact],
    recorded_at: datetime,
    recorded_by: str,
) -> ProposalReview:
    """Accept or refuse each proposal for `prospect`. `existing` is what is already recorded,
    oldest first. `recorded_at` and `recorded_by` are the Core's: they date and sign the record,
    and must be the same every time the same Brain result is processed."""
    if (
        prospect.client_id != client_id
        or conversation.client_id != client_id
        or conversation.prospect_id != prospect.prospect_id
    ):
        return ProposalReview(
            rejected=tuple(
                RejectedProposal(proposal=p, reason=ProposalRejection.WRONG_CLIENT)
                for p in proposals
            )
        )
    messages = {m.message_id: m for m in conversation.messages}
    current: dict[str, Fact] = {}
    for fact in existing:
        current[fact.key] = fact

    accepted: list[Fact] = []
    rejected: list[RejectedProposal] = []
    for proposal in proposals:
        outcome = _review_one(
            proposal, messages, current.get(proposal.key), recorded_at, recorded_by
        )
        if isinstance(outcome, ProposalRejection):
            rejected.append(RejectedProposal(proposal=proposal, reason=outcome))
        elif outcome not in accepted:  # the same proposal twice in one result counts once
            accepted.append(outcome)
    return ProposalReview(accepted=tuple(accepted), rejected=tuple(rejected))


def _review_one(
    proposal: FactProposal,
    messages: dict[MessageId, Message],
    latest: Fact | None,
    recorded_at: datetime,
    recorded_by: str,
) -> Fact | ProposalRejection:
    if len(proposal.key) > MAX_KEY_LENGTH or len(proposal.value) > MAX_VALUE_LENGTH:
        return ProposalRejection.MALFORMED
    if proposal.key.startswith(CORE_FACT_KEY_PREFIX):
        return ProposalRejection.RESERVED_KEY
    allowed = _ALLOWED.get(proposal.source)
    claim = proposal.key in UNVERIFIED_CLAIM_KEYS
    if claim:
        # What the prospect said or sent is only ever recorded as discovered, never verified.
        allowed = _CLAIM_STATUSES if proposal.source in _CLAIM_SOURCES else None
    if allowed is None:
        return ProposalRejection.SOURCE_NOT_ALLOWED
    if proposal.status not in allowed:
        return ProposalRejection.STATUS_NOT_ALLOWED

    message = None
    if proposal.message_id is not None:
        message = messages.get(proposal.message_id)
        if message is None:
            return ProposalRejection.UNKNOWN_MESSAGE
        if (claim or proposal.source is FactSource.PROSPECT) and (
            message.direction is not Direction.INBOUND
        ):
            return ProposalRejection.NOT_A_PROSPECT_MESSAGE
        if proposal.excerpt is not None and _squash(proposal.excerpt) not in _squash(message.text):
            return ProposalRejection.EXCERPT_NOT_IN_MESSAGE
    elif claim or proposal.source is FactSource.PROSPECT:
        return ProposalRejection.MESSAGE_REQUIRED

    if (
        latest is not None
        and latest.is_confirmed
        and latest.evidence is not None
        and latest.evidence.source in _AUTHORITATIVE
        and latest.value != proposal.value
    ):
        return ProposalRejection.CONFLICTS_WITH_AUTHORITATIVE_FACT

    try:
        return Fact(
            key=proposal.key,
            value=proposal.value,
            status=proposal.status,
            confidence=proposal.confidence,
            evidence=FactEvidence(
                source=proposal.source,
                message_id=proposal.message_id,
                excerpt=proposal.excerpt,
                observed_at=message.timestamp if message is not None else None,
                recorded_by=recorded_by,
            ),
            recorded_at=recorded_at,
        )
    except ValueError:
        return ProposalRejection.MALFORMED
