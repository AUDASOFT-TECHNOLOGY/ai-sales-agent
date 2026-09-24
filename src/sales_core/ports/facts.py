"""Port through which the Engine reads what is already known about a prospect."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sales_core.contracts import Fact, ProspectId


@runtime_checkable
class FactsReader(Protocol):
    """Read-only. Where facts are stored, and who writes them, is not the Engine's concern."""

    def get_facts(self, prospect_id: ProspectId) -> Sequence[Fact]:
        """Facts known for the prospect; empty if there are none."""
        ...
