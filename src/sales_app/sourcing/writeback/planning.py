"""Which statuses to write back into the source: a pure plan, no I/O, no ledger, no source call.

The plan compares what Revansa wants a prospect's status to be with what the source holds, and keeps
only the writes that **tighten** the source: it never loosens a restriction and never overwrites a
status that is already as strict or stricter, whoever set it (a person may have, in between).

What Revansa wants is its own restrictive status (a pause, a closure, an opt-out), or an opt-out the
consent registry holds for the contact. A contactable status (`NEW`, `ACTIVE`) is never written: a
CRM cannot say that Revansa is in touch, and writing `NEW` would only loosen. An opt-out is written
only if the operator chose to propagate it, and only while the registry is healthy: an unhealthy
registry blocks everyone, which is no reason to mark every record of a CRM as refusing contact.

Four more fields may be copied into a spreadsheet's own columns (`plan_field_writes`). Each has a rule
that never overwrites what a person put there:

- **Last contact, last reply**: written only if the cell is empty or holds a strictly earlier moment.
  A later or equal moment is left alone.
- **Follow-up count**: written only if the cell is empty or holds a strictly smaller number (and
  only once there is something to count).
- **Next action** (type and due date, together): written only if both cells are empty, or if they
  still hold exactly what Revansa last wrote there (`last_written`, from the ledger); a next action
  someone else put there is left alone. When Revansa has none any more, it clears the cells under
  the same condition.

The plan carries ids, statuses and counts of what was left alone, and for the other fields the
values that are to be written, which are Revansa's own: dates, counts, an action type. Never a name,
a contact or any other value the source holds.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

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
    NOT_ADVANCING = "not_advancing"  # the source holds a later date or a larger number
    UNREADABLE_VALUE = "unreadable_value"  # the source did not say what its cell holds
    OCCUPIED = "occupied"  # a next action that Revansa did not write


def _moment(value: datetime | None) -> datetime | None:
    """A moment as a source can hold it: to the second, so a write is never "earlier" than itself."""
    return None if value is None else value.replace(microsecond=0)


def value_text(field: ProspectField, value: Any) -> ProspectStatus | str:
    """A value of a written-back field in the canonical form the ledger keeps.

    The status is itself. A date is its ISO form in UTC, to the second; a count its digits; the next
    action its type and due date joined by `|`. Nothing (an empty cell) is the empty text.
    """
    match field:
        case ProspectField.STATUS:
            return value
        case ProspectField.NEXT_ACTION:
            action, due = value
            if action is None and due is None:
                return ""
            return f"{action or ''}|{value_text(ProspectField.LAST_CONTACTED_AT, due)}"
        case ProspectField.FOLLOW_UP_COUNT:
            return "" if value is None else str(int(value))
        case _:
            moment = _moment(value)
            return "" if moment is None else moment.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    """One value to write: `expected` is what the source held when it was read for this plan.

    `new` and `expected` are the typed values (a status; a `datetime`; an `int`; a pair of an action
    type and a due date), `None` or a pair of `None` for an empty cell.
    """

    prospect_id: str  # the source's native id
    public_id: str
    new: Any
    expected: Any
    field: ProspectField = ProspectField.STATUS

    def to_change(self) -> ProspectChange:
        return ProspectChange(
            prospect_id=self.prospect_id,
            new={self.field: self.new},
            expected={self.field: self.expected},
        )

    @property
    def ledger_new(self) -> ProspectStatus | str:
        return value_text(self.field, self.new)

    @property
    def ledger_expected(self) -> ProspectStatus | str:
        return value_text(self.field, self.expected)


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


# ---- the other fields -------------------------------------------------------------------------

# The order writes are planned and sent in. Fixed, so that a plan is reproducible.
WRITEBACK_ORDER = (
    ProspectField.STATUS,
    ProspectField.LAST_CONTACTED_AT,
    ProspectField.LAST_INBOUND_AT,
    ProspectField.FOLLOW_UP_COUNT,
    ProspectField.NEXT_ACTION,
)
_MOMENTS = (ProspectField.LAST_CONTACTED_AT, ProspectField.LAST_INBOUND_AT)
_NOTHING = (None, None)


def plan_field_writes(
    records: Sequence[Prospect],
    states: Mapping[ProspectId, ProspectState],
    values: Mapping[str, Mapping[ProspectField, Any]],
    *,
    capabilities: SourceCapabilities,
    policy: WriteBackPolicy,
    is_blocked: Callable[[ContactInfo], bool],
    registry_healthy: bool,
    last_written: Callable[[str, ProspectField], str | None],
) -> WriteBackPlan:
    """Every write the policy allows: the status (see `plan_status_writes`) and the other fields.

    `values` is what the source holds now in those other fields, by native id (a source's
    `read_writeback_values()`); a record it gives nothing for is left alone and counted.
    `last_written(public_id, field)` is the canonical text Revansa last wrote there, or None: the
    ledger's answer, and the only thing that lets Revansa recognise a cell as its own. It is asked
    only for a next action, and only when a cell holds one that differs from Revansa's: no other
    field needs it, and an empty or equal cell needs no answer.
    Raises `WriteBackPolicyError` if the policy does not fit the source.
    """
    status = plan_status_writes(
        records,
        states,
        capabilities=capabilities,
        policy=policy,
        is_blocked=is_blocked,
        registry_healthy=registry_healthy,
    )
    wanted = [f for f in WRITEBACK_ORDER if f is not ProspectField.STATUS and policy.allows(f)]
    if not wanted:
        return status
    writes = list(status.writes)
    skipped: dict[SkipReason, int] = dict(status.skipped)

    def skip(reason: SkipReason) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for record in records:
        try:
            public = to_public_id(capabilities.id_namespace, record.prospect_id)
        except InvalidNativeIdError:
            continue  # already counted by the status plan, or not wanted at all
        state = states.get(ProspectId(public))
        held = values.get(record.prospect_id)
        for each in wanted:
            if held is None or each not in held:
                skip(SkipReason.UNREADABLE_VALUE)
                continue
            if state is None:
                skip(SkipReason.NOTHING_TO_WRITE)
                continue
            current = held[each]
            new, reason = _wanted(each, state, current, lambda p=public, f=each: last_written(p, f))
            if reason is not None:
                skip(reason)
            else:
                writes.append(PlannedWrite(record.prospect_id, public, new, current, each))
    return WriteBackPlan(tuple(writes), skipped)


def _wanted(
    each: ProspectField,
    state: ProspectState,
    current: Any,
    written: Callable[[], str | None],
) -> tuple[Any, SkipReason | None]:
    """The value to write for one field of one prospect, or why none. `written` is asked (it reads
    the ledger) only when the answer depends on it."""
    schedule = state.schedule
    if each in _MOMENTS:
        mine = _moment(
            schedule.last_contacted_at
            if each is ProspectField.LAST_CONTACTED_AT
            else schedule.last_inbound_at
        )
        if mine is None:
            return None, SkipReason.NOTHING_TO_WRITE
        theirs = _moment(current)
        if theirs is None:
            return mine, None
        if theirs == mine:
            return None, SkipReason.ALREADY_APPLIED
        return (mine, None) if theirs < mine else (None, SkipReason.NOT_ADVANCING)
    if each is ProspectField.FOLLOW_UP_COUNT:
        mine = schedule.follow_up_count
        if mine <= 0:  # nothing has been counted: a sheet is not marked with zeros
            return None, SkipReason.NOTHING_TO_WRITE
        if current is None:
            return mine, None
        if current == mine:
            return None, SkipReason.ALREADY_APPLIED
        return (mine, None) if current < mine else (None, SkipReason.NOT_ADVANCING)
    # the next action: type and due date, written and cleared together
    mine_text = value_text(each, (schedule.next_action_type, schedule.next_action_due_at))
    theirs_text = value_text(each, current)
    if mine_text == theirs_text:
        return None, SkipReason.ALREADY_APPLIED if mine_text else SkipReason.NOTHING_TO_WRITE
    if theirs_text == "" or theirs_text == written():  # the ledger is read for a filled cell only
        return (
            ((schedule.next_action_type, _moment(schedule.next_action_due_at)), None)
            if (mine_text)
            else (_NOTHING, None)
        )
    return None, SkipReason.OCCUPIED
