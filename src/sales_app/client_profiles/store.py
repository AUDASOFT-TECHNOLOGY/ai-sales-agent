"""Where client profiles live: one JSON file, rewritten atomically as a whole (few records,
changed rarely, single-process deployment -- same reasoning as `FileAdminUserStore`)."""

# `list` is also this store's method name; keep annotations lazy so `list[...]` hints resolve.
from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sales_app.client_profiles.models import (
    ClientProfile,
    ClientProfileStoreCorruptError,
    normalize_login,
)

FORMAT = 1


@runtime_checkable
class ClientProfileStore(Protocol):
    """Implementations are safe to share between threads."""

    def get(self, client_id: str) -> ClientProfile | None: ...

    def get_by_login(self, login: str) -> ClientProfile | None: ...

    def list(self) -> Sequence[ClientProfile]:
        """Every profile, ordered by client id."""
        ...

    def create(self, profile: ClientProfile) -> None:
        """Raises ValueError if the client id or login is already taken."""
        ...

    def update(self, profile: ClientProfile) -> None:
        """Replace the stored profile with the same client id. Raises ValueError if there is none,
        or if its login now belongs to another client."""
        ...


def _check_update(existing: Sequence[ClientProfile], profile: ClientProfile) -> None:
    if not any(p.client_id == profile.client_id for p in existing):
        raise ValueError(f"client {profile.client_id!r} has no profile")
    if any(p.login == profile.login and p.client_id != profile.client_id for p in existing):
        raise ValueError(f"login {profile.login!r} already exists")


def _check_unique(existing: Sequence[ClientProfile], profile: ClientProfile) -> None:
    if any(p.client_id == profile.client_id for p in existing):
        raise ValueError(f"client {profile.client_id!r} already has a profile")
    if any(p.login == profile.login for p in existing):
        raise ValueError(f"login {profile.login!r} already exists")


class FileClientProfileStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def get(self, client_id: str) -> ClientProfile | None:
        return next((p for p in self._read() if p.client_id == client_id), None)

    def get_by_login(self, login: str) -> ClientProfile | None:
        needle = normalize_login(login)
        return next((p for p in self._read() if p.login == needle), None)

    def list(self) -> Sequence[ClientProfile]:
        return tuple(sorted(self._read(), key=lambda p: p.client_id))

    def create(self, profile: ClientProfile) -> None:
        with self._lock:
            profiles = self._read()
            _check_unique(profiles, profile)
            self._write([*profiles, profile])

    def update(self, profile: ClientProfile) -> None:
        with self._lock:
            profiles = self._read()
            _check_update(profiles, profile)
            self._write([profile if p.client_id == profile.client_id else p for p in profiles])

    def _read(self) -> list[ClientProfile]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                raise ValueError("unsupported file format")
            return [_from_json(item) for item in raw["profiles"]]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ClientProfileStoreCorruptError(f"{self._path}: {error}") from error

    def _write(self, profiles: list[ClientProfile]) -> None:
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"format": FORMAT, "profiles": [_to_json(p) for p in profiles]}
        temporary = directory / f".tmp-{uuid.uuid4().hex}"
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _to_json(profile: ClientProfile) -> dict[str, Any]:
    return {
        "client_id": profile.client_id,
        "login": profile.login,
        "password_hash": profile.password_hash,
        "must_change_password": profile.must_change_password,
        "logo": profile.logo,
        "whatsapp_number": profile.whatsapp_number,
        "whatsapp_reconnect_required": profile.whatsapp_reconnect_required,
        "created_at": profile.created_at.isoformat(),
        "created_by": profile.created_by,
    }


def _from_json(data: dict[str, Any]) -> ClientProfile:
    return ClientProfile(
        client_id=data["client_id"],
        login=data["login"],
        password_hash=data["password_hash"],
        must_change_password=data["must_change_password"],
        logo=data.get("logo"),
        whatsapp_number=data.get("whatsapp_number"),
        whatsapp_reconnect_required=data.get("whatsapp_reconnect_required", False),
        created_at=datetime.fromisoformat(data["created_at"]),
        created_by=data.get("created_by"),
    )


class InMemoryClientProfileStore:
    """For tests: same behaviour, no filesystem."""

    def __init__(self, profiles: Sequence[ClientProfile] = ()) -> None:
        self._profiles: list[ClientProfile] = list(profiles)
        self._lock = threading.Lock()

    def get(self, client_id: str) -> ClientProfile | None:
        return next((p for p in self._profiles if p.client_id == client_id), None)

    def get_by_login(self, login: str) -> ClientProfile | None:
        needle = normalize_login(login)
        return next((p for p in self._profiles if p.login == needle), None)

    def list(self) -> Sequence[ClientProfile]:
        return tuple(sorted(self._profiles, key=lambda p: p.client_id))

    def create(self, profile: ClientProfile) -> None:
        with self._lock:
            _check_unique(self._profiles, profile)
            self._profiles.append(profile)

    def update(self, profile: ClientProfile) -> None:
        with self._lock:
            _check_update(self._profiles, profile)
            self._profiles = [
                profile if p.client_id == profile.client_id else p for p in self._profiles
            ]
