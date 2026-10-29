"""What the agent is doing right now, told as it happens.

The Engine announces each thing it starts (reading the prospect file, thinking about a message for a
person, sending it, reading a reply) and when it stops. Who listens is none of its business: the
application shows it to the client on a live screen. Telling is best effort: a reporter that fails
never changes what the Engine does, and `doing` swallows whatever it raises.

A step carries a kind from the list below, optionally the prospect it is about (an id and a display
name) and a short `detail`. Never put a message body, a token or an address in one.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

log = logging.getLogger(__name__)

READING_PROSPECTS = "reading_prospects"  # reading the prospect file / the CRM
PLANNING = "planning"  # choosing who to write to today
CHECKING = "checking"  # making sure a person may be written to
READING_HISTORY = "reading_history"  # re-reading what was said with a person
THINKING = "thinking"  # preparing a first message or a follow-up for a person
ANSWERING = "answering"  # preparing the answer to a person's reply
SENDING = "sending"  # handing a message to the channel
RECEIVING = "receiving"  # storing a reply that just came in
CHECKING_INBOX = "checking_inbox"  # looking for new replies
SYNCING = "syncing"  # writing results back to the client's CRM
REPORTING = "reporting"  # preparing the daily report


class ActivityReporter(Protocol):
    def begin(
        self,
        kind: str,
        *,
        prospect_id: str | None = None,
        name: str | None = None,
        detail: str | None = None,
    ) -> int | None:
        """A step starts. Returns a token for `end`, or None when nothing is kept."""
        ...

    def end(self, step: int | None, *, ok: bool = True, detail: str | None = None) -> None:
        """The step ended: `ok` False when it failed."""
        ...


class NoActivity:
    """The reporter of a deployment that shows nothing."""

    def begin(
        self,
        kind: str,
        *,
        prospect_id: str | None = None,
        name: str | None = None,
        detail: str | None = None,
    ) -> int | None:
        return None

    def end(self, step: int | None, *, ok: bool = True, detail: str | None = None) -> None:
        return None


NO_ACTIVITY = NoActivity()


@contextmanager
def doing(
    reporter: ActivityReporter,
    kind: str,
    *,
    prospect_id: str | None = None,
    name: str | None = None,
    detail: str | None = None,
) -> Iterator[None]:
    """Tells that `kind` starts, runs the body, then tells how it ended (failed if it raised; the
    exception goes on untouched)."""
    step: int | None = None
    try:
        step = reporter.begin(kind, prospect_id=prospect_id, name=name, detail=detail)
    except Exception as error:  # noqa: BLE001 - telling never changes the work
        log.warning("activity not reported: %s", error.__class__.__name__)
    ok = False
    try:
        yield
        ok = True
    finally:
        try:
            reporter.end(step, ok=ok)
        except Exception as error:  # noqa: BLE001
            log.warning("activity not reported: %s", error.__class__.__name__)
