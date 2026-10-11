"""Builders shared by the consent registry tests. Invented, client-neutral data only."""

import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore
from sales_app.sourcing.consent import (
    ConsentRegistry,
    ConsentSecrets,
    FileConsentLog,
    InMemoryConsentLog,
    Origin,
    ReasonCode,
)
from sales_app.sourcing.consent.anchor import consent_scope
from sales_core.contracts import ContactInfo

_EVENT_IDS = itertools.count(1)  # shared, so two registries never mint the same id

CLIENT = "client-a"
OTHER = "client-b"
START = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

# Distinctive values, so a leak anywhere is easy to find: the number in three spellings, an address.
PHONE = "+216 99 888 777"
PHONE_DIGITS = "21699888777"
PHONE_SPELLED = "(216) 99-888-777"
EMAIL = "leak.me@example.com"
SECOND_PHONE = "+216 99 111 222"
THIRD_PHONE = "+216 99 333 444"
LEAKY = (PHONE, PHONE_DIGITS, PHONE_SPELLED, EMAIL, "leak.me", "99 888 777", "99-888-777")


class Clock:
    """Time that only moves when told to."""

    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def contact(phone: str | None = PHONE, email: str | None = None) -> ContactInfo:
    return ContactInfo(phone=phone, email=email)


@dataclass
class World:
    """One client's registry with everything it stands on, within reach of a test."""

    registry: ConsentRegistry
    log: FileConsentLog | InMemoryConsentLog
    store: InMemorySecretStore
    audit: InMemorySecretAudit
    secrets: ConsentSecrets
    clock: Clock
    client_id: str = CLIENT
    ids: list[str] = field(default_factory=list)

    @property
    def scope(self) -> str:
        return consent_scope(self.client_id)

    def block(self, who: ContactInfo | None = None, **kw) -> int:
        return self.registry.record_opt_out(
            who or contact(),
            origin=kw.pop("origin", Origin.OPERATOR),
            reason=kw.pop("reason", ReasonCode.OPERATOR_REQUEST),
            actor=kw.pop("actor", "cli:tester"),
            **kw,
        )

    def line_texts(self) -> list[str]:
        assert isinstance(self.log, FileConsentLog)
        if not self.log.path.exists():
            return []
        return self.log.path.read_text(encoding="utf-8").splitlines()

    def rewrite(self, lines: list[str], *, newline: bool = True) -> None:
        assert isinstance(self.log, FileConsentLog)
        text = "\n".join(lines) + ("\n" if newline and lines else "")
        self.log.path.write_text(text, encoding="utf-8", newline="")

    def edit_line(self, index: int, **changes) -> None:
        lines = self.line_texts()
        data = json.loads(lines[index])
        data.update(changes)
        lines[index] = json.dumps(data, sort_keys=True, separators=(",", ":"))
        self.rewrite(lines)


def make_world(
    tmp_path: Path | None = None,
    *,
    client_id: str = CLIENT,
    store: InMemorySecretStore | None = None,
    audit: InMemorySecretAudit | None = None,
    log: FileConsentLog | InMemoryConsentLog | None = None,
    clock: Clock | None = None,
    initialise: bool = True,
    lock_timeout: float = 5.0,
) -> World:
    clock = clock or Clock()
    store = store or InMemorySecretStore()
    audit = audit or InMemorySecretAudit()
    if log is None:
        log = (
            FileConsentLog(tmp_path / "consent" / f"{client_id}.jsonl", lock_timeout=lock_timeout)
            if tmp_path is not None
            else InMemoryConsentLog()
        )
    secrets = ConsentSecrets(store, audit, client_id, now=clock)
    registry = ConsentRegistry(
        client_id, log, secrets, now=clock, new_event_id=lambda: f"{next(_EVENT_IDS):032x}"
    )
    world = World(registry, log, store, audit, secrets, clock, client_id)
    if initialise:
        registry.initialise(actor="cli:tester")
    return world
