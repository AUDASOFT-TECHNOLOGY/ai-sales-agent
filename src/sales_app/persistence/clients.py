"""The `clients` table: identity only. It anchors the foreign keys of everything a client owns, so a
profile, a session, a settings version or an Admin's scope can never point at a client that does not
exist. A client's name, status and settings are NOT kept here (see `client.yaml` and the versioned
settings); this is not a second copy of them.
"""

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from sqlalchemy import Connection, Engine, insert, select

from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.persistence.tables import clients

CONFIG_FILENAME = "client.yaml"


def ensure_client(
    connection: Connection, client_id: str, *, created_at: datetime, created_by: str | None
) -> bool:
    """Register a client if it is not known yet. True if a row was created. Safe against a
    concurrent registration of the same id."""
    if connection.execute(
        select(clients.c.client_id).where(clients.c.client_id == client_id)
    ).first():
        return False
    values = {"client_id": client_id, "created_at": created_at, "created_by": created_by}
    dialect = connection.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:  # pragma: no cover - only the two supported backends
        connection.execute(insert(clients).values(**values))
        return True
    result = connection.execute(dialect_insert(clients).values(**values).on_conflict_do_nothing())
    return bool(result.rowcount)


def existing_client_ids(connection: Connection, client_ids: Iterable[str]) -> set[str]:
    wanted = set(client_ids)
    if not wanted:
        return set()
    rows = connection.execute(select(clients.c.client_id).where(clients.c.client_id.in_(wanted)))
    return {row[0] for row in rows}


def client_directories(clients_dir: Path) -> list[str]:
    """The ids of every `clients/<id>/client.yaml` (the template directory is not a client)."""
    if not clients_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in clients_dir.iterdir()
        if p.is_dir()
        and re.fullmatch(CLIENT_ID_PATTERN.pattern, p.name)
        and (p / CONFIG_FILENAME).is_file()
    )


def seed_clients(
    engine: Engine, clients_dir: Path, *, now: datetime, by: str = "seed"
) -> list[str]:
    """Register every client directory not yet in the table. Idempotent; returns the new ids."""
    created = []
    with engine.begin() as connection:
        for client_id in client_directories(clients_dir):
            if ensure_client(connection, client_id, created_at=now, created_by=by):
                created.append(client_id)
    return created


def table_counts(engine: Engine) -> dict[str, int]:
    """Row counts of the tables `sales-agent db status` reports."""
    from sqlalchemy import func

    from sales_app.persistence.tables import (
        admin_user_clients,
        admin_users,
        client_profiles,
        config_versions,
    )

    tables = (clients, admin_users, admin_user_clients, client_profiles, config_versions)
    with engine.connect() as connection:
        return {
            t.name: connection.execute(select(func.count()).select_from(t)).scalar() or 0
            for t in tables
        }
