"""Channels that have to be asked for their replies (e-mail), as opposed to those that call us.

WhatsApp pushes each reply to a webhook. A mailbox does not: the scheduler opens it from time to
time and hands what it finds to the Engine, exactly as the webhook route does (`receive_inbound`,
then `process_inbound`). Nothing here is e-mail specific: any channel that can list its unread
messages and mark one read can be polled the same way.

**Never lost, never twice.** A message is acknowledged only after the Engine has stored it, and the
Engine de-duplicates by the message's own id. A crash between the two leaves the message unread, so
the next poll offers it again and the Engine recognises it. A message the Engine cannot store is not
acknowledged: it is offered again, and reported each time, instead of vanishing.

Errors carry no address and no text: only fixed sentences and the error's type.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sales_core.contracts import InboundMessage
from sales_core.engine import Engine

log = logging.getLogger(__name__)

DEFAULT_BATCH = 50


class Fetched(Protocol):
    """One message from the channel, and the handle to acknowledge it with."""

    @property
    def handle(self) -> str: ...

    @property
    def message(self) -> InboundMessage: ...


class InboxPoller(Protocol):
    """One poll of one client's inbox: use it for a single run and close it."""

    def fetch(self, limit: int = ...) -> Sequence[Fetched]: ...

    def acknowledge(self, handle: str) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class InboxReport:
    """What one poll did. `errors` has the same meaning as an Engine run's: problems worth seeing,
    in fixed sentences."""

    received: int = 0
    errors: tuple[str, ...] = ()


def poll_inbox(engine: Engine, poller: InboxPoller, *, limit: int = DEFAULT_BATCH) -> InboxReport:
    """Store every unread message, acknowledge it, then let the Engine answer it.

    A failure to read the inbox (it cannot be reached, the login is refused) is raised, so the
    scheduler treats the job as failed and backs off. A failure on one message is reported and the
    others go on."""
    errors: list[str] = []
    received = 0
    for item in poller.fetch(limit):
        try:
            receipt = engine.receive_inbound(item.message)
        except Exception as error:  # noqa: BLE001 - one bad message must not hold back the rest
            errors.append(f"could not store a reply ({type(error).__name__})")
            continue  # not acknowledged: offered again on the next poll
        poller.acknowledge(item.handle)
        received += 1
        errors.extend(receipt.errors)
        try:
            errors.extend(engine.process_inbound(receipt).errors)
        except Exception as error:  # noqa: BLE001 - `recover_pending_replies` answers it later
            errors.append(f"could not process a reply ({type(error).__name__})")
    return InboxReport(received=received, errors=tuple(errors))
