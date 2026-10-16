"""What an operator allows Revansa to write back into a client's source.

Nothing is written unless the operator said so: the default policy is empty. Today only the status
can be written back, and only to tighten it (see `planning`), plus four copies of Revansa's own
bookkeeping a spreadsheet may show (last contact, last reply, follow-up count, next action), each
with its own rule of non-overwriting (see `planning`). The identity of a person (name, contact,
channel, language) can never be, whatever the policy says or the source declares writable.
"""

from dataclasses import dataclass

from sales_app.sourcing.fields import SOURCE_ONLY_FIELDS, ProspectField
from sales_app.sourcing.ports import SourceCapabilities

# The fields that may ever be written back. Revansa's own fields only, so that what a write-back
# ledger or a log holds can never be a name, a number or an address.
WRITEBACK_FIELDS = frozenset(
    {
        ProspectField.STATUS,
        ProspectField.LAST_CONTACTED_AT,
        ProspectField.LAST_INBOUND_AT,
        ProspectField.FOLLOW_UP_COUNT,
        ProspectField.NEXT_ACTION,
    }
)
assert not WRITEBACK_FIELDS & SOURCE_ONLY_FIELDS


class WriteBackPolicyError(ValueError):
    """A write-back policy that is not acceptable, for itself or for the source it is used with.
    The message is a fixed sentence."""


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteBackPolicy:
    """The operator's decision, built explicitly: empty (nothing is written) by default.

    - `fields`: the fields Revansa may write into the source. Only `WRITEBACK_FIELDS` are accepted;
      each one needs a column the operator named (the source checks it before anything is written).
    - `propagate_opt_out`: also write `OPTED_OUT` into the source, for a contact that opted out in
      Revansa or in the consent registry. A separate choice, off by default; it needs the status
      to be writable and a source that can express "do not contact".
    """

    fields: frozenset[ProspectField] = frozenset()
    propagate_opt_out: bool = False

    def __post_init__(self) -> None:
        fields = frozenset(self.fields)
        object.__setattr__(self, "fields", fields)
        if not fields <= WRITEBACK_FIELDS:
            raise WriteBackPolicyError("this field can never be written back to a source")
        if self.propagate_opt_out and ProspectField.STATUS not in fields:
            raise WriteBackPolicyError("propagating an opt-out needs the status to be written back")

    @classmethod
    def of(cls, *fields: ProspectField, propagate_opt_out: bool = False) -> "WriteBackPolicy":
        return cls(fields=frozenset(fields), propagate_opt_out=propagate_opt_out)

    @property
    def enabled(self) -> bool:
        return bool(self.fields)

    def allows(self, field: ProspectField) -> bool:
        return field in self.fields

    def validate_for(self, capabilities: SourceCapabilities) -> None:
        """Refuse a policy the source cannot honour, rather than discover it at the first write."""
        if not self.fields <= capabilities.writable:
            raise WriteBackPolicyError("the source cannot write a field the policy allows")
        if self.propagate_opt_out and not capabilities.consent_signal:
            raise WriteBackPolicyError("the source cannot express a refusal to be contacted")
