"""Where a mailbox was read up to, when it is read without being consumed (`observe`).

A mailbox that is read without marking anything read (a client's own mailbox, where private mail
sits next to the replies) cannot use "unread" to know what is new. It remembers a position instead:
the UID of the first message it has not dealt with yet (`next_uid`), and the `uidvalidity` those UIDs
belong to. A server that renumbers a folder changes its UIDVALIDITY, and then no UID kept before
means anything: the reader starts from the date instead (`updated_at`) and lets the Engine
de-duplicate, rather than guess.

The position moves only after the Engine has stored the message (or the message was not for it), and
only past messages that are all dealt with: one that failed holds the position back, so it is offered
again. It never moves backwards within one UIDVALIDITY.

**The store is a port of plain values.** This adapter may import nothing of the application, and the
database may import nothing of the adapter, so a store hands over and takes back numbers and a date,
never a type of either side: `load` gives `(uidvalidity, next_uid, updated_at)` or None, and `save`
takes the three. Whatever a store raises, the reader sees one thing: the place could not be read or
kept (`MailCursorUnavailableError`, a fixed sentence).

A mailbox is identified by a digest, not by its address: the position needs to tell mailboxes apart,
not to say whose they are.
"""

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MailCursor:
    uidvalidity: int
    next_uid: int  # the first UID not dealt with yet: every UID before it is
    updated_at: datetime  # when the position last moved (UTC)


class MailCursorStore(Protocol):
    """The positions of one client's mailboxes, by mailbox digest."""

    def load(self, mailbox: str) -> tuple[int, int, datetime] | None:
        """(uidvalidity, next_uid, updated_at), or None if this mailbox was never read."""
        ...

    def save(self, mailbox: str, uidvalidity: int, next_uid: int, updated_at: datetime) -> None: ...


def mailbox_digest(host: str, username: str, folder: str) -> str:
    """What identifies one folder of one login on one server, without containing any of it."""
    text = "|".join(part.strip().lower() for part in (host, username, folder))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def moves_forward(held: tuple[int, int] | None, uidvalidity: int, next_uid: int) -> bool:
    """Whether a position may replace the one held, given as (uidvalidity, next_uid): a different
    UIDVALIDITY always may (the old UIDs mean nothing); within one, only a position further on."""
    return held is None or held[0] != uidvalidity or held[1] < next_uid


class InMemoryMailCursorStore:
    """For tests: positions are lost with the process, so a reader using it starts again from the
    end of the mailbox when it restarts."""

    def __init__(self) -> None:
        self._cursors: dict[str, tuple[int, int, datetime]] = {}
        self._lock = threading.Lock()

    def load(self, mailbox: str) -> tuple[int, int, datetime] | None:
        with self._lock:
            return self._cursors.get(mailbox)

    def save(self, mailbox: str, uidvalidity: int, next_uid: int, updated_at: datetime) -> None:
        with self._lock:
            held = self._cursors.get(mailbox)
            if moves_forward(None if held is None else held[:2], uidvalidity, next_uid):
                self._cursors[mailbox] = (uidvalidity, next_uid, updated_at)

    def __repr__(self) -> str:
        return f"InMemoryMailCursorStore(<{len(self._cursors)} positions>)"
