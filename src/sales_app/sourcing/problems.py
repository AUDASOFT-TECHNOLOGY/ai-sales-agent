"""What a source reports about records it left out, without ever repeating their content."""

from dataclasses import dataclass
from enum import StrEnum


class ProblemCode(StrEnum):
    INVALID_NATIVE_ID = "invalid_native_id"  # the source's id is outside the accepted alphabet
    INVALID_LOCAL_ID = "invalid_local_id"  # an Engine-made id that cannot be stored safely
    BLANK_STATUS = "blank_status"  # the record carries no status: left out, never assumed new
    UNMAPPED_STATUS = "unmapped_status"  # a status the operator did not map: left out
    UNREADABLE_RECORD = "unreadable_record"  # the record cannot be interpreted at all
    DUPLICATE_CONTACT = "duplicate_contact"  # a local prospect and a source record share a contact


@dataclass(frozen=True, slots=True)
class SourceProblem:
    """One record that was left out (or set aside), and why.

    `native_id` is the source's own id when it is safe to show (it passed the id check), otherwise
    None. `detail` is a fixed sentence: never a name, a number, an address or a credential.
    """

    code: ProblemCode
    detail: str
    native_id: str | None = None
