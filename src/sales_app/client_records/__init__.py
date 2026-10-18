"""A client's record: identity, status and engine, kept in the database.

This is where a client *is*. `clients/<id>/client.yaml` is only a historical fallback for a client
whose record has not been filled yet (a field left `None` means "ask the file"). A record never holds
a setting, a credential or a Brain's data: those have their own homes (the versioned settings, the
credential store).

- `display_name`: the name given at creation. A published settings version still wins over it.
- `enabled`: whether the Admin has switched the account on. `None`: the file decides.
- `engine`: the catalogue key of the Brain the client runs on. `DEFAULT_ENGINE` is the generic
  consultative sales engine every new client uses. `None`: the client keeps the Brain its file names
  (its historical binding), untouched.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

DEFAULT_ENGINE = "consultative"


@dataclass(frozen=True, slots=True)
class ClientRecord:
    client_id: str
    display_name: str | None = None
    enabled: bool | None = None
    engine: str | None = None


class ClientRecordStore(Protocol):
    def get(self, client_id: str) -> ClientRecord | None: ...

    def list(self) -> list[ClientRecord]: ...

    def save(self, record: ClientRecord, *, at: datetime, by: str) -> None:
        """Create the client if it is unknown, then write the fields of `record` that are not None.
        A field left None is never erased."""

    def set_enabled(self, client_id: str, enabled: bool, *, at: datetime, by: str) -> None:
        """Switch a known client on or off (creating its record if it has none yet)."""


class InMemoryClientRecordStore:
    """For tests and for a deployment with no database (where the files remain the only truth)."""

    def __init__(self, records: Iterable[ClientRecord] = ()) -> None:
        self._records = {r.client_id: r for r in records}

    def get(self, client_id: str) -> ClientRecord | None:
        return self._records.get(client_id)

    def list(self) -> list[ClientRecord]:
        return sorted(self._records.values(), key=lambda r: r.client_id)

    def save(self, record: ClientRecord, *, at: datetime, by: str) -> None:
        current = self._records.get(record.client_id, ClientRecord(record.client_id))
        changes = {
            name: getattr(record, name)
            for name in ("display_name", "enabled", "engine")
            if getattr(record, name) is not None
        }
        self._records[record.client_id] = replace(current, **changes)

    def set_enabled(self, client_id: str, enabled: bool, *, at: datetime, by: str) -> None:
        self.save(ClientRecord(client_id, enabled=enabled), at=at, by=by)


__all__ = ["DEFAULT_ENGINE", "ClientRecord", "ClientRecordStore", "InMemoryClientRecordStore"]
