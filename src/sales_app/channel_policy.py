"""Reading and changing which channels clients may use. Only an administrator calls the writes.

The values live in the credential store (encrypted, audited) under two keys that are *not* in its
catalog: they are not credentials, and must not show up as ones in the Admin's credential screens.
What they mean, and how a client's own configuration is judged against them, is
`sales_app.config.channel_access`.

Every write is audited (who, which scope, which key; never a value) and handed to the runtime, so a
client that loses a channel stops using it at once instead of at the next restart.
"""

import logging
from collections.abc import Callable, Collection
from datetime import UTC, datetime

from sales_app.config.channel_access import (
    ACCESS_PATTERN,
    ALLOWED_PATTERN,
    effective_channels,
    format_access,
    format_allowed,
    parse_access,
    parse_allowed,
)
from sales_app.config.secrets import CHANNELS_ACCESS, CHANNELS_ALLOWED
from sales_app.secret_store import GLOBAL_SCOPE, client_scope
from sales_app.secret_store.audit import SecretAudit, SecretAuditEvent
from sales_app.secret_store.port import SecretStore

log = logging.getLogger(__name__)


class InvalidChannelPolicyError(ValueError):
    """A policy that names a channel Revansa does not know. The message is a fixed sentence."""


class ChannelPolicyService:
    def __init__(
        self,
        store: SecretStore,
        audit: SecretAudit,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))
        self._on_change: Callable[[str, str], object] | None = None

    def set_on_change(self, callback: Callable[[str, str], object] | None) -> None:
        """Called with (scope, key) after a change, so the runtime applies it. It only schedules
        work; a failure in it never undoes the change."""
        self._on_change = callback

    # ---- reading ---------------------------------------------------------------------------

    def global_allowed(self) -> frozenset[str]:
        return parse_allowed(self._store.get(GLOBAL_SCOPE, CHANNELS_ALLOWED))

    def client_access(self, client_id: str) -> tuple[frozenset[str], frozenset[str]]:
        """(added, removed): the exceptions held for this client."""
        return parse_access(self._store.get(client_scope(client_id), CHANNELS_ACCESS))

    def effective(self, client_id: str) -> frozenset[str]:
        """What this client may use now: the global list, with its exceptions applied."""
        return effective_channels(
            self._store.get(GLOBAL_SCOPE, CHANNELS_ALLOWED),
            self._store.get(client_scope(client_id), CHANNELS_ACCESS),
        )

    # ---- writing (administrators only: the routes that call these check the role) -----------

    def set_global(self, channels: Collection[str], *, actor: str, known: Collection[str]) -> None:
        _require_known(channels, known)
        text = format_allowed(channels)
        assert ALLOWED_PATTERN.match(text)
        self._store.set(GLOBAL_SCOPE, CHANNELS_ALLOWED, text, by=actor)
        self._record(actor, "set", GLOBAL_SCOPE, CHANNELS_ALLOWED, "stored")
        self._changed(GLOBAL_SCOPE, CHANNELS_ALLOWED)

    def set_client_access(
        self,
        client_id: str,
        added: Collection[str],
        removed: Collection[str],
        *,
        actor: str,
        known: Collection[str],
    ) -> None:
        _require_known([*added, *removed], known)
        scope = client_scope(client_id)
        text = format_access(added, removed)
        if text is None:
            removed_something = self._store.delete(scope, CHANNELS_ACCESS)
            self._record(
                actor,
                "delete",
                scope,
                CHANNELS_ACCESS,
                "removed" if removed_something else "not_found",
            )
            if removed_something:
                self._changed(scope, CHANNELS_ACCESS)
            return
        assert ACCESS_PATTERN.match(text)
        self._store.set(scope, CHANNELS_ACCESS, text, by=actor)
        self._record(actor, "set", scope, CHANNELS_ACCESS, "stored")
        self._changed(scope, CHANNELS_ACCESS)

    # ---- internals -------------------------------------------------------------------------

    def _record(self, actor: str, action: str, scope: str, key: str, outcome: str) -> None:
        self._audit.record(SecretAuditEvent(self._now(), actor, action, scope, key, outcome))

    def _changed(self, scope: str, key: str) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(scope, key)
        except Exception:
            log.exception("could not schedule applying a changed channel policy")


def _require_known(channels: Collection[str], known: Collection[str]) -> None:
    if any(channel not in known for channel in channels):
        raise InvalidChannelPolicyError("a channel Revansa does not know")
