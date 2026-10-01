"""Client-independent figures computed from a client's own sales data.

Pure functions over what `SalesRepository` returns: no I/O, no client, no vendor. Dashboards and
reports must call these rather than recount (see docs/web-admin-product-design.md section 5).
"""

from collections.abc import Iterable

from sales_core.contracts import ActionStatus, SalesAction


def prospects_contacted(actions: Iterable[SalesAction]) -> int:
    """Distinct prospects that received at least one message: those with a SENT action."""
    return len({a.prospect_id for a in actions if a.status is ActionStatus.SENT})
