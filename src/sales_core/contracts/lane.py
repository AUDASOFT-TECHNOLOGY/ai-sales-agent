"""LaneRoute: where one group of prospects comes from, how they are written to, what is sold to them.

A client may run several lanes at once (a source, a channel and the products offered to the people
of that source). The Core never reads a configuration: it is handed routes and uses them to pick the
channel of each prospect and to tell the Brain which products are in scope. A client with no lanes
has no routes and behaves exactly as before: one channel for everyone, every product.
"""

from dataclasses import dataclass

from sales_core.contracts._validation import require_text
from sales_core.contracts.enums import Channel
from sales_core.contracts.ids import LaneId


@dataclass(frozen=True, slots=True, kw_only=True)
class LaneRoute:
    lane_id: LaneId
    channel: Channel  # what the people of this lane are written to on
    product_ids: tuple[str, ...] = ()  # the products to offer them; empty: the whole catalog
    # The prefix of the ids a source gives its prospects (`<source_id>:<native id>`), which is how
    # a prospect that was never attributed is attributed. None: this lane is the default one.
    source_id: str | None = None

    def __post_init__(self) -> None:
        require_text(self.lane_id, "lane_id")
        object.__setattr__(self, "product_ids", tuple(self.product_ids))
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("a lane lists a product twice")
        if self.source_id is not None:
            require_text(self.source_id, "source_id")
