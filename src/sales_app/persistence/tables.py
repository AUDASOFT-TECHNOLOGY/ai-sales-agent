"""The schema, as SQLAlchemy Core tables. The Alembic migrations are the source of truth for what
a deployed database looks like; these definitions are what the stores query with.

Account and configuration data, and (the `op_*` tables) a client's operational data when its
prospects live in another system. Credentials are kept only as ciphertext (`secrets`, see
`sales_app.secret_store`): the key that opens them is never in the database.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """Always timezone-aware UTC in Python, whatever the database keeps (SQLite drops the zone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


metadata = MetaData()

clients = Table(
    "clients",
    metadata,
    Column("client_id", String(64), primary_key=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255)),
    # Identity, status and engine (migration 0015). NULL: not migrated, `client.yaml` decides.
    Column("display_name", String(80)),
    Column("enabled", Boolean),
    Column("engine", String(64)),
    Column("updated_at", UtcDateTime),
    Column("updated_by", String(255)),
)

client_profiles = Table(
    "client_profiles",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("login", String(64), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("must_change_password", Boolean, nullable=False),
    Column("logo", Text),
    Column("whatsapp_number", String(40)),
    # The number's digits only: what makes "+216 22 768 307" and "21622768307" the same number.
    Column("whatsapp_digits", String(20), unique=True),
    Column("whatsapp_reconnect_required", Boolean, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255)),
    Column("updated_at", UtcDateTime),
)

# The Client's own switch for its agent (run or pause). Not the Admin's `enabled`, which stays in
# `client.yaml`: the two are never one value.
client_agent_state = Table(
    "client_agent_state",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("running", Boolean, nullable=False),
    Column("changed_at", UtcDateTime, nullable=False),
    Column("changed_by", String(255), nullable=False),
)

# The last real test of a client's prospect source, with a fingerprint of what it was made for: a
# result counts only while the source and its connection parameters are still those.
client_source_check = Table(
    "client_source_check",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("ok", Boolean, nullable=False),
    Column("checked_at", UtcDateTime, nullable=False),
)

client_sessions = Table(
    "client_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column(
        "client_id", String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False
    ),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Index("ix_client_sessions_client_id", "client_id"),
    Index("ix_client_sessions_expires_at", "expires_at"),
)

admin_users = Table(
    "admin_users",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("username", String(255), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("role", String(16), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255)),
)

admin_user_clients = Table(
    "admin_user_clients",
    metadata,
    Column(
        "admin_id", String(255), ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
)

admin_sessions = Table(
    "admin_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column(
        "admin_id", String(255), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    ),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Index("ix_admin_sessions_admin_id", "admin_id"),
    Index("ix_admin_sessions_expires_at", "expires_at"),
)

