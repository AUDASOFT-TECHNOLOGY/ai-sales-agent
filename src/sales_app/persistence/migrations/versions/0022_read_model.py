"""Read model of a client's prospects and conversations: columns and indexes to filter, search, sort
and page by SQL, and a projection table of the prospects.

Additive only: no existing column, table or constraint changes. The new columns are nullable and
filled here from the stored documents (a row whose document cannot be read is left empty, never
guessed); the application fills them on every write after that. `op_prospect_index` is a projection
that the repository refreshes; here it is seeded from what the database already holds.

Revision ID: 0022
Revises: 0021
"""

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_BATCH = 500
_UTC = sa.DateTime(timezone=True)


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _doc(value: Any) -> dict[str, Any]:
    if isinstance(value, str):  # SQLite hands a JSON column back as text through a bare table
        import json

        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def upgrade() -> None:
    # ---- columns -------------------------------------------------------------------------
    op.add_column("op_actions", sa.Column("prospect_id", sa.String(255)))
    op.add_column("op_actions", sa.Column("action_type", sa.String(32)))
    op.add_column("op_actions", sa.Column("due_at", _UTC))
    op.add_column("op_messages", sa.Column("direction", sa.String(16)))
    op.add_column("op_messages", sa.Column("channel", sa.String(16)))
    op.add_column("op_messages", sa.Column("sent_at", _UTC))
    op.add_column("op_facts", sa.Column("fact_key", sa.String(128)))

    op.create_table(
        "op_prospect_index",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("prospect_id", sa.String(255), primary_key=True),
        sa.Column("external_id", sa.String(255)),
        sa.Column("lane_id", sa.String(128)),
        sa.Column("source_id", sa.String(128)),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(64)),
        sa.Column("search_text", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(255)),
        sa.Column("locale", sa.String(32)),
        sa.Column("channel", sa.String(16)),
        sa.Column("next_action_type", sa.String(32)),
        sa.Column("next_action_at", _UTC),
        sa.Column("last_contacted_at", _UTC),
        sa.Column("last_inbound_at", _UTC),
        sa.Column("last_activity_at", _UTC),
        sa.Column("follow_up_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("needs_human", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status_reason", sa.Text),
        sa.Column("conversation_id", sa.String(255)),
        sa.Column("indexed_at", _UTC, nullable=False),
        sa.UniqueConstraint(
            "client_id", "lane_id", "external_id", name="uq_op_prospect_index_external"
        ),
    )

    # ---- backfill (before the indexes, so they are built once) ----------------------------
    connection = op.get_bind()
    _backfill_actions(connection)
    _backfill_messages(connection)
    _backfill_facts(connection)
    _seed_index(connection)

    # ---- indexes -------------------------------------------------------------------------
    op.create_index("ix_op_actions_prospect", "op_actions", ["client_id", "prospect_id"])
    op.create_index("ix_op_actions_status_due", "op_actions", ["client_id", "status", "due_at"])
    op.create_index(
        "ix_op_messages_prospect_sent", "op_messages", ["client_id", "prospect_id", "sent_at"]
    )
    op.create_index("ix_op_messages_sent", "op_messages", ["client_id", "sent_at"])
    op.create_index("ix_op_facts_key", "op_facts", ["client_id", "fact_key"])
    op.create_index("ix_op_prospect_index_status", "op_prospect_index", ["client_id", "status"])
    op.create_index("ix_op_prospect_index_lane", "op_prospect_index", ["client_id", "lane_id"])
    op.create_index("ix_op_prospect_index_channel", "op_prospect_index", ["client_id", "channel"])
    op.create_index(
        "ix_op_prospect_index_next", "op_prospect_index", ["client_id", "next_action_at"]
    )
    op.create_index(
        "ix_op_prospect_index_activity", "op_prospect_index", ["client_id", "last_activity_at"]
    )
    op.create_index("ix_op_prospect_index_name", "op_prospect_index", ["client_id", "display_name"])


