"""The ConfigStore seam: where a client's versioned settings live."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from sales_app.config_store.models import ConfigVersion


@runtime_checkable
class ConfigStore(Protocol):
    """Append-only, versioned storage of one settings document per client.

    Versions are numbered 1, 2, 3, ... with no gaps, and never change once written. The latest
    version is the current one; rolling back means publishing an old document again as a new
    version. Implementations are safe to share between threads and, for the file store, between
    processes. They validate nothing about a document beyond it being JSON-compatible.
    """

    def current(self, client_id: str) -> ConfigVersion | None:
        """The latest version, or None if nothing was published. Raises `ConfigCorruptError` if
        stored data cannot be trusted."""
        ...

    def get(self, client_id: str, version: int) -> ConfigVersion | None: ...

    def history(self, client_id: str) -> Sequence[ConfigVersion]:
        """Every version, oldest first."""
        ...

    def publish(
        self,
        client_id: str,
        document: Mapping[str, Any],
        *,
        author: str,
        note: str = "",
        expected_parent: int | None,
    ) -> ConfigVersion:
        """Append a new version. `expected_parent` is the version the caller believes is the latest
        (None: nothing published yet). Raises `ConfigConflictError`, writing nothing, if it is not."""
        ...
