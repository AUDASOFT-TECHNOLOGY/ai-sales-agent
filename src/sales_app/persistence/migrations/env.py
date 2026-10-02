"""Alembic environment. Migrations run through `sales_app.persistence.schema`, which hands over an
open connection; with none, the URL in the Alembic config is used."""

from alembic import context
from sqlalchemy import create_engine

from sales_app.persistence.database import normalize_url

config = context.config


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(normalize_url(config.get_main_option("sqlalchemy.url")))
    with engine.connect() as opened:
        context.configure(connection=opened, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
