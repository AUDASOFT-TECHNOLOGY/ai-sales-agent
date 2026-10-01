"""Secrets in one JSON file, every value encrypted, rewritten atomically as a whole (few records,
changed rarely, single-process deployment: the reasoning of `FileClientProfileStore`)."""

import json
import os
import threading
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sales_app.secret_store.cipher import SecretCipher
from sales_app.secret_store.errors import (
    DuplicateSecretError,
    InvalidMasterKeyError,
    SecretDecryptError,
    SecretStoreCorruptError,
)
from sales_app.secret_store.port import SecretEntry

FORMAT = 1


def _context(scope: str, key: str) -> str:
    return f"revansa-secret|v{FORMAT}|{scope}|{key}"


class FileSecretStore:
    def __init__(
        self,
        path: Path,
        cipher: SecretCipher,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path)
        self._cipher = cipher
        self._now = now or (lambda: datetime.now(UTC))
        # Reentrant, and held by reads too: on Windows a file cannot be replaced while another thread
        # (the runtime refresh, say) has it open, so reading and rewriting must not overlap.
        self._lock = threading.RLock()

    def verify(self) -> None:
        """Open every record. Raises InvalidMasterKeyError if the key does not match the file."""
        try:
            with self._lock:
                records = self._read()
            for record in records:
                self._cipher.decrypt(record["value"], _context(record["scope"], record["key"]))
        except SecretDecryptError:
            raise InvalidMasterKeyError(
                "SALES_AGENT_SECRETS_KEY does not open the secrets file "
                f"{self._path} (wrong key, or the file was altered)"
            ) from None

    def get(self, scope: str, key: str) -> str | None:
        record = self._find(self._read(), scope, key)
        return (
            None if record is None else self._cipher.decrypt(record["value"], _context(scope, key))
        )

    def entry(self, scope: str, key: str) -> SecretEntry | None:
        record = self._find(self._read(), scope, key)
        return None if record is None else _entry(record)

    def entries(self, scope: str) -> Sequence[SecretEntry]:
        records = [r for r in self._read() if r["scope"] == scope]
        return tuple(_entry(r) for r in sorted(records, key=lambda r: r["key"]))

    def set(
        self, scope: str, key: str, value: str, *, by: str, unique: bool = False
    ) -> SecretEntry:
        with self._lock:
            records = self._read()
            if unique:
                for other in records:
                    if other["key"] == key and other["scope"] != scope:
                        held = self._cipher.decrypt(other["value"], _context(other["scope"], key))
                        if held == value:
                            raise DuplicateSecretError(other["scope"])
            record = {
                "scope": scope,
                "key": key,
                "value": self._cipher.encrypt(value, _context(scope, key)),
                "updated_at": self._now().isoformat(),
                "updated_by": by,
            }
            kept = [r for r in records if not (r["scope"] == scope and r["key"] == key)]
            self._write([*kept, record])
            return _entry(record)

    def delete(self, scope: str, key: str) -> bool:
        with self._lock:
            records = self._read()
            kept = [r for r in records if not (r["scope"] == scope and r["key"] == key)]
            if len(kept) == len(records):
                return False
            self._write(kept)
            return True

    # ---- file ----------------------------------------------------------------------------

    @staticmethod
    def _find(records: list[dict[str, Any]], scope: str, key: str) -> dict[str, Any] | None:
        return next((r for r in records if r["scope"] == scope and r["key"] == key), None)

    def _read(self) -> list[dict[str, Any]]:
        with self._lock:  # see __init__: a read must not overlap a replace
            return self._read_unlocked()

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                raise ValueError("unsupported file format")
            records = raw["records"]
            for record in records:
                for field in ("scope", "key", "value", "updated_at", "updated_by"):
                    if not isinstance(record[field], str):
                        raise TypeError(f"record field {field!r} is not text")
                datetime.fromisoformat(record["updated_at"])
            return list(records)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SecretStoreCorruptError(f"{self._path}: {error}") from error

    def _write(self, records: list[dict[str, Any]]) -> None:
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": FORMAT,
            "records": sorted(records, key=lambda r: (r["scope"], r["key"])),
        }
        temporary = directory / f".tmp-{uuid.uuid4().hex}"
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, 0o600)  # owner only where the platform supports it
            except OSError:
                pass
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _entry(record: dict[str, Any]) -> SecretEntry:
    return SecretEntry(
        scope=record["scope"],
        key=record["key"],
        updated_at=datetime.fromisoformat(record["updated_at"]),
        updated_by=record["updated_by"],
    )
