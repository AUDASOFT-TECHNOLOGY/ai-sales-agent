"""Which status a prospect really has when a source and Revansa each say something.

Pure rules, with no provider and no Engine in them, so they can be tested alone. They only ever make
things safer: whenever two sides disagree about whether to contact someone, the side that says "do
not" wins.

Safety order, from the strictest: `OPTED_OUT` > `CLOSED` > `PAUSED` > the contactable statuses
(`NEW`, `ACTIVE`). It is the Core's own reading: only `NEW` and `ACTIVE` may be contacted, and any
other status, even one added later, blocks (`sales_core.safety.checks`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sales_app.sourcing.problems import ProblemCode
from sales_core.contracts import ProspectStatus

CONTACTABLE = frozenset({ProspectStatus.NEW, ProspectStatus.ACTIVE})

_SEVERITY = {
    ProspectStatus.OPTED_OUT: 4,
    ProspectStatus.CLOSED: 3,
    ProspectStatus.PAUSED: 2,
}

# What an operator may map a CRM value to. `ACTIVE` is deliberately absent: it says "Revansa has
# started talking to this person", which only Revansa can know. A CRM can say a lead is new, that it
# must wait, that it is finished or that it refused contact; never that Revansa is already in touch.
MAPPABLE = frozenset(
    {
        ProspectStatus.NEW,
        ProspectStatus.PAUSED,
        ProspectStatus.CLOSED,
        ProspectStatus.OPTED_OUT,
    }
)


class StatusMappingError(ValueError):
    """An operator's status mapping is not acceptable. Raised when the configuration is read."""


def severity(status: ProspectStatus) -> int:
    """0 for a status that may be contacted, otherwise how strict the restriction is (1 to 4).

    An unknown status (one the Core adds later) counts as `PAUSED`: the Core blocks it as such.
    """
    if status in CONTACTABLE:
        return 0
    return _SEVERITY.get(status, _SEVERITY[ProspectStatus.PAUSED])


def is_restrictive(status: ProspectStatus) -> bool:
    return severity(status) > 0


def lowers_restriction(current: ProspectStatus, proposed: ProspectStatus) -> bool:
    """Whether moving from `current` to `proposed` would make contact more permitted.

    Used to refuse an automatic change that would lift an opt-out, a closure or a pause: only a
    person, through the proper path, may do that.
    """
    return severity(proposed) < severity(current)


def effective_status(
    source_status: ProspectStatus | None,
    overlay_status: ProspectStatus | None,
    *,
    contact_opted_out: bool = False,
) -> ProspectStatus:
    """The status the Core sees.

    1. A contact that has opted out is `OPTED_OUT`, whichever record or status it is found on. An
       opt-out follows the person, not one record.
    2. Otherwise, if either side is restrictive, the stricter one wins, whoever set it.
    3. Otherwise the status Revansa set wins (its own lifecycle, e.g. `ACTIVE`), then the source's,
       then `NEW`.

    `source_status` is None when the source does not own the status; `overlay_status` is None when
    Revansa never set one.
    """
    if contact_opted_out:
        return ProspectStatus.OPTED_OUT
    sides = (source_status, overlay_status)
    restrictive = [s for s in sides if s is not None and is_restrictive(s)]
    if restrictive:
        return max(restrictive, key=severity)
    if overlay_status is not None:
        return overlay_status
    if source_status is not None:
        return source_status
    return ProspectStatus.NEW


def _normalize(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True, slots=True)
class StatusMapping:
    """How one client's CRM statuses become Core statuses. Written by the operator, never inferred.

    There is no default: a value that is not listed is not understood, and a record carrying it is
    left out. In particular nothing is assumed about what a word like "closed", "won" or "lost"
    means for a client: whoever configures the mapping decides which values must stop all outreach
    and lists them as `closed` (or `paused`, or `opted_out`).
    """

    values: Mapping[str, ProspectStatus]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @classmethod
    def from_config(cls, raw: Mapping[str, str]) -> "StatusMapping":
        """`{crm value: core status}`. Values are compared ignoring case and surrounding blanks.

        Refused (`StatusMappingError`): a blank CRM value, a target that is not a Core status or is
        `active`, one value mapped to two different statuses, and a mapping with no value at all
        that leads to `new` (nothing could ever be contacted).
        """
        values: dict[str, ProspectStatus] = {}
        for key, target in raw.items():
            name = _normalize(key)
            if not name:
                raise StatusMappingError("a status mapping cannot name a blank CRM value")
            try:
                status = ProspectStatus(_normalize(target))
            except ValueError:
                raise StatusMappingError(
                    f"{key!r}: {target!r} is not a status (new, paused, closed or opted_out)"
                ) from None
            if status not in MAPPABLE:
                raise StatusMappingError(
                    f"{key!r}: a CRM value cannot map to {status.value!r}; only Revansa sets it "
                    "(use new, paused, closed or opted_out)"
                )
            if values.setdefault(name, status) is not status:
                raise StatusMappingError(f"{key!r} is mapped to two different statuses")
        if ProspectStatus.NEW not in values.values():
            raise StatusMappingError("no CRM value maps to 'new': nothing could be contacted")
        return cls(values)

    def lookup(self, raw: str) -> ProspectStatus | None:
        return self.values.get(_normalize(raw))


