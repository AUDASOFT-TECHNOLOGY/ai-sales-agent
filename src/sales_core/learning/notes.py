"""Where the Engine reads the notes the agent learned, for the Brain's next message."""

from collections.abc import Sequence
from typing import Protocol

from sales_core.contracts import LearnedNote


class NotesProvider(Protocol):
    def active_notes(self) -> Sequence[LearnedNote]:
        """The notes that apply now (state `active`), at most the few the Brain may be given."""
        ...
