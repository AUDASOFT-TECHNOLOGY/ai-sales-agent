"""Admin accounts and sessions in SQL: the `AdminUserStore` and `AdminSessionStore` ports.

An account's client allow-list is the `admin_user_clients` table, with a foreign key to `clients`: a
scope pointing at a client that does not exist is refused.
"""

# `list` is also a method name below; keep annotations lazy so `list[...]` hints stay the builtin.
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Connection, Engine, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from sales_app.admin_auth.models import (
    AdminRole,
    AdminSession,
    AdminStoreCorruptError,
    AdminUser,
)
from sales_app.persistence.clients import existing_client_ids
from sales_app.persistence.tables import admin_sessions, admin_user_clients, admin_users


def read_user(
    connection: Connection, *, admin_id: str | None = None, username: str | None = None
) -> AdminUser | None:
    if admin_id is not None:
        condition = admin_users.c.id == admin_id
    else:
        condition = admin_users.c.username == username
    row = connection.execute(select(admin_users).where(condition)).first()
    return None if row is None else _user_from_row(connection, row)


def read_users(connection: Connection) -> list[AdminUser]:
    rows = connection.execute(select(admin_users).order_by(admin_users.c.username)).all()
    return [_user_from_row(connection, row) for row in rows]


def _user_from_row(connection: Connection, row) -> AdminUser:
    scope = connection.execute(
        select(admin_user_clients.c.client_id).where(admin_user_clients.c.admin_id == row.id)
    )
    try:
        return AdminUser(
            id=row.id,
            username=row.username,
            password_hash=row.password_hash,
            role=AdminRole(row.role),
            client_ids=frozenset(r[0] for r in scope),
            active=row.active,
            created_at=row.created_at,
            created_by=row.created_by,
        )
    except ValueError as error:
        raise AdminStoreCorruptError(f"admin account {row.id!r}: {error}") from error


def _user_values(user: AdminUser) -> dict:
    return {
        "username": user.username,
        "password_hash": user.password_hash,
        "role": user.role.value,
        "active": user.active,
        "created_at": user.created_at,
        "created_by": user.created_by,
    }


def _require_known_clients(connection: Connection, client_ids: frozenset[str]) -> None:
    unknown = client_ids - existing_client_ids(connection, client_ids)
    if unknown:
        raise ValueError(f"unknown client(s): {', '.join(sorted(unknown))}")


def insert_user(connection: Connection, user: AdminUser) -> None:
    """Raises ValueError for a taken username or id, or a client that does not exist."""
    _require_known_clients(connection, user.client_ids)
    taken = select(admin_users.c.id).where(admin_users.c.username == user.username)
    if connection.execute(taken).first():
        raise ValueError(f"username {user.username!r} already exists")
    taken = select(admin_users.c.id).where(admin_users.c.id == user.id)
    if connection.execute(taken).first():
        raise ValueError(f"id {user.id!r} already exists")
    connection.execute(insert(admin_users).values(id=user.id, **_user_values(user)))
    _write_scope(connection, user)


def _write_scope(connection: Connection, user: AdminUser) -> None:
    connection.execute(delete(admin_user_clients).where(admin_user_clients.c.admin_id == user.id))
    if user.client_ids:
        connection.execute(
            insert(admin_user_clients),
            [{"admin_id": user.id, "client_id": c} for c in sorted(user.client_ids)],
        )


class SqlAdminUserStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_by_username(self, username: str) -> AdminUser | None:
        with self._engine.connect() as connection:
            return read_user(connection, username=username.strip().lower())

    def get(self, admin_id: str) -> AdminUser | None:
        with self._engine.connect() as connection:
            return read_user(connection, admin_id=admin_id)

    def list(self) -> Sequence[AdminUser]:
        with self._engine.connect() as connection:
            return tuple(read_users(connection))

    def create(self, user: AdminUser) -> None:
        try:
            with self._engine.begin() as connection:
                insert_user(connection, user)
        except IntegrityError as error:  # a concurrent create took the username or id
            raise ValueError(f"account {user.username!r} already exists") from error

    def update(self, user: AdminUser) -> None:
        try:
            with self._engine.begin() as connection:
                exists = select(admin_users.c.id).where(admin_users.c.id == user.id)
                if not connection.execute(exists).first():
                    raise KeyError(f"no admin account {user.id!r}")
                _require_known_clients(connection, user.client_ids)
                connection.execute(
                    update(admin_users)
                    .where(admin_users.c.id == user.id)
                    .values(**_user_values(user))
                )
                _write_scope(connection, user)
        except IntegrityError as error:
            raise ValueError(f"username {user.username!r} already exists") from error


class _SqlSessionStore:
    """Sessions of one kind of owner. `AdminSession.admin_id` holds the owner id, whatever the
    owner is (an admin here, a client for `SqlClientSessionStore`)."""

    def __init__(self, engine: Engine, table, owner_column: str) -> None:
        self._engine = engine
        self._table = table
        self._owner = owner_column

    def create(self, session: AdminSession) -> None:
        values = {
            self._owner: session.admin_id,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
        }
        table = self._table
        with self._engine.begin() as connection:
            # Same as the file store: a token hash is unique, writing it again replaces it.
            connection.execute(delete(table).where(table.c.token_hash == session.token_hash))
            connection.execute(insert(table).values(token_hash=session.token_hash, **values))

    def get(self, token_hash: str) -> AdminSession | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._table).where(self._table.c.token_hash == token_hash)
            ).first()
        if row is None:
            return None
        try:
            return AdminSession(
                token_hash=row.token_hash,
                admin_id=getattr(row, self._owner),
                created_at=row.created_at,
                expires_at=row.expires_at,
            )
        except ValueError as error:
            raise AdminStoreCorruptError(f"session {token_hash[:8]}...: {error}") from error

    def delete(self, token_hash: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(self._table).where(self._table.c.token_hash == token_hash))

    def delete_all_for(self, admin_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(self._table).where(self._table.c[self._owner] == admin_id))


class SqlAdminSessionStore(_SqlSessionStore):
    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, admin_sessions, "admin_id")