@dataclass(frozen=True, slots=True)
class StatusResolution:
    """A record's status as the Core will see it, or why the record must be left out.

    Two questions are kept apart on purpose:

    - Does the record enter the commercial cycle? `status` is None when it does not (`excluded`),
      with the reason in `problem`.
    - Did the CRM say "do not contact"? `opt_out_signal` carries that, whether or not the record is
      excluded. It is a safety signal to be honoured and recorded for the contact; it never makes a
      record admissible, and an excluded record stays excluded.
    """

    status: ProspectStatus | None
    problem: ProblemCode | None = None
    opt_out_signal: bool = False

    @property
    def excluded(self) -> bool:
        return self.status is None


def resolve_source_status(
    raw: str | None,
    mapping: StatusMapping,
    *,
    do_not_contact: bool = False,
) -> StatusResolution:
    """Turn a CRM's own status (and, if it has one, its do-not-contact signal) into a Core status.

    - A blank status excludes the record (`BLANK_STATUS`); it is never assumed to be new.
    - A status the operator did not map excludes it (`UNMAPPED_STATUS`).
    - A record the mapping understands is kept with its mapped status.
    - A do-not-contact signal is never dropped. On a record that is kept it makes the status
      `OPTED_OUT`, the strictest one, whatever the mapping says. On a record that is excluded it is
      reported in `opt_out_signal` next to the problem, so the caller can still record the refusal
      for that contact; the record itself stays out of the commercial cycle.

    No combination can make a record contactable: the signal only ever restricts.
    """
    if raw is None or not raw.strip():
        return StatusResolution(None, ProblemCode.BLANK_STATUS, opt_out_signal=do_not_contact)
    status = mapping.lookup(raw)
    if status is None:
        return StatusResolution(None, ProblemCode.UNMAPPED_STATUS, opt_out_signal=do_not_contact)
    if do_not_contact:
        return StatusResolution(ProspectStatus.OPTED_OUT, opt_out_signal=True)
    return StatusResolution(status)


# The statuses Revansa may pass on to a CRM (it only ever tightens a status, see
# `writeback.planning`); an opt-out is added only if the operator asked for it to be propagated.
_WRITTEN_STATUSES = (ProspectStatus.PAUSED, ProspectStatus.CLOSED)


@dataclass(frozen=True, slots=True)
class StatusWords:
    """The CRM word to write for each status Revansa may pass on: the inverse of a status mapping."""

    values: Mapping[ProspectStatus, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @classmethod
    def from_config(
        cls, raw: Mapping[str, str], *, propagate_opt_out: bool = False
    ) -> "StatusWords":
        """Exactly one CRM value must lead to each status that is written (paused and closed, and
        opted_out if opt-outs are propagated): none leaves Revansa without a word to write, several
        leave it guessing. Either is refused (`StatusMappingError`) with a sentence that says
        which status. Values that differ only by case or blanks count as several."""
        mapping = StatusMapping.from_config(raw)  # also refuses a mapping that is not valid
        needed = (*_WRITTEN_STATUSES, *((ProspectStatus.OPTED_OUT,) if propagate_opt_out else ()))
        words: dict[ProspectStatus, str] = {}
        for status in needed:
            found = sorted({key.strip() for key in raw if mapping.lookup(key) is status})
            if len(found) != 1:
                raise StatusMappingError(
                    f"status write-back needs exactly one value of status_mapping for "
                    f"{status.value!r}, and it has {len(found)}"
                )
            words[status] = found[0]
        return cls(words)

    def word(self, status: object) -> str | None:
        return self.values.get(status)  # type: ignore[arg-type]

    def all_words(self) -> tuple[str, ...]:
        return tuple(self.values.values())
