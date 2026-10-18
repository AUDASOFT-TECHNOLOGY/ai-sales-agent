"""The schema, as SQLAlchemy Core tables. The Alembic migrations are the source of truth for what
a deployed database looks like; these definitions are what the stores query with.

Account and configuration data, and (the `op_*` tables) a client's operational data when its
prospects live in another system. No credential of any kind (see `sales_app.secret_store`).
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
    UniqueConstraint("client_id", "idempotency_key", name="uq_op_actions_idempotency_key"),
    Index("ix_op_actions_status", "client_id", "status"),
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
    UniqueConstraint("client_id", "message_id", name="uq_op_messages_message_id"),
    Index("ix_op_messages_prospect", "client_id", "prospect_id"),
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
    UniqueConstraint("client_id", "fact_id", name="uq_op_facts_fact_id"),
    Index("ix_op_facts_prospect", "client_id", "prospect_id"),
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

# Monthly invoices. One per client and period (the database refuses a second). An invoice carries its
# own copy of every line (plan name, price, currency, credits, quota, days, amount), so editing a plan
# afterwards changes nothing already invoiced. `status` is PENDING, PAID or FOR_FREE: a declaration by
# the Admin, never a payment received.
invoices = Table(
    "invoices",
    metadata,
    Column("invoice_id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("period", String(7), nullable=False),
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
