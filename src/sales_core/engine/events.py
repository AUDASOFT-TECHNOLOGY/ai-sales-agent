"""Audit event types written by the Engine. The Core owns this vocabulary."""

ACTION_PLANNED = "action.planned"
ACTION_PROPOSED = "action.proposed"
ACTION_APPROVED = "action.approved"  # automatic approval (autonomous mode)
ACTION_COMPLETED = "action.completed"
ACTION_CANCELLED = "action.cancelled"
ACTION_SENT = "action.sent"
ACTION_SEND_FAILED = "action.send_failed"
ACTION_SEND_UNCONFIRMED = "action.send_unconfirmed"  # stuck in SENDING: a human must reconcile
ACTION_ERROR = "action.error"
ACTION_BOOKKEEPING_FAILED = "action.bookkeeping_failed"  # sent, but a follow-up write failed

BRAIN_DECISION = "brain.decision"
SAFETY_BLOCKED = "safety.blocked"

PROSPECT_OPTED_OUT = "prospect.opted_out"
PROSPECT_PAUSED = "prospect.paused"
PROSPECT_HANDOFF = "prospect.handoff"
PROSPECT_CLOSED = "prospect.closed"

INBOUND_RECEIVED = "inbound.received"
INBOUND_DUPLICATE = "inbound.duplicate"
INBOUND_UNKNOWN_SENDER = "inbound.unknown_sender"
INBOUND_ERROR = "inbound.error"

DRYRUN_DECISION = "dryrun.decision"
DRYRUN_INBOUND = "dryrun.inbound"
