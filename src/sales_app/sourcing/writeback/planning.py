"""Which statuses to write back into the source: a pure plan, no I/O, no ledger, no source call.

The plan compares what Revansa wants a prospect's status to be with what the source holds, and keeps
only the writes that **tighten** the source: it never loosens a restriction and never overwrites a
status that is already as strict or stricter, whoever set it (a person may have, in between).

What Revansa wants is its own restrictive status (a pause, a closure, an opt-out), or an opt-out the
consent registry holds for the contact. A contactable status (`NEW`, `ACTIVE`) is never written: a
CRM cannot say that Revansa is in touch, and writing `NEW` would only loosen. An opt-out is written
only if the operator chose to propagate it, and only while the registry is healthy: an unhealthy
registry blocks everyone, which is no reason to mark every record of a CRM as refusing contact.

The plan carries ids and statuses and counts of what was left alone. Never a name, a contact or a
value the source holds apart from its status.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from sales_app.sourcing.errors import InvalidNativeIdError
from sales_app.sourcing.fields import ProspectField
from sales_app.sourcing.identifiers import to_public_id
from sales_app.sourcing.ports import ProspectChange, ProspectState, SourceCapabilities
from sales_app.sourcing.statuses import is_restrictive, severity
from sales_app.sourcing.writeback.policy import WriteBackPolicy
from sales_core.contracts import ContactInfo, Prospect, ProspectId, ProspectStatus


class SkipReason(StrEnum):
    """Why a record gives no write. Counted, never attached to a record."""

    NOTHING_TO_WRITE = "nothing_to_write"  # Revansa holds no restriction to pass on
    OPT_OUT_NOT_PROPAGATED = "opt_out_not_propagated"  # an opt-out the operator did not ask for
    ALREADY_APPLIED = "already_applied"  # the source already holds this status
    NOT_TIGHTER = "not_tighter"  # the source is already as strict, or stricter
    INVALID_ID = "invalid_id"  # the id cannot be named inside Revansa


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    """One status to write: `expected` is what the source held when it was read for this plan."""

    prospect_id: str  # the source's native id
    public_id: str
    new: ProspectStatus
    expected: ProspectStatus

    def to_change(self) -> ProspectChange:
        return ProspectChange(
            prospect_id=self.prospect_id,
            new={ProspectField.STATUS: self.new},
            expected={ProspectField.STATUS: self.expected},
        )


@dataclass(frozen=True, slots=True)
class WriteBackPlan:
    writes: tuple[PlannedWrite, ...] = ()
    skipped: Mapping[SkipReason, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "writes", tuple(self.writes))
        object.__setattr__(self, "skipped", MappingProxyType(dict(self.skipped)))

    @property
    def changes(self) -> tuple[ProspectChange, ...]:
        return tuple(write.to_change() for write in self.writes)


def wanted_status(
    revansa: ProspectStatus | None,
    *,
    registry_blocks: bool,
    registry_healthy: bool,
    policy: WriteBackPolicy,
) -> tuple[ProspectStatus | None, SkipReason | None]:
    """The status Revansa wants passed on to the source, or why none.

    `revansa` is the status in Revansa's own state for the prospect (None if it never set one).
    """
    candidates: list[ProspectStatus] = []
    if revansa is not None and is_restrictive(revansa):
        candidates.append(revansa)
    if registry_blocks:
        candidates.append(ProspectStatus.OPTED_OUT)
    if not candidates:
        return None, SkipReason.NOTHING_TO_WRITE
    strictest = max(candidates, key=severity)
    if strictest is ProspectStatus.OPTED_OUT and not (
        policy.propagate_opt_out and registry_healthy
    ):
        # Not replaced by a lesser status: Revansa would be saying something it did not decide.
        return None, SkipReason.OPT_OUT_NOT_PROPAGATED
    return strictest, None


def plan_status_writes(
    records: Sequence[Prospect],
    states: Mapping[ProspectId, ProspectState],
    *,
    capabilities: SourceCapabilities,
    policy: WriteBackPolicy,
    is_blocked: Callable[[ContactInfo], bool],
    registry_healthy: bool,
) -> WriteBackPlan:
    """The status writes to send to a source, from what it was read as (`records`, native ids).

    `states` is Revansa's own state, by public id. `is_blocked` answers for a contact (the consent
    registry); it is only consulted when opt-outs are propagated, so a registry that is not
    consulted is not read. Raises `WriteBackPolicyError` if the policy does not fit the source.
    """
    policy.validate_for(capabilities)
    if not policy.allows(ProspectField.STATUS):
        return WriteBackPlan()
    consult_registry = policy.propagate_opt_out and registry_healthy
    writes: list[PlannedWrite] = []
    skipped: dict[SkipReason, int] = {}

    def skip(reason: SkipReason) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for record in records:
        try:
            public = to_public_id(capabilities.id_namespace, record.prospect_id)
        except InvalidNativeIdError:
            skip(SkipReason.INVALID_ID)
            continue
        state = states.get(ProspectId(public))
        wanted, reason = wanted_status(
            state.status if state is not None else None,
            registry_blocks=consult_registry and is_blocked(record.contact),
            registry_healthy=registry_healthy,
            policy=policy,
        )
        if wanted is None:
            skip(reason or SkipReason.NOTHING_TO_WRITE)
            continue
        current = record.status
        if current is wanted:
            skip(SkipReason.ALREADY_APPLIED)
        elif severity(wanted) <= severity(current):
            skip(SkipReason.NOT_TIGHTER)
        else:
            writes.append(PlannedWrite(record.prospect_id, public, wanted, current))
    return WriteBackPlan(tuple(writes), skipped)
