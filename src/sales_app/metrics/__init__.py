"""Per-client figures for the dashboards: pure functions (`compute`), the contract (`contracts`) and
the service that composes them from a reader of counts (`service`).

See `docs/read-model-and-metrics.md`. Nothing here imports SQL; the SQL adapter lives in
`sales_app.persistence.metrics_rows`.
"""

from sales_app.metrics.compute import fold_by_product, labelled, lanes_selling, percent
from sales_app.metrics.contracts import (
    UNASSIGNED,
    ClientMetrics,
    NotTracked,
    PaymentFigures,
    Period,
)
from sales_app.metrics.service import MetricsReader, MetricsService

__all__ = [
    "UNASSIGNED",
    "ClientMetrics",
    "MetricsReader",
    "MetricsService",
    "NotTracked",
    "PaymentFigures",
    "Period",
    "fold_by_product",
    "labelled",
    "lanes_selling",
    "percent",
]
