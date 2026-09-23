"""Pure safety checks. They can only block; they never produce or change content.

The Engine calls them before asking the Brain and again immediately before sending, on freshly
read state. Any doubt blocks (fail closed).
"""

import string
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from sales_core.contracts import Channel, Prospect, ProspectStatus, SalesAction

# "stop" is the near-universal opt-out word. Clients add their own (language-specific) words
# through configuration; the Core hard-codes no language.
DEFAULT_OPT_OUT_KEYWORDS = frozenset({"stop"})

_CONTACTABLE_STATUSES = frozenset({ProspectStatus.NEW, ProspectStatus.ACTIVE})


class BlockReason(StrEnum):
    UNKNOWN_PROSPECT = "unknown_prospect"
    OPTED_OUT = "opted_out"
    PAUSED = "paused"  # requires human handling
    CLOSED = "closed"
    NO_CONTACT = "no_contact"  # no address for the channel
    NO_DRAFT = "no_draft"
    CLIENT_MISMATCH = "client_mismatch"


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    reason: BlockReason | None = None

    @property
    def allowed(self) -> bool:
        return self.reason is None


ALLOWED = SafetyVerdict()


def recipient_for(prospect: Prospect, channel: Channel) -> str | None:
    """The prospect's address on `channel`, if we have one."""
    address = prospect.contact.email if channel is Channel.EMAIL else prospect.contact.phone
    return address if address and address.strip() else None


def check_contactable(prospect: Prospect | None, channel: Channel) -> SafetyVerdict:
    """May this prospect be contacted at all on `channel`?"""
    if prospect is None:
        return SafetyVerdict(BlockReason.UNKNOWN_PROSPECT)
    if prospect.status is ProspectStatus.OPTED_OUT:
        return SafetyVerdict(BlockReason.OPTED_OUT)
    if prospect.status is ProspectStatus.PAUSED:
        return SafetyVerdict(BlockReason.PAUSED)
    if prospect.status is ProspectStatus.CLOSED:
        return SafetyVerdict(BlockReason.CLOSED)
    if prospect.status not in _CONTACTABLE_STATUSES:  # future statuses are blocked by default
        return SafetyVerdict(BlockReason.PAUSED)
    if recipient_for(prospect, channel) is None:
        return SafetyVerdict(BlockReason.NO_CONTACT)
    return ALLOWED


def check_ready_to_send(
    prospect: Prospect | None, action: SalesAction, channel: Channel
) -> SafetyVerdict:
    """Final gate, run on fresh prospect state right before the channel call."""
    verdict = check_contactable(prospect, channel)
    if not verdict.allowed:
        return verdict
    if prospect is not None and prospect.client_id != action.client_id:
        return SafetyVerdict(BlockReason.CLIENT_MISMATCH)
    if action.draft is None:
        return SafetyVerdict(BlockReason.NO_DRAFT)
    return ALLOWED


def is_opt_out_message(text: str, keywords: Collection[str]) -> bool:
    """True if the whole message is an opt-out keyword (ignoring case, spaces, punctuation).

    Whole-message matching on purpose: "please don't stop by" must not unsubscribe anyone.
    """
    cleaned = text.strip().strip(string.punctuation + string.whitespace).casefold()
    return bool(cleaned) and cleaned in {k.strip().casefold() for k in keywords}
