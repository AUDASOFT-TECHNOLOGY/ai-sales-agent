"""Audit event types written by the Engine. The Core owns this vocabulary."""

ACTION_PLANNED = "action.planned"
ACTION_PROPOSED = "action.proposed"
ACTION_APPROVED = "action.approved"  # automatic (autonomous mode) or by a person: see `actor`
ACTION_REJECTED = "action.rejected"  # by a person: see `actor`
ACTION_COMPLETED = "action.completed"
ACTION_CANCELLED = "action.cancelled"
ACTION_SENT = "action.sent"
ACTION_SEND_FAILED = "action.send_failed"
ACTION_SEND_UNCONFIRMED = "action.send_unconfirmed"  # stuck in SENDING: a human must reconcile
ACTION_ERROR = "action.error"
ACTION_BOOKKEEPING_FAILED = "action.bookkeeping_failed"  # sent, but a follow-up write failed

BRAIN_DECISION = "brain.decision"
SAFETY_BLOCKED = "safety.blocked"

FACT_PROPOSAL_ACCEPTED = "fact.proposal_accepted"  # a Brain proposal the Core recorded
FACT_PROPOSAL_REJECTED = "fact.proposal_rejected"  # one it refused; `reason` says why

PROSPECT_OPTED_OUT = "prospect.opted_out"
PROSPECT_PAUSED = "prospect.paused"
PROSPECT_HANDOFF = "prospect.handoff"
PROSPECT_CLOSED = "prospect.closed"
PROSPECT_CREATED = "prospect.created"  # an unknown sender the client chose to engage
PROSPECT_LANE_ASSIGNED = "prospect.lane_assigned"  # written once: the lane a prospect belongs to
PROSPECT_LINKED = "prospect.linked"  # a prospect started the conversation on an opt-in channel

INBOUND_RECEIVED = "inbound.received"
INBOUND_DUPLICATE = "inbound.duplicate"
INBOUND_UNKNOWN_SENDER = "inbound.unknown_sender"
INBOUND_LINK_REFUSED = "inbound.link_refused"  # a claim on a prospect that could not be honoured
INBOUND_ERROR = "inbound.error"

DRYRUN_DECISION = "dryrun.decision"
DRYRUN_INBOUND = "dryrun.inbound"
