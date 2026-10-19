"""Pure safety checks. They can only block; they never produce or change content.

The Engine calls them before asking the Brain and again immediately before sending, on freshly
read state. Any doubt blocks (fail closed).
"""

import string
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum

from sales_core.contracts import (
    Channel,
    Direction,
    Message,
    Prospect,
    ProspectStatus,
    SalesAction,
)

# "stop" is the near-universal opt-out word. Clients add their own (language-specific) words
# through configuration; the Core hard-codes no language.
DEFAULT_OPT_OUT_KEYWORDS = frozenset({"stop"})

_CONTACTABLE_STATUSES = frozenset({ProspectStatus.NEW, ProspectStatus.ACTIVE})

# Channels where the person must open the conversation before anyone may write to them (a Telegram
# bot cannot message a user who never started it). Having no address there is not "no contact
# details": it is "has not said hello yet", and the prospect is waiting, not lost.
OPT_IN_CHANNELS = frozenset({Channel.TELEGRAM})


class BlockReason(StrEnum):
    UNKNOWN_PROSPECT = "unknown_prospect"
    OPTED_OUT = "opted_out"
    PAUSED = "paused"  # requires human handling
    CLOSED = "closed"
    NO_CONTACT = "no_contact"  # no address for the channel
    NOT_LINKED = "not_linked"  # the channel needs the person to start the conversation first
    NO_LANE = "no_lane"  # the prospect belongs to no lane the client runs, so no channel is known
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
    match channel:
        case Channel.EMAIL:
            address = prospect.contact.email
        case Channel.TELEGRAM:
            address = prospect.contact.telegram_chat_id
        case _:
            address = prospect.contact.phone
    return address if address and address.strip() else None


def awaits_opt_in(prospect: Prospect, channel: Channel) -> bool:
    """True if the prospect cannot be written to on `channel` only because they have not started
    the conversation there yet."""
    return channel in OPT_IN_CHANNELS and recipient_for(prospect, channel) is None


# What a client writes in its consent column to say "this person expects to hear from us here".
# Anything else (empty, "no", a date, a question mark) is not consent: any doubt blocks.
_CONSENT_WORDS = frozenset({"yes", "y", "true", "1", "ok", "oui", "o", "نعم", "x", "✓"})


def has_stated_consent(prospect: Prospect, attribute: str | None) -> bool:
    """True if the client recorded, in the source column `attribute`, that this person expects to be
    contacted. It is the client's own statement, not something Revansa can verify."""
    if not attribute:
        return False
    return (prospect.attributes.get(attribute) or "").strip().casefold() in _CONSENT_WORDS


def has_initiated(messages: Iterable[Message], channel: Channel, address: str) -> bool:
    """True if this person wrote to us first, from `address`, on `channel`.

    The proof is an inbound message the Engine stored after the channel adapter authenticated it
    (a signed webhook): its `external_id` starts with the sender's address, which is how the
    Telegram adapter names a message (`<chat id>:<message id>`). An address that merely sits on the
    prospect, whatever its origin (a spreadsheet column, a CRM field, an import), is not proof.
    """
    prefix = f"{address.strip()}:"
    return any(
        m.direction is Direction.INBOUND
        and m.channel is channel
        and (m.external_id or "").startswith(prefix)
        for m in messages
    )


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
        return SafetyVerdict(
            BlockReason.NOT_LINKED if channel in OPT_IN_CHANNELS else BlockReason.NO_CONTACT
        )
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
