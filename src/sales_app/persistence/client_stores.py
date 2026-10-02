"""Client profiles and Client sessions in SQL: the `ClientProfileStore` and `AdminSessionStore`
ports (a Client session is an `AdminSession` whose `admin_id` field holds the client id).

Creating a profile also registers the client in `clients` in the same transaction, so a client and
its profile appear together or not at all. Login and WhatsApp number are unique in the database
itself: a race between two requests ends in a refusal, never in a duplicate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from sales_app.client_profiles.models import (
    ClientProfile,
    ClientProfileStoreCorruptError,
    normalize_login,
)
from sales_app.persistence.admin_stores import _SqlSessionStore
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import client_profiles, client_sessions


def whatsapp_digits(number: str | None) -> str | None:
    digits = "".join(ch for ch in number or "" if ch.isdigit())
    return digits or None


def _profile_from_row(row) -> ClientProfile:
    try:
        return ClientProfile(
            client_id=row.client_id,
            login=row.login,
            password_hash=row.password_hash,
            must_change_password=row.must_change_password,
            logo=row.logo,
            whatsapp_number=row.whatsapp_number,
            whatsapp_reconnect_required=row.whatsapp_reconnect_required,
            created_at=row.created_at,
            created_by=row.created_by,
        )
    except ValueError as error:
        raise ClientProfileStoreCorruptError(
            f"client profile {row.client_id!r}: {error}"
        ) from error


def read_profile(connection: Connection, client_id: str) -> ClientProfile | None:
    row = connection.execute(
        select(client_profiles).where(client_profiles.c.client_id == client_id)
    ).first()
    return None if row is None else _profile_from_row(row)


def _values(profile: ClientProfile) -> dict:
    return {
        "login": profile.login,
        "password_hash": profile.password_hash,
        "must_change_password": profile.must_change_password,
        "logo": profile.logo,
        "whatsapp_number": profile.whatsapp_number,
        "whatsapp_digits": whatsapp_digits(profile.whatsapp_number),
        "whatsapp_reconnect_required": profile.whatsapp_reconnect_required,
        "created_at": profile.created_at,
        "created_by": profile.created_by,
    }


def _check_free(connection: Connection, profile: ClientProfile) -> None:
    """Raises ValueError if the login or number belongs to another client."""
    holder = connection.execute(
        select(client_profiles.c.client_id).where(client_profiles.c.login == profile.login)
    ).first()
    if holder is not None and holder[0] != profile.client_id:
        raise ValueError(f"login {profile.login!r} already exists")
    digits = whatsapp_digits(profile.whatsapp_number)
    if digits is not None:
        holder = connection.execute(
            select(client_profiles.c.client_id).where(client_profiles.c.whatsapp_digits == digits)
        ).first()
        if holder is not None and holder[0] != profile.client_id:
            raise ValueError("this WhatsApp number already belongs to another client")


def insert_profile(connection: Connection, profile: ClientProfile) -> None:
    """Registers the client if needed, then its profile. Raises ValueError for a client that
    already has one, or a login or WhatsApp number that is taken."""
    ensure_client(
        connection,
        profile.client_id,
        created_at=profile.created_at,
        created_by=profile.created_by,
    )
    if read_profile(connection, profile.client_id) is not None:
        raise ValueError(f"client {profile.client_id!r} already has a profile")
    _check_free(connection, profile)
    connection.execute(
        insert(client_profiles).values(client_id=profile.client_id, **_values(profile))
    )


class SqlClientProfileStore:
    def __init__(self, engine: Engine, *, now: Callable[[], datetime] | None = None) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))

    def get(self, client_id: str) -> ClientProfile | None:
        with self._engine.connect() as connection:
            return read_profile(connection, client_id)

    def get_by_login(self, login: str) -> ClientProfile | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(client_profiles).where(client_profiles.c.login == normalize_login(login))
            ).first()
        return None if row is None else _profile_from_row(row)

    def list(self) -> Sequence[ClientProfile]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(client_profiles).order_by(client_profiles.c.client_id)
            ).all()
        return tuple(_profile_from_row(row) for row in rows)

    def create(self, profile: ClientProfile) -> None:
        try:
            with self._engine.begin() as connection:
                insert_profile(connection, profile)
        except IntegrityError as error:  # a concurrent request took the login or the number
            raise ValueError("login or WhatsApp number already exists") from error

    def update(self, profile: ClientProfile) -> None:
        try:
            with self._engine.begin() as connection:
                if read_profile(connection, profile.client_id) is None:
                    raise ValueError(f"client {profile.client_id!r} has no profile")
                _check_free(connection, profile)
                connection.execute(
                    update(client_profiles)
                    .where(client_profiles.c.client_id == profile.client_id)
                    .values(**_values(profile), updated_at=self._now())
                )
        except IntegrityError as error:
            raise ValueError("login or WhatsApp number already exists") from error


class SqlClientSessionStore(_SqlSessionStore):
    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, client_sessions, "client_id")
