"""The same filter, search, sort and page as the SQL read model, over a list already in memory.

Used when the database holds no read model for the client yet (the Engine's own view, turned into
`ProspectItem`s, is then what is listed). Pure: no I/O.
"""

from collections.abc import Callable, Sequence
from datetime import datetime

from sales_app.read_model.models import (
    ConversationItem,
    Page,
    ProspectFilter,
    ProspectItem,
    ProspectListQuery,
    ProspectSort,
)
from sales_app.read_model.search import matches, search_text


def _keep(item: ProspectItem, f: ProspectFilter, at: datetime | None) -> bool:
    if f.statuses and item.status not in f.statuses:
        return False
    if f.lane_ids is not None and item.lane_id not in f.lane_ids:
        return False
    if f.channel is not None and item.channel != f.channel:
        return False
    if f.source_id is not None and item.source_id != f.source_id:
        return False
    if f.needs_human is not None and item.needs_human != f.needs_human:
        return False
    if f.has_error is not None and item.has_error != f.has_error:
        return False
    if (f.active_since is not None or f.active_until is not None) and at is None:
        return False
    if f.active_since is not None and at is not None and at < f.active_since:
        return False
    if f.active_until is not None and at is not None and at >= f.active_until:
        return False
    return not f.search or matches(search_text(item.display_name, item.email, item.phone), f.search)


def _name_key(item: ProspectItem) -> tuple[str, str]:
    return (item.display_name.casefold(), item.prospect_id)


def _order[T](rows: Sequence[T], query: ProspectListQuery, key: Callable[[T], ProspectItem], at):
    if query.sort is ProspectSort.NAME:
        return sorted(rows, key=lambda r: _name_key(key(r)))
    by_name = sorted(rows, key=lambda r: _name_key(key(r)))
    stamped = [r for r in by_name if at(r) is not None]
    return sorted(stamped, key=at, reverse=True) + [r for r in by_name if at(r) is None]


def list_in_memory(items: Sequence[ProspectItem], query: ProspectListQuery) -> Page[ProspectItem]:
    kept = [i for i in items if _keep(i, query.filter, i.last_activity_at)]
    ordered = _order(kept, query, lambda i: i, lambda i: i.last_activity_at)
    return Page(
        items=tuple(ordered[query.offset : query.offset + query.limit]),
        total=len(ordered),
        limit=query.limit,
        offset=query.offset,
    )


def conversations_in_memory(
    items: Sequence[ProspectItem], query: ProspectListQuery
) -> Page[ConversationItem]:
    """Prospects with an exchange on record: the Engine's view says when, not how many messages."""
    rows = [
        ConversationItem(
            prospect=i,
            message_count=None,
            last_message_at=i.last_activity_at,
            last_message_direction=None,
        )
        for i in items
        if i.last_activity_at is not None or i.conversation_id is not None
    ]
    kept = [r for r in rows if _keep(r.prospect, query.filter, r.last_message_at)]
    ordered = _order(kept, query, lambda r: r.prospect, lambda r: r.last_message_at)
    return Page(
        items=tuple(ordered[query.offset : query.offset + query.limit]),
        total=len(ordered),
        limit=query.limit,
        offset=query.offset,
    )
