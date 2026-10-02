import sys
from pathlib import Path

# Reuse the generic builders from tests/unit (factories, engine_harness) and tests/kit (settings).
for name in ("unit", "kit"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / name))


from datetime import UTC, datetime

import pytest

SEEDED_CLIENT_IDS = ("alpha", "beta", "gamma", "acme", "a", "b")


@pytest.fixture
def sql_engine():
    """A fresh in-memory database built by the real Alembic migrations (the same schema a
    deployment gets). SQLite stands in for PostgreSQL here; nothing in the stores depends on it."""
    from sales_app.persistence import (
        create_database_engine,
        ensure_schema_current,
        upgrade_database,
    )
    from sales_app.persistence.clients import ensure_client

    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    ensure_schema_current(engine)
    with engine.begin() as connection:
        for client_id in SEEDED_CLIENT_IDS:
            ensure_client(
                connection,
                client_id,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                created_by="test",
            )
    yield engine
    engine.dispose()
