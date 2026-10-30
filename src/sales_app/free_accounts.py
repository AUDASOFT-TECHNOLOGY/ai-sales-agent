"""Free accounts: a client who signed up by itself and has not subscribed to a paid plan yet.

A Free account may discover the interface and prepare its agent (profile, products, circuits,
settings), nothing more. The 50 credits it is given (`FREE_CREDITS`) are for that preparation only:
they never unlock a commercial action. **A client is on the Free tier while it has a free-account
record and no current plan assignment**; assigning a paid plan (the Admin's existing flow) ends the
restriction without touching the record. Clients created before this existed have no record, so
nothing changes for them.

`CommercialAccess.blocked` is the one question every commercial entry point asks (the agent switch,
the scheduler, the channels, the manual send, the approvals). It raises whatever the stores raise:
callers treat that as "blocked" (fail closed).
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sales_app.plans import PlanStore

FREE_CREDITS = 50
UPGRADE_REQUIRED = "upgrade_required"
UPGRADE_MESSAGE = "Your free account lets you prepare your agent. Upgrade your plan to activate it."


@dataclass(frozen=True, slots=True)
class FreeAccount:
    client_id: str
    credits: int
    created_at: datetime


class FreeAccountStore(Protocol):
    def get(self, client_id: str) -> FreeAccount | None: ...

    def add(self, account: FreeAccount) -> None:
        """Keeps the record once: a client already holding one keeps its first (credits are
        granted a single time)."""


class InMemoryFreeAccountStore:
    def __init__(self) -> None:
        self._accounts: dict[str, FreeAccount] = {}
        self._lock = threading.Lock()

    def get(self, client_id: str) -> FreeAccount | None:
        with self._lock:
            return self._accounts.get(client_id)

    def add(self, account: FreeAccount) -> None:
        with self._lock:
            self._accounts.setdefault(account.client_id, account)


class CommercialAccess:
    """Who may act commercially. Stores are attributes so a deployment (or a test) can set them
    after the object was handed around."""

    def __init__(self, free: FreeAccountStore | None, plans: PlanStore | None) -> None:
        self.free = free
        self.plans = plans

    def __repr__(self) -> str:
        return "CommercialAccess()"

    def is_free(self, client_id: str) -> bool:
        if self.free is None or self.free.get(client_id) is None:
            return False
        return self.plans is None or self.plans.current_assignment(client_id) is None

    def blocked(self, client_id: str) -> bool:
        return self.is_free(client_id)