def downgrade() -> None:
    for name, table in (
        ("ix_op_prospect_index_name", "op_prospect_index"),
        ("ix_op_prospect_index_activity", "op_prospect_index"),
        ("ix_op_prospect_index_next", "op_prospect_index"),
        ("ix_op_prospect_index_channel", "op_prospect_index"),
        ("ix_op_prospect_index_lane", "op_prospect_index"),
        ("ix_op_prospect_index_status", "op_prospect_index"),
        ("ix_op_facts_key", "op_facts"),
        ("ix_op_messages_sent", "op_messages"),
        ("ix_op_messages_prospect_sent", "op_messages"),
        ("ix_op_actions_status_due", "op_actions"),
        ("ix_op_actions_prospect", "op_actions"),
    ):
        op.drop_index(name, table_name=table)
    op.drop_table("op_prospect_index")
    with op.batch_alter_table("op_facts") as batch:
        batch.drop_column("fact_key")
    with op.batch_alter_table("op_messages") as batch:
        batch.drop_column("sent_at")
        batch.drop_column("channel")
        batch.drop_column("direction")
    with op.batch_alter_table("op_actions") as batch:
        batch.drop_column("due_at")
        batch.drop_column("action_type")
        batch.drop_column("prospect_id")


# ---- backfill ----------------------------------------------------------------------------------


def _batches(connection: sa.Connection, table: sa.Table, key: sa.Column, columns: list):
    """Rows of `table` in pages by `key` (a single increasing column), never all at once."""
    last = None
    while True:
        query = sa.select(key, *columns).order_by(key).limit(_BATCH)
        if last is not None:
            query = query.where(key > last)
        rows = connection.execute(query).all()
        if not rows:
            return
        yield rows
        last = rows[-1][0]


def _backfill_messages(connection: sa.Connection) -> None:
    table = sa.table(
        "op_messages",
        sa.column("seq", sa.Integer),
        sa.column("document", sa.JSON),
        sa.column("direction", sa.String),
        sa.column("channel", sa.String),
        sa.column("sent_at", _UTC),
    )
    for rows in _batches(connection, table, table.c.seq, [table.c.document]):
        for seq, document in rows:
            doc = _doc(document)
            connection.execute(
                table.update()
                .where(table.c.seq == seq)
                .values(
                    direction=doc.get("direction"),
                    channel=doc.get("channel"),
                    sent_at=_when(doc.get("timestamp")),
                )
            )


def _backfill_actions(connection: sa.Connection) -> None:
    table = sa.table(
        "op_actions",
        sa.column("client_id", sa.String),
        sa.column("action_id", sa.String),
        sa.column("document", sa.JSON),
        sa.column("prospect_id", sa.String),
        sa.column("action_type", sa.String),
        sa.column("due_at", _UTC),
    )
    last: tuple[str, str] | None = None
    while True:
        query = (
            sa.select(table.c.client_id, table.c.action_id, table.c.document)
            .order_by(table.c.client_id, table.c.action_id)
            .limit(_BATCH)
        )
        if last is not None:
            query = query.where(sa.tuple_(table.c.client_id, table.c.action_id) > sa.tuple_(*last))
        rows = connection.execute(query).all()
        if not rows:
            return
        for client_id, action_id, document in rows:
            doc = _doc(document)
            connection.execute(
                table.update()
                .where(table.c.client_id == client_id, table.c.action_id == action_id)
                .values(
                    prospect_id=doc.get("prospect_id"),
                    action_type=doc.get("action_type"),
                    due_at=_when(doc.get("due_at")),
                )
            )
        last = (rows[-1][0], rows[-1][1])


def _backfill_facts(connection: sa.Connection) -> None:
    table = sa.table(
        "op_facts",
        sa.column("seq", sa.Integer),
        sa.column("document", sa.JSON),
        sa.column("fact_key", sa.String),
    )
    for rows in _batches(connection, table, table.c.seq, [table.c.document]):
        for seq, document in rows:
            key = _doc(document).get("key")
            if isinstance(key, str):
                connection.execute(
                    table.update().where(table.c.seq == seq).values(fact_key=key[:128])
                )


