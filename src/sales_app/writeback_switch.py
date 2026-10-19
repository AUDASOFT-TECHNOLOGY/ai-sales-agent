"""The platform's master switch for write-back (results copied into a client's Google Sheet or HubSpot).

Why a switch exists: write-back writes into a client's own files, so it must be possible to stop it
everywhere at once. Why it must not be a forgotten flag: the feature is part of Revansa, so its state
has to be a decision somebody can see and change.

- The **deployment default** is `SALES_AGENT_WRITEBACK_ENABLED` (on unless the deployment sets it to
  false).
- The **Admin's decision** (`PUT /admin/v1/settings/writeback`, owner only) overrides it and is kept
  in the credential store beside the other platform settings, audited (who, when; never a value that
  matters). Removing the decision returns to the deployment default.
- A change reaches every running client at once (the runtime is rebuilt), no restart.

Switching it on writes nothing by itself: a source is written to only when the client named columns
or properties for it one by one, the client is not in the global dry-run mode, and a database keeps
the attempts. `WritebackSupport` and the providers keep those guards.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from sales_app.config.secrets import WRITEBACK_ENABLED
from sales_app.secret_store import GLOBAL_SCOPE
from sales_app.secret_store.audit import SecretAudit, SecretAuditEvent
from sales_app.secret_store.port import SecretStore

log = logging.getLogger(__name__)

Source = Literal["admin", "deployment"]


class WritebackSwitch:
    def __init__(
        self,
        store: SecretStore | None,
        audit: SecretAudit | None,
        *,
        default: bool,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._default = default
        self._now = now or (lambda: datetime.now(UTC))
        self._on_change: Callable[[str, str], object] | None = None

    def __repr__(self) -> str:
        return "WritebackSwitch()"

    def set_on_change(self, callback: Callable[[str, str], object] | None) -> None:
        self._on_change = callback

    # ---- reading ---------------------------------------------------------------------------

    def _decision(self) -> bool | None:
        if self._store is None:
            return None
        try:
            value = self._store.get(GLOBAL_SCOPE, WRITEBACK_ENABLED)
        except Exception:  # noqa: BLE001 - an unreadable decision must not switch writing on
            return False
        if value is None:
            return None
        return value.strip().lower() == "true"

    def enabled(self) -> bool:
        decision = self._decision()
        return self._default if decision is None else decision

    def source(self) -> Source:
        return "deployment" if self._decision() is None else "admin"

    @property
    def deployment_default(self) -> bool:
        return self._default

    # ---- writing (the route that calls this checks the role) -------------------------------

    def set(self, enabled: bool | None, *, actor: str) -> None:
        """`True`/`False`: the Admin's decision. `None`: forget it and follow the deployment."""
        if self._store is None:
            raise RuntimeError("the switch needs the credential store")
        if enabled is None:
            removed = self._store.delete(GLOBAL_SCOPE, WRITEBACK_ENABLED)
            self._record(actor, "delete", "removed" if removed else "not_found")
            if not removed:
                return
        else:
            self._store.set(GLOBAL_SCOPE, WRITEBACK_ENABLED, "true" if enabled else "false", by=actor)
            self._record(actor, "set", "stored")
        if self._on_change is not None:
            try:
                self._on_change(GLOBAL_SCOPE, WRITEBACK_ENABLED)
            except Exception:
                log.exception("could not schedule applying the write-back switch")

    def _record(self, actor: str, action: str, outcome: str) -> None:
        if self._audit is not None:
            self._audit.record(
                SecretAuditEvent(self._now(), actor, action, GLOBAL_SCOPE, WRITEBACK_ENABLED, outcome)
            )
