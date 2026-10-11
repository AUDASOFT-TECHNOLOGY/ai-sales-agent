"""The root key and the integrity anchor, kept in the credential store.

Both live in a scope of their own, `consent:<client_id>`, written straight through the `SecretStore`
port rather than through the credential catalog. The catalog is the Admin's list of credentials: a
key added there would show in the Admin API's credential listings and would have to be declared by a
provider. These two are internal, so they stay out of it. Every write is recorded in the secret
audit (who, what, when; never the value).

The anchor is `<seq>.<mac>` of the last event: where the log must end. Writing it after the event,
never before, means a crash leaves it behind (harmless) and never ahead (which would look like a
truncated log).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.secret_store import (
    SecretAudit,
    SecretAuditEvent,
    SecretStore,
    SecretStoreError,
    SecretStoreUnavailableError,
)
from sales_app.sourcing.consent.errors import ConsentError, ConsentStoreUnavailableError

KEY_NAME = "hmac_key"
ANCHOR_NAME = "anchor"
_ANCHOR = re.compile(r"(\d{1,12})\.([A-Za-z0-9_-]{43})")


class ConsentAnchorFormatError(ConsentError):
    """The stored anchor is malformed."""


def consent_scope(client_id: str) -> str:
    if not CLIENT_ID_PATTERN.fullmatch(client_id):
        raise ValueError("invalid client id")
    return f"consent:{client_id}"


@dataclass(frozen=True, slots=True)
class Anchor:
    seq: int
    mac: str


class ConsentSecrets:
    def __init__(
        self,
        store: SecretStore,
        audit: SecretAudit,
        client_id: str,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._scope = consent_scope(client_id)
        self._now = now or (lambda: datetime.now(UTC))

    def get_root_key(self) -> str | None:
        """The stored key text, or None. A store that cannot be read is `ConsentStoreUnavailable`."""
        return self._read(KEY_NAME)  # a malformed one is reported by the caller's parse

    def create_root_key(self, value: str, *, actor: str) -> None:
        self._write(KEY_NAME, value, actor)

    def get_anchor(self) -> Anchor | None:
        value = self._read(ANCHOR_NAME)
        if value is None:
            return None
        found = _ANCHOR.fullmatch(value)
        if found is None:
            raise ConsentAnchorFormatError("the consent anchor is malformed")
        return Anchor(int(found.group(1)), found.group(2))

    def set_anchor(self, anchor: Anchor, *, actor: str) -> None:
        self._write(ANCHOR_NAME, f"{anchor.seq}.{anchor.mac}", actor)

    # ---- internals -----------------------------------------------------------------------

    def _read(self, name: str) -> str | None:
        try:
            return self._store.get(self._scope, name)
        except SecretStoreError:
            raise ConsentStoreUnavailableError("the secret store cannot be read") from None

    def _write(self, name: str, value: str, actor: str) -> None:
        try:
            self._store.set(self._scope, name, value, by=actor)
        except SecretStoreUnavailableError:
            self._record(actor, name, "rejected", "unavailable")
            raise ConsentStoreUnavailableError("the secret store cannot be written") from None
        except SecretStoreError:
            self._record(actor, name, "rejected", "store_error")
            raise ConsentStoreUnavailableError("the secret store cannot be written") from None
        self._record(actor, name, "stored", None)

    def _record(self, actor: str, name: str, outcome: str, reason: str | None) -> None:
        self._audit.record(
            SecretAuditEvent(self._now(), actor, "set", self._scope, name, outcome, reason)
        )
