"""Where admin sessions live: one file per session, named by the token's hash, so a lookup or a
delete never scans the directory and concurrent logins never contend on the same file.
"""

import json
import os
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sales_app.admin_auth.models import AdminSession, AdminStoreCorruptError

FORMAT = 1


@runtime_checkable
class AdminSessionStore(Protocol):
    """Implementations are safe to share between threads and, for the file store, processes."""

    def create(self, session: AdminSession) -> None: ...

    def get(self, token_hash: str) -> AdminSession | None: ...

    def delete(self, token_hash: str) -> None:
        """No error if the session is already gone (logout is idempotent)."""
        ...

    def delete_all_for(self, admin_id: str) -> None:
        """Every session of one account: used on deactivation, role change or password reset so
        the change takes effect immediately instead of waiting for sessions to expire."""
        ...


class FileAdminSessionStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def create(self, session: AdminSession) -> None:
        self._write(session)

    def get(self, token_hash: str) -> AdminSession | None:
        path = self._path(token_hash)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                raise ValueError("unsupported file format")
            if raw["token_hash"] != token_hash:
                raise ValueError("file does not match its name")
            return _session_from_json(raw)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AdminStoreCorruptError(f"{path}: {error}") from error

    def delete(self, token_hash: str) -> None:
        self._path(token_hash).unlink(missing_ok=True)

    def delete_all_for(self, admin_id: str) -> None:
        if not self._root.is_dir():
            return
        for path in self._root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # a session file this account cannot own; leave it for `get` to reject
            if isinstance(raw, dict) and raw.get("admin_id") == admin_id:
                path.unlink(missing_ok=True)

    # ---- internals -----------------------------------------------------------------------

    def _path(self, token_hash: str) -> Path:
        return self._root / f"{token_hash}.json"

    def _write(self, session: AdminSession) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._path(session.token_hash)
        temporary = self._root / f".tmp-{uuid.uuid4().hex}"
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(_session_to_json(session), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)  # atomic; overwriting is fine (no version history here)
        finally:
            temporary.unlink(missing_ok=True)


def _session_to_json(session: AdminSession) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "token_hash": session.token_hash,
        "admin_id": session.admin_id,
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
    }


def _session_from_json(data: dict[str, Any]) -> AdminSession:
    return AdminSession(
        token_hash=data["token_hash"],
        admin_id=data["admin_id"],
        created_at=datetime.fromisoformat(data["created_at"]),
        expires_at=datetime.fromisoformat(data["expires_at"]),
    )


class InMemoryAdminSessionStore:
    """For tests: same behaviour, no filesystem."""

    def __init__(self) -> None:
        self._sessions: dict[str, AdminSession] = {}

    def create(self, session: AdminSession) -> None:
        self._sessions[session.token_hash] = session

    def get(self, token_hash: str) -> AdminSession | None:
        return self._sessions.get(token_hash)

    def delete(self, token_hash: str) -> None:
        self._sessions.pop(token_hash, None)

    def delete_all_for(self, admin_id: str) -> None:
        for token_hash in [h for h, s in self._sessions.items() if s.admin_id == admin_id]:
            del self._sessions[token_hash]

    def all(self) -> Sequence[AdminSession]:
        """Test helper only, not part of the port."""
        return tuple(self._sessions.values())
