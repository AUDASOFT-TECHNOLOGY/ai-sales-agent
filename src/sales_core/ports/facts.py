"""Ports through which what is known about a prospect is read and recorded.

Kept apart from `SalesRepository`: the Engine only needs to read, whoever applies facts only needs
to write, and a backend may serve either. An adapter that is both (the Sheets adapter, later a SQL
one) simply implements both.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sales_core.contracts import ClientId, Fact, ProspectId


@runtime_checkable
class FactsReader(Protocol):
    """Read-only. Where facts are stored, and who writes them, is not the Engine's concern."""

    def get_facts(self, prospect_id: ProspectId) -> Sequence[Fact]:
        """Every observation recorded for the prospect, oldest first (ties in append order);
        empty if there are none. Several may share a key: the same key can take another value
        later, and earlier observations stay in the history, so the newest is the current one.
        Only this client's facts, only this prospect's."""
        ...


@runtime_checkable
class FactsWriter(Protocol):
    """Append-only. A recorded observation is never rewritten or removed by this port.

    An instance is bound to a single client, like `SalesRepository`.
    """

    @property
    def client_id(self) -> ClientId: ...

    def append_facts(self, prospect_id: ProspectId, facts: Sequence[Fact]) -> None:
        """Record observations for the prospect. Idempotent: an observation whose
        `sales_core.contracts.fact_identity` is already recorded (or repeated in `facts`) is
        ignored, so a retry after a failure of unknown outcome cannot duplicate it.

        Raises `KeyError` for an unknown prospect, `ValueError` for a fact without `recorded_at` or
        one the backend cannot store. Nothing is written in either case."""
        ...
