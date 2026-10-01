"""The port: what the application needs from a place that keeps credentials.

File-backed and encrypted today (`FileSecretStore`); a vault, a KMS or a database can implement
the same protocol later without the rest of the application changing. Note what is *not* here: no
method lists values, and `SecretEntry` has no value field, so a status can be shown anywhere
without risk. Only `get` returns a value, and only server code calls it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SecretEntry:
    """That a credential is set, and when and by whom. Never what it is."""

    scope: str
    key: str
    updated_at: datetime
    updated_by: str


@runtime_checkable
class SecretStore(Protocol):
    """Implementations are safe to share between threads. Keys must exist in the catalog; the
    store does not check that (see `CredentialService`), it only keeps what it is given."""

    def get(self, scope: str, key: str) -> str | None:
        """The stored value, for server-side use only."""
        ...

    def entry(self, scope: str, key: str) -> SecretEntry | None: ...

    def entries(self, scope: str) -> Sequence[SecretEntry]:
        """Everything set in this scope, ordered by key."""
        ...

    def set(
        self, scope: str, key: str, value: str, *, by: str, unique: bool = False
    ) -> SecretEntry:
        """Create or replace. With `unique`, raises DuplicateSecretError if another scope already
        holds the same value for this key (checked atomically with the write)."""
        ...

    def delete(self, scope: str, key: str) -> bool:
        """True if something was removed."""
        ...
