"""A Telegram account that exists only in memory, for tests (`tests/` only: not a shipped fake). Nothing here talks to Telegram.

It implements `sales_integrations.telegram.account.AccountClient` and records what it was asked, so
a test can say exactly what would have left, what was read, and for whom. Failures are scripted per
operation. It stands for Telegram's behaviour as the code understands it; it proves nothing about
the real service.
"""

from collections import deque
from datetime import UTC, datetime

from sales_integrations.telegram.account import (
    Accept,
    AccountFailure,
    Batch,
    Identity,
    Incoming,
    LoginTicket,
    PasswordNeeded,
    Sent,
    SignedIn,
)


class FakeAccountClient:
    """One account. `people` maps a phone number to the account id that holds it."""

    CODE = "12345"
    PASSWORD = "two-step-password"

    def __init__(
        self,
        *,
        user_id: str = "900001",
        name: str = "Client Owner",
        people: dict[str, str] | None = None,
        two_step: bool = False,
        session: str | None = None,
    ) -> None:
        self.identity = Identity(user_id, name)
        self.people = dict(people or {})
        self.two_step = two_step
        self.session = session
        self.sent: list[tuple[str, str]] = []  # (peer, text)
        self.requested: list[str] = []  # phones a code was asked for
        self.lookups: list[str] = []  # phones looked up
        self.reads: list[int] = []  # `after_id` of each read
        self.logged_out = False
        self.inbox: list[Incoming] = []  # what the account received, by message id
        self.newest = 0
        self._failures: dict[str, deque[AccountFailure]] = {}
        self._next_id = 1000

    # ---- scripting ----------------------------------------------------------------------------

    def fail(self, operation: str, kind: str, *, retry_after: int | None = None, times: int = 1):
        queue = self._failures.setdefault(operation, deque())
        queue.extend(AccountFailure(kind, retry_after=retry_after) for _ in range(times))

    def receive(self, sender: str, text: str, *, message_id: int, phone: str | None = None):
        self.inbox.append(
            Incoming(
                sender_id=sender,
                message_id=message_id,
                text=text,
                at=datetime(2026, 10, 19, 9, 0, tzinfo=UTC),
                name=f"Person {sender}",
                phone=phone,
            )
        )
        self.newest = max(self.newest, message_id)

    def _check(self, operation: str) -> None:
        queue = self._failures.get(operation)
        if queue:
            raise queue.popleft()

    # ---- AccountClient ------------------------------------------------------------------------

    def request_code(self, phone: str) -> LoginTicket:
        self._check("request_code")
        self.requested.append(phone)
        return LoginTicket(phone_code_hash="hash-1", session="temporary-session")

    def sign_in_code(self, ticket: LoginTicket, phone: str, code: str) -> SignedIn:
        self._check("sign_in_code")
        if code != self.CODE:
            raise AccountFailure("bad_code")
        if self.two_step:
            raise PasswordNeeded(LoginTicket(ticket.phone_code_hash, "half-signed-session"))
        return SignedIn(session=f"session-of-{self.identity.user_id}", identity=self.identity)

    def sign_in_password(self, ticket: LoginTicket, password: str) -> SignedIn:
        self._check("sign_in_password")
        if password != self.PASSWORD:
            raise AccountFailure("bad_password")
        return SignedIn(session=f"session-of-{self.identity.user_id}", identity=self.identity)

    def me(self) -> Identity:
        self._check("me")
        return self.identity

    def send_text(self, peer: str, text: str) -> Sent:
        self._check("send_text")
        self.sent.append((peer, text))
        self._next_id += 1
        return Sent(message_id=self._next_id)

    def resolve_phone(self, phone: str, name: str) -> str | None:
        self._check("resolve_phone")
        self.lookups.append(phone)
        return self.people.get(phone)

    def newest_message_id(self) -> int:
        self._check("newest_message_id")
        return self.newest

    def fetch_incoming(self, after_id: int, limit: int, accept: Accept) -> Batch:
        self._check("fetch_incoming")
        self.reads.append(after_id)
        mine = [
            m
            for m in sorted(self.inbox, key=lambda m: m.message_id)
            if m.message_id > after_id and accept(m.sender_id, m.phone)
        ]
        return Batch(messages=mine[:limit], scanned_to=self.newest, truncated=len(mine) > limit)

    def log_out(self) -> None:
        self._check("log_out")
        self.logged_out = True