config_versions = Table(
    "config_versions",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("parent", Integer),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("checksum", String(64), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("author", String(255), nullable=False),
    Column("note", Text, nullable=False),
)

# ---- a client's operational data (see `sales_app.persistence.operational_rows`) ------------------
#
# What Revansa does for one client when its prospects live elsewhere: actions, messages, the audit
# trail, facts, and its own state for each prospect. Every row carries `client_id` (a foreign key to
# `clients`) and every query filters on it. The full record is kept as a JSON `document`; the other
# columns are only what is queried, ordered or kept unique. No credential is ever stored here.

op_actions = Table(
    "op_actions",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("action_id", String(128), primary_key=True),
    Column("idempotency_key", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    # Denormalised from the document (migration 0022), so errors and counts are read by SQL.
    Column("prospect_id", String(255)),
    Column("action_type", String(32)),
    Column("due_at", UtcDateTime),
    UniqueConstraint("client_id", "idempotency_key", name="uq_op_actions_idempotency_key"),
    Index("ix_op_actions_status", "client_id", "status"),
    Index("ix_op_actions_prospect", "client_id", "prospect_id"),
    Index("ix_op_actions_status_due", "client_id", "status", "due_at"),
)

op_messages = Table(
    "op_messages",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # append order
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("message_id", String(128), nullable=False),
    Column("prospect_id", String(255), nullable=False),
    Column("conversation_id", String(255), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    # Denormalised from the document (migration 0022): a conversation is paged and counted by SQL.
    Column("direction", String(16)),
    Column("channel", String(16)),
    Column("sent_at", UtcDateTime),
    UniqueConstraint("client_id", "message_id", name="uq_op_messages_message_id"),
    Index("ix_op_messages_prospect", "client_id", "prospect_id"),
    Index("ix_op_messages_prospect_sent", "client_id", "prospect_id", "sent_at"),
    Index("ix_op_messages_sent", "client_id", "sent_at"),
)

op_audit = Table(
    "op_audit",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("occurred_at", UtcDateTime, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("prospect_id", String(255)),
    Column("action_id", String(128)),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Index("ix_op_audit_prospect", "client_id", "prospect_id"),
)

op_facts = Table(
    "op_facts",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("fact_id", String(64), nullable=False),  # `fact_identity`: a retry is ignored
    Column("prospect_id", String(255), nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("fact_key", String(128)),  # denormalised from the document (migration 0022)
    UniqueConstraint("client_id", "fact_id", name="uq_op_facts_fact_id"),
    Index("ix_op_facts_prospect", "client_id", "prospect_id"),
    Index("ix_op_facts_key", "client_id", "fact_key"),
)

op_prospect_states = Table(
    "op_prospect_states",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("prospect_id", String(255), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("conversation_id", String(255)),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
)

# The read model of a client's prospects (migration 0022): one row per prospect the platform has
# seen, with what a list needs to filter, search, sort and page by SQL, whoever owns the truth (the
# source for who the person is, `op_prospect_states` for where Revansa stands). It is a projection,
# never the truth: it is refreshed from the repository's merged view and can be rebuilt at any time.
# `external_id` is the id in the lane's source (the public id without its source prefix), so the
# database itself refuses to hold one person twice for the same (client, lane, external id).
op_prospect_index = Table(
    "op_prospect_index",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("prospect_id", String(255), primary_key=True),
    Column("external_id", String(255)),
    Column("lane_id", String(128)),
    Column("source_id", String(128)),
    Column("display_name", String(255), nullable=False, server_default=""),
    Column("email", String(320)),
    Column("phone", String(64)),
    Column("search_text", Text, nullable=False, server_default=""),  # lower-cased name/contacts
    Column("status", String(32), nullable=False),
    Column("stage", String(255)),
    Column("locale", String(32)),
    Column("channel", String(16)),  # the channel the person is written to
    Column("next_action_type", String(32)),
    Column("next_action_at", UtcDateTime),
    Column("last_contacted_at", UtcDateTime),
    Column("last_inbound_at", UtcDateTime),
    Column("last_activity_at", UtcDateTime),
    Column("follow_up_count", Integer, nullable=False, server_default="0"),
    Column("needs_human", Boolean, nullable=False, server_default=false()),
    Column("status_reason", Text),
    Column("conversation_id", String(255)),
    Column("indexed_at", UtcDateTime, nullable=False),
    UniqueConstraint("client_id", "lane_id", "external_id", name="uq_op_prospect_index_external"),
    Index("ix_op_prospect_index_status", "client_id", "status"),
    Index("ix_op_prospect_index_lane", "client_id", "lane_id"),
    Index("ix_op_prospect_index_channel", "client_id", "channel"),
    Index("ix_op_prospect_index_next", "client_id", "next_action_at"),
    Index("ix_op_prospect_index_activity", "client_id", "last_activity_at"),
    Index("ix_op_prospect_index_name", "client_id", "display_name"),
)

op_local_prospects = Table(
    "op_local_prospects",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("prospect_id", String(255), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    UniqueConstraint("client_id", "prospect_id", name="uq_op_local_prospects_prospect_id"),
)

# One row per comparable contact key of a local prospect: the database itself refuses two local
# prospects that share a phone number or an address.
op_local_contacts = Table(
    "op_local_contacts",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("contact_key", String(320), primary_key=True),
    Column("prospect_id", String(255), nullable=False),
)

# The write-back ledger: what Revansa tried to write into a client's source, and how each try ended.
# A journal, not the truth: what to write is recomputed from the source and from `op_prospect_states`.
# `sequence` is the client's own count (1, 2, 3, ...), so it is part of the key and not a global
# counter; a retry of an attempt (same `attempt_id`) is recorded once. It holds a prospect's public
# id, a field name and two short canonical values (a status, a date, a count): never a name, a
# contact or anything the source said.
op_writeback_ledger = Table(
    "op_writeback_ledger",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("sequence", Integer, primary_key=True, autoincrement=False),
    Column("attempt_id", String(128), nullable=False),
    Column("prospect_id", String(255), nullable=False),
    Column("field", String(32), nullable=False),
    Column("new_value", String(255), nullable=False),
    Column("expected_value", String(255), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("code", String(16)),
    Column("at", UtcDateTime, nullable=False),
    Column("not_before", UtcDateTime),
    UniqueConstraint("client_id", "attempt_id", name="uq_op_writeback_ledger_attempt"),
    Index("ix_op_writeback_ledger_prospect", "client_id", "prospect_id", "field", "sequence"),
)

# The LLM usage register: one row per model call of a client's agent. Counts only, never a prompt or
# an answer. A token count the provider did not report stays NULL: an unknown is not a zero.
op_llm_usage = Table(
    "op_llm_usage",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("occurred_at", UtcDateTime, nullable=False),
    Column("provider", String(64), nullable=False),
    Column("model", String(128), nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("status", String(16), nullable=False),
    Index("ix_op_llm_usage_client", "client_id", "occurred_at"),
)

# What a client's agent is doing right now, step by step (kept two days). A step holds a kind, the
# display name of the person it is about and its times: never a message, an address or a credential.
op_agent_activity = Table(
    "op_agent_activity",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("state", String(16), nullable=False),
    Column("started_at", UtcDateTime, nullable=False),
    Column("ended_at", UtcDateTime),
    Column("prospect_id", String(255)),
    Column("name", String(120)),
    Column("detail", String(200)),
    Index("ix_op_agent_activity_client", "client_id", "seq"),
    Index("ix_op_agent_activity_started", "client_id", "started_at"),
)

# What an agent keeps to learn from its results. Anonymous throughout: a person is an opaque
# reference, a message a length, a rule a code. Traces are deleted once their conversation is reviewed.
op_learning_traces = Table(
    "op_learning_traces",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("case_ref", String(32), nullable=False),
    Column("action_ref", String(32), nullable=False),
    Column("at", UtcDateTime, nullable=False),
    Column("decision", String(32), nullable=False),
    Column("action_type", String(32), nullable=False),
    Column("channel", String(16)),
    Column("length", Integer),
    Column("questions", Integer),
    Column("gap_hours", Numeric(10, 1)),
    Column("unanswered", Integer, nullable=False, server_default="0"),
    Column("answers_reply", Boolean, nullable=False, server_default=false()),
    Column("labels", Text, nullable=False, server_default="{}"),
    UniqueConstraint("client_id", "action_ref", name="uq_op_learning_traces_action"),
    Index("ix_op_learning_traces_case", "client_id", "case_ref"),
)

op_learning_reviews = Table(
    "op_learning_reviews",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("case_ref", String(32), primary_key=True),
    Column("result", String(16), nullable=False),
    Column("ended_at", UtcDateTime, nullable=False),
    Column("reviewed_at", UtcDateTime, nullable=False),
    Column("causes", Text, nullable=False, server_default=""),
    Column("practices", Text, nullable=False, server_default=""),
    Column("confidence", String(8), nullable=False),
    Column("turning_step", Integer),
    Column("unknown", Boolean, nullable=False, server_default=false()),
    Column("product", String(64)),
    Column("applied_notes", Text, nullable=False, server_default=""),
    Column("steps_seen", Integer, nullable=False, server_default="0"),
    Index("ix_op_learning_reviews_ended", "client_id", "ended_at"),
)

op_learning_notes = Table(
    "op_learning_notes",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("note_id", String(96), primary_key=True),
    Column("code", String(48), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("theme", String(32), nullable=False),
    Column("state", String(12), nullable=False),
    Column("product", String(64)),
    Column("evidence", String(8), nullable=False),
    Column("cases", Integer, nullable=False, server_default="0"),
    Column("against", Integer, nullable=False, server_default="0"),
    Column("reason", String(32), nullable=False, server_default=""),
    Column("created_at", UtcDateTime),
    Column("updated_at", UtcDateTime),
    Column("active_since", UtcDateTime),
    Column("off_by", String(8)),
    Column("confirmed_by_client", Boolean, nullable=False, server_default=false()),
)

# The Admin's price list for model calls (global, not per client): what a million input and a million
# output tokens cost for a provider's model, in one currency. A model with no row has no known price.
llm_pricing = Table(
    "llm_pricing",
    metadata,
    Column("provider", String(64), primary_key=True),
    Column("model", String(128), primary_key=True),
    Column("input_per_million", Numeric(18, 6), nullable=False),
    Column("output_per_million", Numeric(18, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(255), nullable=False),
)

# Commercial plans (global) and the plan each client is on. An assignment keeps the plan's terms as
# they were when it started and is never rewritten, only ended (`ended_at`), so a future invoice can
# say which terms applied. `name_key` is the lower-cased name: names are unique ignoring case.
plans = Table(
    "plans",
    metadata,
    Column("plan_id", String(64), primary_key=True),
    Column("name", String(80), nullable=False),
    Column("name_key", String(80), nullable=False, unique=True),
    Column("monthly_price", Numeric(18, 2), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("included_credits", Integer, nullable=False),
    Column("daily_quota", Integer, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(255), nullable=False),
    Column("billing_period", String(8), nullable=False, server_default="monthly"),
)

client_plan_assignments = Table(
    "client_plan_assignments",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("plan_id", String(64), ForeignKey("plans.plan_id"), nullable=False),
    Column("plan_name", String(80), nullable=False),
    Column("monthly_price", Numeric(18, 2), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("included_credits", Integer, nullable=False),
    Column("daily_quota", Integer, nullable=False),
    Column("started_at", UtcDateTime, nullable=False),
    Column("ended_at", UtcDateTime),
    Column("assigned_by", String(255), nullable=False),
    Index("ix_client_plan_assignments_client", "client_id", "started_at"),
    Index(
        "uq_client_plan_assignments_current",
        "client_id",
        unique=True,
        postgresql_where=text("ended_at IS NULL"),
        sqlite_where=text("ended_at IS NULL"),
    ),
)

# Invoices, one per billing cycle. One per client and period = cycle date `YYYY-MM-DD` (legacy: `YYYY-MM`; the database refuses a second). An invoice carries its
# own copy of every line (plan name, price, currency, credits, quota, days, amount), so editing a plan
# afterwards changes nothing already invoiced. `status` is PENDING, FAILED, PAID, FOR_FREE or
# DISCOUNTED_100: FOR_FREE and the discount are declarations by the Admin, never a payment received.
# `amount` is always the billed amount before any discount; the discount columns are 0/NULL otherwise.
invoices = Table(
    "invoices",
    metadata,
    Column("invoice_id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("period", String(10), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("amount", Numeric(18, 2), nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255), nullable=False),
    Column("status_changed_at", UtcDateTime),
    Column("status_changed_by", String(255)),
    # How it was paid: "manual" (the Admin's declaration) or "electronic" (a verified payment
    # confirmation, with the provider's transaction and event ids). NULL while unpaid / free.
    Column("payment_origin", String(16)),
    Column("payment_transaction_id", String(128)),
    Column("payment_event_id", String(128)),
    Column("discount_percent", Integer, nullable=False, server_default="0"),
    Column("amount_after_discount", Numeric(18, 2)),
    Column("discount_amount", Numeric(18, 2)),
    Column("free_amount", Numeric(18, 2)),
    Column("discount_reason", String(255)),
    UniqueConstraint("client_id", "period", name="uq_invoices_client_period"),
    Index("ix_invoices_period", "period", "client_id"),
    Index("uq_invoices_transaction", "payment_transaction_id", unique=True),
)

# Every payment decision, traced: one row per distinct webhook event (applied or rejected; a replayed
# event_id is refused by the primary key and changes nothing) and one per manual settlement
# (`event_id` "manual:<invoice id>"). The table is the audit of how invoices got paid.
payment_events = Table(
    "payment_events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("source", String(16), nullable=False),
    Column("invoice_id", Integer, ForeignKey("invoices.invoice_id")),
    Column("client_id", String(64)),
    Column("invoice_reference", String(100)),
    Column("amount", Numeric(18, 2)),
    Column("currency", String(3)),
    Column("transaction_id", String(128)),
    Column("event_status", String(16), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("received_at", UtcDateTime, nullable=False),
    Index("ix_payment_events_invoice", "invoice_id"),
)

invoice_lines = Table(
    "invoice_lines",
    metadata,
    Column("line_id", Integer, primary_key=True, autoincrement=True),
    Column("invoice_id", Integer, ForeignKey("invoices.invoice_id"), nullable=False),
    Column("plan_id", String(64), nullable=False),
    Column("plan_name", String(80), nullable=False),
    Column("monthly_price", Numeric(18, 2), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("included_credits", Integer, nullable=False),
    Column("daily_quota", Integer, nullable=False),
    Column("days", Integer, nullable=False),
    Column("days_in_period", Integer, nullable=False),
    Column("amount", Numeric(18, 2), nullable=False),
    Index("ix_invoice_lines_invoice", "invoice_id"),
)


# Where each mailbox that is read without being consumed was read up to (`observe`): the digest of
# the mailbox (never its address), the UID of the first message not dealt with yet, and the
# UIDVALIDITY those UIDs belong to. Nothing of any message. It only moves forward (see the store).
op_mail_cursor = Table(
    "op_mail_cursor",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("mailbox_key", String(64), primary_key=True),
    Column("uid_validity", BigInteger, nullable=False),
    Column("next_uid", BigInteger, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# The daily send cap the Admin set for a client: a whole number of at least 1. No row, no cap, and
# nothing may be sent. There is no default value.
client_send_caps = Table(
    "client_send_caps",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("daily_cap", Integer, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(255), nullable=False),
    CheckConstraint("daily_cap >= 1", name="ck_client_send_caps_cap"),
)

# How many messages a client sent on one channel on one UTC day. A place is reserved here, in one
# statement, before a message is sent (see `sales_app.send_cap`). Counts only: nothing of a message.
op_send_counter = Table(
    "op_send_counter",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("channel", String(32), primary_key=True),
    Column("day", Date, primary_key=True),
    Column("used", Integer, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    CheckConstraint("used >= 0", name="ck_op_send_counter_used"),
)


# How a client wants its daily report: where, to whom, at what time of day and in which explicit
# IANA time zone. No row: no scheduled report. The recipient is the client's own address.
client_report_settings = Table(
    "client_report_settings",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("channel", String(16), nullable=False),
    Column("recipient", String(320), nullable=False),
    Column("send_time", String(5), nullable=False),  # HH:MM wall clock in `timezone`
    Column("timezone", String(64), nullable=False),
    Column("language", String(8), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(255), nullable=False),
)

# One report per client and day, whatever happened to its delivery (the primary key is the unique
# constraint). `figures` is what the report says, `text` what was sent; `status` is the honest state
# of the delivery. Errors are short codes, never what a server answered.
daily_reports = Table(
    "daily_reports",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("report_day", Date, primary_key=True),
    Column("window_start", UtcDateTime, nullable=False),
    Column("window_end", UtcDateTime, nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("generated_at", UtcDateTime, nullable=False),
    Column("language", String(8), nullable=False),
    Column("figures", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("body", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", UtcDateTime),
    Column("error_code", String(64)),
    Column("channel", String(16)),
    Column("recipient_hint", String(320)),
    Column("sent_at", UtcDateTime),
    Column("updated_at", UtcDateTime, nullable=False),
    CheckConstraint(
        "status IN ('pending','sending','sent','failed','undeliverable')",
        name="ck_daily_reports_status",
    ),
    Index("ix_daily_reports_status", "client_id", "status"),
)

# The documents and links a client attached to one of its products (`sales_app.resources`). A file's
# bytes are kept here, in the client's own rows: every statement filters on the client, so a resource
# can only be reached through the client that owns it. `status` is what could really be read from it,
# never a promise (ready / stored_only / failed).
product_resources = Table(
    "product_resources",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("product_id", String(64), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("category", String(16), nullable=False),
    Column("title", String(200), nullable=False),
    Column("url", Text),
    Column("filename", String(200)),
    Column("mime_type", String(100)),
    Column("size_bytes", Integer),
    Column("sha256", String(64)),
    Column("status", String(16), nullable=False),
    Column("status_detail", String(32)),
    Column("text_chars", Integer, nullable=False),
    Column("content", LargeBinary),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    CheckConstraint("kind IN ('link','file')", name="ck_product_resources_kind"),
    CheckConstraint(
        "status IN ('ready','stored_only','failed')", name="ck_product_resources_status"
    ),
    Index("ix_product_resources_product", "client_id", "product_id"),
)

# The searchable passages of a ready resource, in order. Text only.
product_resource_chunks = Table(
    "product_resource_chunks",
    metadata,
    Column(
        "resource_id",
        String(64),
        ForeignKey("product_resources.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column("client_id", String(64), nullable=False),
    Column("product_id", String(64), nullable=False),
    Column("body", Text, nullable=False),
    Index("ix_product_resource_chunks_product", "client_id", "product_id"),
)

# Credentials, encrypted with AES-256-GCM before they reach the database (`sales_app.secret_store`).
# `scope` is "global" (Revansa's) or "client:<id>" (one client's own): the scope and the key are bound
# into the ciphertext, so a row copied to another scope or key cannot be opened. The master key that
# opens them is never stored here. No foreign key on purpose: a scope also names the platform.
secrets = Table(
    "secrets",
    metadata,
    Column("scope", String(128), primary_key=True),
    Column("key", String(128), primary_key=True),
    Column("value", Text, nullable=False),  # ciphertext only
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(255), nullable=False),
    Index("ix_secrets_key", "key"),
)

# Who changed which credential, and when. Never a value, never anything derived from one.
secret_audit = Table(
    "secret_audit",
    metadata,
    Column(
        "id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    ),
    Column("at", UtcDateTime, nullable=False),
    Column("actor", String(255), nullable=False),
    Column("action", String(16), nullable=False),
    Column("scope", String(128), nullable=False),
    Column("key", String(128), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("reason", String(32)),
)

# The consent registry's events (`sales_app.sourcing.consent`): an append-only, hash-chained log per
# client. `(client_id, seq)` is the key, so two writers can never pick the same sequence number and
# one client's events can never be read as another's. `at` is the exact signed timestamp text, so a
# signature is always re-computed over the very characters it was made over. A contact is only ever
# a keyed digest (`key`). Rows are never updated or deleted (triggers, see migration 0017).
consent_events = Table(
    "consent_events",
    metadata,
    Column("client_id", String(64), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("event_id", String(32), nullable=False),
    Column("key", String(64), nullable=False),
    Column("type", String(16), nullable=False),
    Column("origin", String(24), nullable=False),
    Column("at", String(40), nullable=False),
    Column("actor", String(64), nullable=False),
    Column("reason", String(32), nullable=False),
    Column("evidence_ref", String(80), nullable=False),
    Column("prev_mac", String(64), nullable=False),
    Column("mac", String(64), nullable=False),
    UniqueConstraint("client_id", "event_id", name="uq_consent_events_event"),
    CheckConstraint("seq >= 1", name="ck_consent_events_seq"),
    Index("ix_consent_events_key", "client_id", "key"),
)


# ---- WhatsApp registers (see `sales_app.whatsapp_state`) -----------------------------------------
# The last time each person wrote to a client's WhatsApp account (the 24-hour window): a phone number
# and a date, nothing of a message.
wa_contact_windows = Table(
    "wa_contact_windows",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("wa_id", String(32), primary_key=True),
    Column("last_inbound_at", UtcDateTime, nullable=False),
)

# Meta's status of each message a client sent, by the id Meta gave it. Counts and error codes only.
wa_deliveries = Table(
    "wa_deliveries",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("message_id", String(128), primary_key=True),
    Column("status", String(16), nullable=False),
    Column("at", UtcDateTime, nullable=False),
    Column("recipient", String(32), nullable=False),
    Column("error_code", String(16), nullable=False),
    Column("error_title", String(200), nullable=False),
    Index("ix_wa_deliveries_status", "client_id", "status", "at"),
)

# A client's message templates as Meta knows them, and the one that opens a conversation.
wa_templates = Table(
    "wa_templates",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("name", String(512), primary_key=True),
    Column("language", String(16), primary_key=True),
    Column("category", String(24), nullable=False),
    Column("status", String(24), nullable=False),
    Column("body", Text, nullable=False),
    Column("parameter_names", String(512), nullable=False),
    Column("reason", String(300), nullable=False),
    Column("meta_id", String(40), nullable=False),
    Column("is_opening", Boolean, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)


# ---- multi-process resilience (migration 0020) ---------------------------------------------------
# Consents under way (OAuth / Embedded Signup), kept in the database instead of one process's memory,
# so the callback can land on another process than the one that started the consent. Single use (the
# row is deleted by the statement that reads it) and with an expiry. `verifier` is the PKCE verifier of
# a Google consent (useless without the authorisation code and the client's own browser session);
# empty for the others. Never shown, never logged.
oauth_pending_consents = Table(
    "oauth_pending_consents",
    metadata,
    Column("kind", String(32), primary_key=True),  # gmail | sheets | hubspot | whatsapp
    Column("session_id", String(128), primary_key=True),
    Column("client_id", String(64), nullable=False),
    Column("verifier", String(255), nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Index("ix_oauth_pending_expires", "expires_at"),
)

# One holder per (client, job): the scheduler of a process takes the lease before a job and frees it
# after; a holder that dies loses it at `expires_at`. Holds no data.
job_leases = Table(
    "job_leases",
    metadata,
    Column("client_id", String(64), primary_key=True),
    Column("job", String(32), primary_key=True),
    Column("holder", String(64), nullable=False),
    Column("acquired_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
)

# The central audit log (`sales_app.audit_log`): append-only. `before` / `after` are redacted before
# they get here; no secret is ever kept. `client_id` has no foreign key on purpose: the history of a
# client must outlive any change to its record.
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", UtcDateTime, nullable=False),
    Column("actor", String(128), nullable=False),
    Column("actor_role", String(32), nullable=False),
    Column("client_id", String(64), nullable=True),
    Column("action", String(96), nullable=False),
    Column("target", String(256), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("before", JSON().with_variant(JSONB(), "postgresql"), nullable=True),
    Column("after", JSON().with_variant(JSONB(), "postgresql"), nullable=True),
    Index("ix_audit_log_at", "at"),
    Index("ix_audit_log_client", "client_id", "at"),
    Index("ix_audit_log_action", "action", "at"),
)

# Notifications by role (`sales_app.notifications`). `dedupe_key` is unique (it already carries the
# audience and the client): one open notification per event.
notifications = Table(
    "notifications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("audience", String(8), nullable=False),
    Column("client_id", String(64), nullable=True),
    Column("kind", String(48), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("title", String(160), nullable=False),
    Column("message", String(400), nullable=False),
    Column("suggested_action", String(300), nullable=False),
    Column("dedupe_key", String(255), nullable=False),
    Column("read_at", UtcDateTime, nullable=True),
    Column("resolved_at", UtcDateTime, nullable=True),
    UniqueConstraint("dedupe_key", name="uq_notifications_dedupe"),
    Index("ix_notifications_audience", "audience", "client_id", "created_at"),
)


# ---- technical costs by provider (migration 0023, `sales_app.provider_costs`) ---------------------
# The Admin's cost parameters: a unit price per provider and key (e.g. meta_whatsapp / message_sent).
# No row: the price is not provided, the cost is "unavailable" and never invented.
cost_rates = Table(
    "cost_rates",
    metadata,
    Column("provider", String(32), primary_key=True),
    Column("rate_key", String(64), primary_key=True),
    Column("unit", String(32), nullable=False),
    Column("unit_price", Numeric(18, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(255), nullable=False),
)

# One cost line of a provider for a day or a month (`period_date` is the first day of the period).
# `basis` says how it is known: measured, estimated, unavailable (amount NULL). `client_id` NULL: a
# platform cost (infra) not attributed to one client; it has no foreign key on purpose.
provider_costs = Table(
    "provider_costs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), nullable=True),
    Column("provider", String(32), nullable=False),
    Column("category", String(64), nullable=False),
    Column("period_date", Date, nullable=False),
    Column("period_kind", String(8), nullable=False),  # day | month
    Column("quantity", Numeric(24, 6)),
    Column("unit", String(32), nullable=False),
    Column("unit_price", Numeric(18, 6)),
    Column("amount", Numeric(18, 6)),
    Column("currency", String(3)),
    Column("basis", String(16), nullable=False),
    Column("source", String(64), nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("recorded_by", String(255), nullable=False),
    Index("ix_provider_costs_period", "period_date", "provider"),
    Index("ix_provider_costs_client", "client_id", "period_date"),
)

# Who is in charge of a conversation (`sales_app.conversation_control`, migration 0025). One row per
# (client, prospect) that was ever handed to a person; no row means the agent. `mode` is `agent` or
# `human`; in `human` mode `taken_by` is empty while the conversation waits for a person (the Brain
# handed it over) and names the person once they took it. Durable on purpose: a restart must never
# give a conversation a person is handling back to the agent.
conversation_control = Table(
    "conversation_control",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("prospect_id", String(255), primary_key=True),
    Column("mode", String(8), nullable=False),
    Column("taken_by", String(128), nullable=True),
    Column("taken_at", UtcDateTime, nullable=True),
    Column("reason", String(300), nullable=True),
    Column("released_at", UtcDateTime, nullable=True),
    CheckConstraint("mode IN ('agent', 'human')", name="ck_conversation_control_mode"),
    Index("ix_conversation_control_mode", "client_id", "mode"),
)
