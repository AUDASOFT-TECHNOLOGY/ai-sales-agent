"""The consent registry of one client: who must never be contacted, tamper-evident.

An opted-out contact is one whose latest event is an `opt_out`; a `release` ends it. A contact is
stored only as a keyed digest (see `keys`).

**Fail closed.** Whatever is wrong with the registry (never set up, key or anchor missing, a record
changed, the log truncated, the store or the log unreadable, anything unexpected), `health` is not
`OK` and `blocks` answers True for every contact. The code that composes a source and a store
treats a blocked contact as opted out, which the Core's own safety check then refuses to contact.
Nothing is ever guessed or repaired silently.

**What it does not protect against.** It reveals edits to the log by anyone who does not hold the
key. It does not protect against an attacker who can restore, together and consistently, the log
and the secret store that holds the key and the anchor.

The module exposes two doors: `ConsentWriter` (what the Engine's side and the sources need: read the
blocked set, record an opt-out) and `ConsentRegistry.append_release`, which only the operator
service (`admin`) may use; an architecture test keeps it that way. Lifting a block here changes no
prospect: this module knows no prospect.
"""

import hmac
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sales_app.sourcing.consent.anchor import Anchor, ConsentAnchorFormatError, ConsentSecrets
from sales_app.sourcing.consent.errors import (
    ConsentConflictError,
    ConsentFormatError,
    ConsentKeyError,
    ConsentLockTimeoutError,
    ConsentLogUnavailableError,
    ConsentRefusedError,
    ConsentStoreUnavailableError,
    ConsentUnavailableError,
)
from sales_app.sourcing.consent.events import (
    ConsentEvent,
    EventType,
    Origin,
    ReasonCode,
    sign_event,
    verify_chain,
)
from sales_app.sourcing.consent.keys import ConsentKeys, new_root_key
from sales_app.sourcing.consent.log import ConsentLog
from sales_core.contracts import ContactInfo


class Health(StrEnum):
    OK = "ok"
    NOT_INITIALISED = "not_initialised"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


class InitResult(StrEnum):
    CREATED = "created"
    COMPLETED = "completed"  # a key existed from an interrupted set-up: only the anchor was added
    ALREADY_INITIALISED = "already_initialised"


@dataclass(frozen=True, slots=True)
class Report:
    """What an operator may see about a registry. Never a contact, a key or a digest."""

    health: Health
    reason: str | None  # a fixed code
    events: int = 0
    blocked: int = 0  # contact identities currently blocked
    releases: int = 0
    torn_tail: bool = False  # an unfinished last line was found (it is cleaned at the next write)
    anchor_behind: bool = False  # the anchor trails valid events (it catches up at the next write)


@dataclass(frozen=True, slots=True)
class _Loaded:
    health: Health
    reason: str | None
    keys: ConsentKeys | None = None
    events: tuple[ConsentEvent, ...] = ()
    blocked: frozenset[str] = frozenset()
    torn_tail: bool = False
    anchor_behind: bool = False


class RegistrySnapshot:
    """The blocked set at one moment, verified once. Cheap to ask about many contacts."""

    __slots__ = ("_blocked", "_keys", "health", "reason")

    def __init__(self, loaded: _Loaded) -> None:
        self.health = loaded.health
        self.reason = loaded.reason
        self._keys = loaded.keys
        self._blocked = loaded.blocked

    @property
    def healthy(self) -> bool:
        return self.health is Health.OK

    def blocks(self, contact: ContactInfo) -> bool:
        """True if the contact must not be contacted. True for everyone if the registry is not OK."""
        if self.health is not Health.OK or self._keys is None:
            return True
        return any(digest in self._blocked for digest in self._keys.digests(contact))

    def __repr__(self) -> str:
        return f"RegistrySnapshot(health={self.health.value})"


@runtime_checkable
class ConsentWriter(Protocol):
    """What the rest of the application may do with the registry. There is no way to lift a block."""

    def snapshot(self) -> RegistrySnapshot: ...

    def record_opt_out(
        self,
        contact: ContactInfo,
        *,
        origin: Origin,
        reason: ReasonCode,
        actor: str,
        evidence_ref: str = "",
    ) -> int: ...


def _corrupt(reason: str) -> _Loaded:
    return _Loaded(Health.CORRUPT, reason)


def _unavailable(reason: str) -> _Loaded:
    return _Loaded(Health.UNAVAILABLE, reason)


MAX_WRITE_ATTEMPTS = 8  # a write that keeps losing its position this many times is reported


