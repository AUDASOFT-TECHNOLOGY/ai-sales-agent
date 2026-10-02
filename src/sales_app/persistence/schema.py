"""Creating and checking the database schema with Alembic. Nothing here runs by itself: the
application only *checks* the schema at startup and refuses to run against an outdated one; creating
or upgrading it is an explicit operator action (`sales-agent db upgrade`)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class SchemaNotReadyError(Exception):
    """The database schema is missing or not at the revision this version of the app needs."""


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(_config()).get_current_head()
    assert head is not None
    return head


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade_database(engine: Engine, revision: str = "head") -> None:
    config = _config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def ensure_schema_current(engine: Engine) -> None:
    """Raises `SchemaNotReadyError` unless the database is exactly at the latest revision."""
    current, head = current_revision(engine), head_revision()
    if current != head:
        found = current or "no schema"
        raise SchemaNotReadyError(
            f"database schema is at {found}, this version needs {head}: "
            "run `sales-agent db upgrade`"
        )
