"""Wiring of the daily report onto the real application: its stores, its channels, its send cap.

The report needs a database (it is kept per client and day, and it takes a place in the e-mail send
cap that lives there): without one there is no report service, and the Client API says so rather
than keeping reports in memory. The channel that carries a report is built from the same provider
table as the prospecting channel (so the same credentials, the same checks), but it writes to the
client's own report address, never to a prospect.
"""

from collections.abc import Callable
from typing import Any

from sales_app.daily_report import (
    CHANNEL_UNAVAILABLE,
    EMAIL,
    DailyReportService,
    ReportChannelUnavailableError,
    SendCapQuota,
)
from sales_app.persistence.daily_report_rows import SqlReportSettingsStore, SqlReportStore
from sales_app.registry import ClientRegistry
from sales_app.send_cap import DailySendGuard, SendLimits
from sales_app.wiring import IntegrationBuilders
from sales_core.approval import ApprovalMode
from sales_core.ports import Clock


class BuilderReportChannels:
    """`ReportChannels` over the provider table: the channel of `kind`, built for this client."""

    def __init__(self, registry: ClientRegistry, builders: IntegrationBuilders) -> None:
        self._registry = registry
        self._builders = builders

    def __repr__(self) -> str:
        return "BuilderReportChannels()"

    def open(self, client_id: str, channel: str):
        config = self._registry.get_config(client_id)
        provider = self._builders.channels.get(channel)
        if config is None or provider is None:
            raise ReportChannelUnavailableError("channel_not_wired")
        if config.effective_mode is ApprovalMode.DRY_RUN:  # a dry run talks to nobody
            raise ReportChannelUnavailableError("dry_run")
        try:
            return provider.build(config)
        except Exception:  # noqa: BLE001 - missing credentials and the like: never their text
            raise ReportChannelUnavailableError(CHANNEL_UNAVAILABLE) from None


def build_report_service(
    engine: Any | None,  # the SQLAlchemy engine; only `sales_app.persistence` may import SQLAlchemy
    registry: ClientRegistry,
    builders: IntegrationBuilders,
    application_policy: Callable[[str], frozenset[str]],
    clock: Clock,
    *,
    limits: SendLimits | None = None,
) -> DailyReportService | None:
    """The report service of this deployment, or None when it has no database.

    `limits` is the deployment's e-mail limit (Admin cap and plan): the report takes its place in the
    same counter as every other e-mail of the client, under the same effective cap."""
    if engine is None or limits is None:
        return None

    def guard(client_id: str) -> DailySendGuard:
        return limits.guard(client_id, EMAIL)

    def timezone_of(client_id: str) -> str | None:
        config = registry.get_config(client_id)
        return config.spec.timezone if config is not None else None

    return DailyReportService(
        settings=SqlReportSettingsStore(engine),
        reports=SqlReportStore(engine),
        channels=BuilderReportChannels(registry, builders),
        now=clock.now,
        quota=SendCapQuota(lambda client_id: limits.effective(client_id), limits.used),
        send_guard=guard,
        channel_allowed=lambda client_id, channel: channel in application_policy(client_id),
        default_timezone=timezone_of,
    )