class ConsentRegistry:
    def __init__(
        self,
        client_id: str,
        log: ConsentLog,
        secrets: ConsentSecrets,
        *,
        now: Callable[[], datetime] | None = None,
        new_event_id: Callable[[], str] | None = None,
    ) -> None:
        self._client_id = client_id
        self._log = log
        self._secrets = secrets
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_event_id or (lambda: uuid.uuid4().hex)

    @property
    def client_id(self) -> str:
        return self._client_id

    def __repr__(self) -> str:
        return f"ConsentRegistry(client={self._client_id!r})"

    # ---- reading -------------------------------------------------------------------------

    def snapshot(self) -> RegistrySnapshot:
        return RegistrySnapshot(self._load())

    def report(self) -> Report:
        loaded = self._load()
        return Report(
            health=loaded.health,
            reason=loaded.reason,
            events=len(loaded.events),
            blocked=len(loaded.blocked),
            releases=sum(1 for e in loaded.events if e.type is EventType.RELEASE),
            torn_tail=loaded.torn_tail,
            anchor_behind=loaded.anchor_behind,
        )

    def history(self, contact: ContactInfo) -> tuple[ConsentEvent, ...]:
        """The events of this contact's identities, oldest first. Needs a healthy registry."""
        with self._exclusive():
            loaded = self._require_ok(self._load())
            wanted = set(loaded.keys.digests(contact))  # type: ignore[union-attr]
            return tuple(e for e in loaded.events if e.key in wanted)

    def digests(self, contact: ContactInfo) -> tuple[str, ...]:
        """The digests of a contact's identities (opaque). Needs a healthy registry."""
        with self._exclusive():
            return self._require_ok(self._load()).keys.digests(contact)  # type: ignore[union-attr]

    def events(self) -> tuple[ConsentEvent, ...]:
        """Every event, oldest first. Needs a healthy registry."""
        with self._exclusive():
            return self._require_ok(self._load()).events

    def identity_kind(self, contact: ContactInfo, digest: str) -> str | None:
        """'phone' or 'email': which identity of `contact` a digest is. Never the value."""
        with self._exclusive():
            loaded = self._require_ok(self._load())
            return loaded.keys.kind_of(digest, contact)  # type: ignore[union-attr]

    # ---- setting up ----------------------------------------------------------------------

    def initialise(self, *, actor: str) -> InitResult:
        """Explicit, and safe to repeat. Creates the key and the anchor of a registry that does not
        exist; leaves a healthy one alone; **refuses** to touch one that exists but is not healthy.
        It never makes a new key when anything of the registry is already there."""
        with self._exclusive():
            loaded = self._load()
            if loaded.health is Health.OK:
                return InitResult.ALREADY_INITIALISED
            if loaded.health is Health.UNAVAILABLE:
                raise ConsentUnavailableError(loaded.health.value, loaded.reason or "unknown")
            if loaded.health is not Health.NOT_INITIALISED:
                raise ConsentRefusedError(loaded.reason or loaded.health.value)
            if loaded.keys is not None:  # a set-up that stopped after the key
                self._secrets.set_anchor(Anchor(0, loaded.keys.genesis_mac()), actor=actor)
                return InitResult.COMPLETED
            if self._secrets.get_root_key() is not None:  # appeared since: never overwrite it
                raise ConsentRefusedError("key_present")
            text = new_root_key()
            self._secrets.create_root_key(text, actor=actor)
            keys = ConsentKeys.from_stored(text, self._client_id)
            self._secrets.set_anchor(Anchor(0, keys.genesis_mac()), actor=actor)
            return InitResult.CREATED

    # ---- writing -------------------------------------------------------------------------

    def record_opt_out(
        self,
        contact: ContactInfo,
        *,
        origin: Origin,
        reason: ReasonCode,
        actor: str,
        evidence_ref: str = "",
    ) -> int:
        """Block this contact. Idempotent: an identity already blocked adds nothing. Returns how
        many identities were newly blocked. Raises `ConsentUnavailableError` if the registry is not
        healthy (everything is blocked anyway)."""

        def write(loaded: _Loaded) -> int:
            fresh = [d for d in loaded.keys.digests(contact) if d not in loaded.blocked]  # type: ignore[union-attr]
            return self._append(
                loaded, fresh, EventType.OPT_OUT, origin, reason, actor, evidence_ref
            )

        return self._write_retrying(write)

    def append_release(
        self,
        contact: ContactInfo,
        *,
        actor: str,
        reason: ReasonCode,
        evidence_ref: str,
    ) -> int:
        """End the block on this contact's identities that are blocked. **Operator service only.**
        Returns how many were released. Changes nothing else: no prospect, no status."""

        def write(loaded: _Loaded) -> int:
            held = [d for d in loaded.keys.digests(contact) if d in loaded.blocked]  # type: ignore[union-attr]
            return self._append(
                loaded, held, EventType.RELEASE, Origin.OPERATOR, reason, actor, evidence_ref
            )

        return self._write_retrying(write)

    # ---- internals -----------------------------------------------------------------------

    def _write_retrying(self, write: Callable[["_Loaded"], int]) -> int:
        """Run a read-verify-append, again from a fresh read when another writer took the position.

        A log kept where several processes write (the database) can move on between the read and
        the append; the append then refuses and writes nothing. Retrying starts over from what the
        log holds now, so nothing is lost, nothing is written twice (an identity another writer has
        already blocked is no longer "fresh") and events keep the order they were accepted in. The
        number of attempts is bounded: past it the conflict is reported, never swallowed."""
        written = 0
        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                with self._exclusive():
                    return written + write(self._require_ok(self._load()))
            except ConsentConflictError as conflict:
                written += getattr(conflict, "written", 0)  # events this attempt did append
                if attempt == MAX_WRITE_ATTEMPTS:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """The log's exclusive lock, where failing to get it means the registry is unavailable
        (every contact stays blocked) rather than a raw lock error."""
        with ExitStack() as stack:
            try:
                stack.enter_context(self._log.exclusive())
            except ConsentLockTimeoutError:
                raise ConsentUnavailableError(Health.UNAVAILABLE.value, "lock_timeout") from None
            except ConsentLogUnavailableError:
                raise ConsentUnavailableError(Health.UNAVAILABLE.value, "log_unreadable") from None
            yield

    @staticmethod
    def _require_ok(loaded: _Loaded) -> _Loaded:
        if loaded.health is not Health.OK or loaded.keys is None:
            raise ConsentUnavailableError(loaded.health.value, loaded.reason or "unknown")
        return loaded

    def _append(
        self,
        loaded: _Loaded,
        digests: list[str],
        kind: EventType,
        origin: Origin,
        reason: ReasonCode,
        actor: str,
        evidence_ref: str,
    ) -> int:
        keys = loaded.keys
        assert keys is not None
        last_seq = len(loaded.events)
        previous = loaded.events[-1].mac if loaded.events else keys.genesis_mac()
        written = 0
        for digest in digests:
            event = sign_event(
                keys,
                seq=last_seq + 1,
                key=digest,
                type=kind,
                origin=origin,
                at=self._now(),
                actor=actor,
                reason=reason,
                evidence_ref=evidence_ref,
                prev_mac=previous,
                event_id=self._new_id(),
            )
            try:
                self._log.append(event, expected_last_seq=last_seq)  # the event first...
            except ConsentConflictError as conflict:
                conflict.written = written  # type: ignore[attr-defined]
                raise
            self._secrets.set_anchor(
                Anchor(event.seq, event.mac), actor=actor
            )  # ...then the anchor
            last_seq, previous = event.seq, event.mac
            written += 1
        return written

    def _load(self) -> _Loaded:
        try:
            with self._log.exclusive():
                return self._load_locked()
        except ConsentLockTimeoutError:
            return _unavailable("lock_timeout")
        except ConsentLogUnavailableError:
            return _unavailable("log_unreadable")
        except ConsentStoreUnavailableError:
            return _unavailable("store_unavailable")
        except ConsentFormatError:
            return _corrupt("format_invalid")
        except ConsentKeyError:
            return _corrupt("key_invalid")
        except ConsentAnchorFormatError:
            return _corrupt("anchor_invalid")
        except Exception:  # noqa: BLE001 - whatever it is, it must block, never let anyone through
            return _unavailable("internal_error")

    def _load_locked(self) -> _Loaded:
        text = self._secrets.get_root_key()
        anchor = self._secrets.get_anchor()
        contents = self._log.read()
        events = contents.events
        has_log_data = bool(events) or contents.torn_tail

        if text is None:
            if anchor is None and not has_log_data:
                return _Loaded(Health.NOT_INITIALISED, "not_initialised")
            return _corrupt("key_missing")
        keys = ConsentKeys.from_stored(text, self._client_id)

        if anchor is None:
            if not has_log_data:
                return _Loaded(Health.NOT_INITIALISED, "init_incomplete", keys=keys)
            return _corrupt("anchor_missing")

        chain = verify_chain(keys, events)
        if not chain.ok:
            return _corrupt(chain.reason or "chain_broken")
        if anchor.seq > len(events):
            return _corrupt("anchor_ahead")
        expected = keys.genesis_mac() if anchor.seq == 0 else events[anchor.seq - 1].mac
        if not hmac.compare_digest(anchor.mac, expected):
            return _corrupt("anchor_mismatch")

        state: dict[str, EventType] = {}
        for event in events:
            state[event.key] = event.type
        blocked = frozenset(k for k, kind in state.items() if kind is EventType.OPT_OUT)
        return _Loaded(
            Health.OK,
            None,
            keys=keys,
            events=events,
            blocked=blocked,
            torn_tail=contents.torn_tail,
            anchor_behind=anchor.seq < len(events),
        )