def _seed_index(connection: sa.Connection) -> None:
    """One index row per prospect Revansa holds a state for, with the name and contact of the
    local ones. The state of a source's prospect never holds a name: the first repository read
    after the upgrade completes those rows."""
    states = sa.table(
        "op_prospect_states",
        sa.column("client_id", sa.String),
        sa.column("prospect_id", sa.String),
        sa.column("conversation_id", sa.String),
        sa.column("document", sa.JSON),
    )
    local = sa.table(
        "op_local_prospects",
        sa.column("client_id", sa.String),
        sa.column("prospect_id", sa.String),
        sa.column("document", sa.JSON),
    )
    text_columns = [
        "client_id",
        "prospect_id",
        "external_id",
        "lane_id",
        "source_id",
        "display_name",
        "email",
        "phone",
        "search_text",
        "status",
        "stage",
        "locale",
        "channel",
        "next_action_type",
        "status_reason",
        "conversation_id",
    ]
    when_columns = [
        "next_action_at",
        "last_contacted_at",
        "last_inbound_at",
        "last_activity_at",
        "indexed_at",
    ]
    index = sa.table(
        "op_prospect_index",
        *(sa.column(name, sa.String) for name in text_columns),
        *(sa.column(name, _UTC) for name in when_columns),
        sa.column("follow_up_count", sa.Integer),
        sa.column("needs_human", sa.Boolean),
    )
    messages = sa.table(
        "op_messages",
        sa.column("client_id", sa.String),
        sa.column("prospect_id", sa.String),
        sa.column("sent_at", _UTC),
    )
    now = datetime.now(UTC)
    names: dict[tuple[str, str], dict[str, Any]] = {}
    for client_id, prospect_id, document in connection.execute(
        sa.select(local.c.client_id, local.c.prospect_id, local.c.document)
    ):
        names[(client_id, prospect_id)] = _doc(document)
    last_message: dict[tuple[str, str], datetime] = {}
    for client_id, prospect_id, newest in connection.execute(
        sa.select(messages.c.client_id, messages.c.prospect_id, sa.func.max(messages.c.sent_at))
        .where(messages.c.sent_at.is_not(None))
        .group_by(messages.c.client_id, messages.c.prospect_id)
    ):
        moment = _when(newest) if isinstance(newest, str) else newest
        if moment is not None:
            last_message[(client_id, prospect_id)] = (
                moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
            )

    rows: list[dict[str, Any]] = []
    for client_id, prospect_id, conversation_id, document in connection.execute(
        sa.select(
            states.c.client_id, states.c.prospect_id, states.c.conversation_id, states.c.document
        )
    ):
        state = _doc(document)
        identity = names.get((client_id, prospect_id))
        contact = (identity or {}).get("contact") or {}
        schedule = state.get("schedule") or {}
        contacted = _when(schedule.get("last_contacted_at"))
        inbound = _when(schedule.get("last_inbound_at"))
        recent = [m for m in (contacted, inbound, last_message.get((client_id, prospect_id))) if m]
        status = state.get("status") or (identity or {}).get("status") or "new"
        name = str((identity or {}).get("display_name") or "")
        email = contact.get("email")
        phone = contact.get("phone")
        rows.append(
            {
                "client_id": client_id,
                "prospect_id": prospect_id,
                "external_id": prospect_id,
                "lane_id": state.get("lane_id") or (identity or {}).get("lane_id"),
                "source_id": "local" if identity is not None else None,
                "display_name": name,
                "email": email,
                "phone": phone,
                "search_text": " ".join(
                    part
                    for part in (
                        name.lower(),
                        str(email or "").lower(),
                        str(phone or "").lower(),
                        "".join(ch for ch in str(phone or "") if ch.isdigit()),
                    )
                    if part
                ),
                "status": status,
                "stage": state.get("stage"),
                "locale": (identity or {}).get("locale"),
                "channel": contact.get("preferred_channel"),
                "next_action_type": schedule.get("next_action_type"),
                "next_action_at": _when(schedule.get("next_action_due_at")),
                "last_contacted_at": contacted,
                "last_inbound_at": inbound,
                "last_activity_at": max(recent) if recent else None,
                "follow_up_count": int(schedule.get("follow_up_count") or 0),
                "needs_human": status == "paused",
                "status_reason": state.get("status_reason"),
                "conversation_id": conversation_id,
                "indexed_at": now,
            }
        )
        if len(rows) >= _BATCH:
            connection.execute(index.insert(), rows)
            rows = []
    if rows:
        connection.execute(index.insert(), rows)
