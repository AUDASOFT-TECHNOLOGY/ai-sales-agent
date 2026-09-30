"""Where admin accounts live.

Unlike `ConfigStore`'s append-only versions, accounts are edited in place (role changes,
deactivation, password resets), so this is a small mutable-record store, not a version history:
one JSON file, rewritten atomically as a whole on every change. Few accounts, changed rarely --
a whole-file rewrite under a lock is simpler than per-record files and safe for the
single-process deployment this app already assumes (see `sales_app.config_store.file_store`).
"""

# `list` is also this store's method name below; keep annotations lazy so `list[AdminUser]`
# type hints in the class body still resolve to the builtin, not the method.
from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sales_app.admin_auth.models import AdminRole, AdminStoreCorruptError, AdminUser

FORMAT = 1


@runtime_checkable
class AdminUserStore(Protocol):
    """Implementations are safe to share between threads."""

    def get_by_username(self, username: str) -> AdminUser | None: ...

    def get(self, admin_id: str) -> AdminUser | None: ...

    def list(self) -> Sequence[AdminUser]:
        """Every account, ordered by username."""
        ...

    def create(self, user: AdminUser) -> None:
        """Raises ValueError if the username or id is already taken."""
        ...

    def update(self, user: AdminUser) -> None:
        """Replaces the stored record for `user.id`. Raises KeyError if it does not exist."""
        ...


class FileAdminUserStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def get_by_username(self, username: str) -> AdminUser | None:
        needle = username.strip().lower()
        return next((u for u in self._read() if u.username == needle), None)

    def get(self, admin_id: str) -> AdminUser | None:
        return next((u for u in self._read() if u.id == admin_id), None)

    def list(self) -> Sequence[AdminUser]:
        return tuple(sorted(self._read(), key=lambda u: u.username))

    def create(self, user: AdminUser) -> None:
        with self._lock:
            users = self._read()
            if any(u.username == user.username for u in users):
                raise ValueError(f"username {user.username!r} already exists")
            if any(u.id == user.id for u in users):
                raise ValueError(f"id {user.id!r} already exists")
            self._write([*users, user])

    def update(self, user: AdminUser) -> None:
        with self._lock:
            users = self._read()
            index = next((i for i, u in enumerate(users) if u.id == user.id), None)
            if index is None:
                raise KeyError(f"no admin account {user.id!r}")
            users[index] = user
            self._write(users)

    # ---- internals -----------------------------------------------------------------------

    def _read(self) -> list[AdminUser]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                raise ValueError("unsupported file format")
            return [_user_from_json(item) for item in raw["users"]]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AdminStoreCorruptError(f"{self._path}: {error}") from error

    def _write(self, users: list[AdminUser]) -> None:
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"format": FORMAT, "users": [_user_to_json(u) for u in users]}
        temporary = directory / f".tmp-{uuid.uuid4().hex}"
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)  # atomic on POSIX and Windows; overwrite is fine
        finally:
            temporary.unlink(missing_ok=True)


def _user_to_json(user: AdminUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "password_hash": user.password_hash,
        "role": user.role.value,
        "client_ids": sorted(user.client_ids),
        "active": user.active,
        "created_at": user.created_at.isoformat(),
        "created_by": user.created_by,
    }


def _user_from_json(data: dict[str, Any]) -> AdminUser:
    return AdminUser(
        id=data["id"],
        username=data["username"],
        password_hash=data["password_hash"],
        role=AdminRole(data["role"]),
        client_ids=frozenset(data["client_ids"]),
        active=data["active"],
        created_at=datetime.fromisoformat(data["created_at"]),
        created_by=data.get("created_by"),
    )


class InMemoryAdminUserStore:
    """For tests: same behaviour, no filesystem."""

    def __init__(self, users: Sequence[AdminUser] = ()) -> None:
        self._users: dict[str, AdminUser] = {u.id: u for u in users}
        self._lock = threading.Lock()

    def get_by_username(self, username: str) -> AdminUser | None:
        needle = username.strip().lower()
        return next((u for u in self._users.values() if u.username == needle), None)

    def get(self, admin_id: str) -> AdminUser | None:
        return self._users.get(admin_id)

    def list(self) -> Sequence[AdminUser]:
        return tuple(sorted(self._users.values(), key=lambda u: u.username))

    def create(self, user: AdminUser) -> None:
        with self._lock:
            if any(u.username == user.username for u in self._users.values()):
                raise ValueError(f"username {user.username!r} already exists")
            if user.id in self._users:
                raise ValueError(f"id {user.id!r} already exists")
            self._users[user.id] = user

    def update(self, user: AdminUser) -> None:
        with self._lock:
            if user.id not in self._users:
                raise KeyError(f"no admin account {user.id!r}")
            self._users[user.id] = user
