"""What an authorised person may do with the consent registry: check, record, release.

The only module that may end a block (an architecture test enforces it): neither the Engine's side,
nor a source, nor the code that composes them can lift a refusal, because they are never handed
anything that can.

**Lifting a refusal changes nothing else.** `release` appends one event to the registry and stops
there. This service is given no prospect, no store and no source, so it cannot reactivate, resume
or edit anyone: a prospect already marked opted out, paused or closed stays so until a person changes
that on purpose, and a source that still carries a do-not-contact signal blocks the contact again
the next time its refusals are recorded.

A release needs, all at once: an active **owner** account and its password (checked again here,
whatever session the caller has), a reason code, an evidence reference, the exact confirmation
phrase, fewer than 5 releases by that account in the last 24 hours, and, if the refusal came from
the contact's own message, the reason `written_consent`. Every refusal to act is audited.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sales_app.admin_auth import AdminRole, AdminUserStore
from sales_app.admin_auth.passwords import hash_password, verify_password
from sales_app.secret_store import SecretAudit, SecretAuditEvent
from sales_app.sourcing.consent.anchor import consent_scope
from sales_app.sourcing.consent.errors import ConsentNotAllowedError
from sales_app.sourcing.consent.events import (
    ACTOR,
    OPT_OUT_REASONS,
    RELEASE_REASONS,
    EventType,
    Origin,
    ReasonCode,
    evidence_problem,
)
from sales_app.sourcing.consent.registry import ConsentRegistry, Health, InitResult, Report
from sales_core.contracts import ContactInfo

CONFIRMATION_PHRASE = "release opt-out"
RELEASE_QUOTA = 5
QUOTA_WINDOW = timedelta(hours=24)
RELEASE_NOTICE = (
    "The registry no longer blocks this contact. No prospect was changed: one already marked as "
    "opted out, paused or closed stays so until a person changes it. A source that still carries a "
    "do-not-contact signal will block the contact again."
)

_dummy_hash: str | None = None


def _comparison_hash() -> str:
    """Something to check a password against when the account does not exist, so that an unknown
    name costs the same as a wrong password."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("not-a-real-password")
    return _dummy_hash


@dataclass(frozen=True, slots=True)
class IdentityState:
    """One identity of the contact ('phone' or 'email'); never its value."""

    kind: str
    blocked: bool
    since: datetime | None
    origin: Origin | None
    reason: ReasonCode | None
    events: int


@dataclass(frozen=True, slots=True)
class CheckResult:
    report: Report
    identities: tuple[IdentityState, ...]

    @property
    def blocked(self) -> bool:
        """True if the contact is blocked, which includes every contact when the registry is not
        healthy."""
        return self.report.health is not Health.OK or any(i.blocked for i in self.identities)


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    released: int
    notice: str = RELEASE_NOTICE


class ConsentAdmin:
    def __init__(
        self,
        registry: ConsentRegistry,
        users: AdminUserStore,
        audit: SecretAudit,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._users = users
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"ConsentAdmin(client={self._registry.client_id!r})"

    # ---- reading -------------------------------------------------------------------------

    def status(self) -> Report:
        return self._registry.report()

    def check(self, contact: ContactInfo) -> CheckResult:
        report = self._registry.report()
        if report.health is not Health.OK:
            return CheckResult(report, ())
        history = self._registry.history(contact)
        identities = []
        for digest in self._registry.digests(contact):
            events = [e for e in history if e.key == digest]
            last = events[-1] if events else None
            identities.append(
                IdentityState(
                    kind=self._registry.identity_kind(contact, digest) or "contact",
                    blocked=last is not None and last.type is EventType.OPT_OUT,
                    since=last.at if last is not None else None,
                    origin=last.origin if last is not None else None,
                    reason=last.reason if last is not None else None,
                    events=len(events),
                )
            )
        return CheckResult(report, tuple(identities))

    # ---- changing ------------------------------------------------------------------------

    def initialise(self, *, actor: str) -> InitResult:
        return self._registry.initialise(actor=actor)

    def record(
        self, contact: ContactInfo, *, actor: str, reason: ReasonCode, evidence_ref: str = ""
    ) -> int:
        """Record a refusal a person was told of (a phone call, a letter). Returns how many
        identities are newly blocked."""
        if reason not in OPT_OUT_REASONS:
            raise self._refuse("invalid_reason")
        return self._registry.record_opt_out(
            contact,
            origin=Origin.OPERATOR,
            reason=reason,
            actor=actor,
            evidence_ref=evidence_ref,
        )

    def release(
        self,
        contact: ContactInfo,
        *,
        username: str,
        password: str,
        reason: ReasonCode,
        evidence_ref: str,
        confirmation: str,
    ) -> ReleaseResult:
        """End the block on a contact. See the module notes for everything that is required."""
        if reason not in RELEASE_REASONS:
            raise self._refuse("invalid_reason")
        if not evidence_ref.strip() or evidence_problem(evidence_ref) is not None:
            raise self._refuse("evidence_required")
        actor = self._authenticate_owner(username, password)
        if confirmation != CONFIRMATION_PHRASE:
            raise self._refuse("bad_confirmation")

        since = self._now() - QUOTA_WINDOW
        recent = [
            e
            for e in self._registry.events()
            if e.type is EventType.RELEASE and e.actor == actor and e.at >= since
        ]
        if len(recent) >= RELEASE_QUOTA:
            raise self._refuse("quota_exceeded")
        if reason is ReasonCode.REGISTRATION_ERROR and self._came_from_the_contact(contact):
            raise self._refuse("origin_rule")

        released = self._registry.append_release(
            contact, actor=actor, reason=reason, evidence_ref=evidence_ref
        )
        self._record("release", "released", None)
        return ReleaseResult(released)

    # ---- internals -----------------------------------------------------------------------

    def _came_from_the_contact(self, contact: ContactInfo) -> bool:
        """Whether a refusal that is still in force was the contact's own message."""
        blocked = {e.key: e for e in self._registry.history(contact)}  # the latest per identity
        return any(
            e.type is EventType.OPT_OUT and e.origin is Origin.INBOUND_KEYWORD
            for e in blocked.values()
        )

    def _authenticate_owner(self, username: str, password: str) -> str:
        """The audit actor of an active owner whose password is right. Any failure, whatever its
        cause (unknown name, wrong password, inactive, not an owner), is the same refusal."""
        user = self._users.get_by_username(username.strip().lower())
        right = verify_password(password, user.password_hash if user else _comparison_hash())
        if user is None or not user.active or user.role is not AdminRole.OWNER or not right:
            raise self._refuse("not_authorised")
        identity = user.id if ACTOR.fullmatch(f"admin:{user.id}") else None
        return f"admin:{identity or hashlib.sha256(user.id.encode()).hexdigest()[:32]}"

    def _refuse(self, code: str) -> ConsentNotAllowedError:
        """Audit a refusal to act and return the error to raise."""
        self._record("release_attempt", "rejected", code)
        return ConsentNotAllowedError(code)

    def _record(self, action: str, outcome: str, reason: str | None) -> None:
        self._audit.record(
            SecretAuditEvent(
                self._now(),
                "operator",
                action,
                consent_scope(self._registry.client_id),
                "release",
                outcome,
                reason,
            )
        )
