"""Choosing the store at startup, from the deployment's settings.

- A secrets file exists: the master key is mandatory and must open it, or startup is refused. The
  alternative (carrying on without the stored credentials) would run clients on the wrong ones.
- No file yet: with a key, a normal empty store; without one, an unavailable store (reads find
  nothing, writes are refused with a clear message), so today's environment-variable setups work.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from sales_app.secret_store.cipher import AesGcmCipher
from sales_app.secret_store.errors import (
    InvalidMasterKeyError,
    SecretStoreError,
    SecretStoreUnavailableError,
)
from sales_app.secret_store.file_store import FileSecretStore
from sales_app.secret_store.port import SecretEntry, SecretStore

SECRETS_FILENAME = "secrets.json"
AUDIT_FILENAME = "audit.jsonl"
KEY_VARIABLE = "SALES_AGENT_SECRETS_KEY"


class UnavailableSecretStore:
    """No master key and nothing stored: reads are empty, writes say why they cannot happen."""

    def get(self, scope: str, key: str) -> str | None:
        return None

    def entry(self, scope: str, key: str) -> SecretEntry | None:
        return None

    def entries(self, scope: str) -> Sequence[SecretEntry]:
        return ()

    def set(
        self, scope: str, key: str, value: str, *, by: str, unique: bool = False
    ) -> SecretEntry:
        raise SecretStoreUnavailableError(
            f"credential storage is not enabled: set {KEY_VARIABLE} "
            "(create one with `sales-agent secrets generate-key`) and restart"
        )

    def delete(self, scope: str, key: str) -> bool:
        raise SecretStoreUnavailableError(
            f"credential storage is not enabled: set {KEY_VARIABLE} and restart"
        )


def build_secret_store(
    directory: Path, key: str | None, *, now: Callable[[], datetime] | None = None
) -> SecretStore:
    """The store for this deployment, or a SecretStoreError describing what to fix."""
    path = Path(directory) / SECRETS_FILENAME
    text = (key or "").strip()
    if not text:
        if path.exists():
            raise InvalidMasterKeyError(
                f"{path} exists but {KEY_VARIABLE} is not set: refusing to start without the "
                "key that opens it"
            )
        return UnavailableSecretStore()
    store = FileSecretStore(path, AesGcmCipher.from_text(text), now=now)
    store.verify()
    return store


__all__ = [
    "AUDIT_FILENAME",
    "KEY_VARIABLE",
    "SECRETS_FILENAME",
    "SecretStoreError",
    "UnavailableSecretStore",
    "build_secret_store",
]
