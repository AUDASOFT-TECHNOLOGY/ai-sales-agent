"""What a client's last real connection test said, and about what.

A test proves an access at one moment, for one configuration. So a result is kept with a
`fingerprint` of what it was made for (the source and its connection parameters): once any of them
changes the fingerprint no longer matches and the result stops counting. The fingerprint is opaque
and holds no secret (a secret enters it only as the moment it was stored).
"""

from dataclasses import dataclass
from datetime import datetime


class SourceCheckStoreCorruptError(RuntimeError):
    """Stored test results cannot be trusted. The message names no value."""


class SourceCheckStoreUnavailableError(RuntimeError):
    """The store cannot be reached (a database down, a missing table). Says nothing of the cause."""


@dataclass(frozen=True, slots=True)
class SourceCheck:
    client_id: str
    fingerprint: str  # what the test was made for
    ok: bool
    checked_at: datetime

    def __post_init__(self) -> None:
        if not self.client_id:
            raise ValueError("a source check needs a client id")
        if not self.fingerprint:
            raise ValueError("a source check needs a fingerprint")
        if self.checked_at.tzinfo is None:
            raise ValueError("checked_at must be timezone-aware")
