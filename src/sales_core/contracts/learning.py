"""What the agent learned from its own results, as a note a Brain can be given.

A note is a short rule of conduct with the evidence behind it. It is not product content (that lives
with the product) and carries no word of a conversation: its wording is fixed by whoever defines the
catalog of notes, and it is identified by a stable `code`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sales_core.contracts._validation import require_text

NoteKind = Literal["do", "avoid"]
NoteState = Literal["watching", "active", "off"]
Evidence = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True, kw_only=True)
class LearnedNote:
    note_id: str  # stable: the code, plus the product when the note holds for one product only
    code: str
    kind: NoteKind
    theme: str
    state: NoteState = "watching"
    product: str | None = None
    evidence: Evidence = "low"
    cases: int = 0  # independent conversations behind it
    against: int = 0  # independent conversations that point the other way
    reason: str = ""  # a closed code saying why it exists; never personal data
    created_at: datetime | None = None
    updated_at: datetime | None = None
    active_since: datetime | None = (
        None  # when it last became active; the effect is measured from here
    )
    off_by: Literal["client", "auto"] | None = None  # who switched it off
    confirmed_by_client: bool = False

    def __post_init__(self) -> None:
        require_text(self.note_id, "note_id")
        require_text(self.code, "code")
        if self.cases < 0 or self.against < 0:
            raise ValueError("counts must be >= 0")
