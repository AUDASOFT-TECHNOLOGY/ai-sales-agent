"""Composition root: the one place where Core, Brains and integrations are wired together."""

import logging
import os
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from sales_app.admin_auth import AdminAuthService, AdminSessionStore, AdminUserStore
from sales_app.agent_state import AgentStateStore, InMemoryAgentStateStore
from sales_app.billing import InvoiceStore
from sales_app.channel_policy import ChannelPolicyService
from sales_app.client_auth import ClientAuthService, is_client_active
from sales_app.client_profiles import ClientProfileStore
from sales_app.config import build_llm_registry, clamp_mode, load_client_configs
from sales_app.config.channel_access import DEFAULT_ALLOWED
from sales_app.config.secrets import WHATSAPP_VERIFY_TOKEN
from sales_app.config_store import ConfigStore
from sales_app.daily_report import DailyReportService
from sales_app.errors import ConfigurationError
from sales_app.inbox import PositionStore
from sales_app.llm_pricing import LLMPricingStore
from sales_app.llm_usage import LLMUsageStore
from sales_app.parsers import ParserTable, build_parsers
from sales_app.payment_confirmation import PAYMENT_WEBHOOK_SECRET
from sales_app.payment_provider import PaymentProvider, SignedWebhookProvider
from sales_app.persistence import AccountStores, build_account_stores
from sales_app.persistence.invoice_rows import SqlInvoiceStore
from sales_app.persistence.llm_pricing_rows import SqlLLMPricingStore
from sales_app.persistence.llm_usage_rows import SqlLLMUsageStore
from sales_app.persistence.mail_cursor_rows import SqlMailCursorStore
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.persistence.plan_rows import SqlPlanStore
from sales_app.persistence.send_cap_rows import SqlSendCapStore, SqlSendCounter
from sales_app.plans import PlanStore, current_daily_quota
from sales_app.registry import ClientRegistry
from sales_app.report_wiring import build_report_service
from sales_app.runtime_manager import RuntimeManager
from sales_app.scheduler import Scheduler
from sales_app.secret_store import (
    AUDIT_FILENAME,
    GLOBAL_SCOPE,
    CredentialService,
    FileSecretAudit,
    SecretAudit,
    SecretStore,
    SecretStoreError,
    build_secret_store,
)
from sales_app.send_cap import SendCapStore, SendLimits
from sales_app.settings import Settings
from sales_app.source_checks import InMemorySourceCheckStore, SourceCheckStore
from sales_app.sourcing.consent.files import open_file_registry
from sales_app.sourcing.ports import OperationalStore
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_app.wiring import IntegrationBuilders, default_builders
from sales_app.wiring.providers import ProviderRegistry, default_registry
from sales_app.wiring.writeback import WritebackSupport, default_policies, sql_ledgers
from sales_core.approval import ApprovalMode
from sales_core.ports import Clock
from sales_integrations.system import SystemClock

log = logging.getLogger(__name__)


@dataclass
class Application:
    settings: Settings
    registry: ClientRegistry
    config_store: ConfigStore  # kept for the CLI and, later, runtime refresh and the Admin API
    admin_auth: AdminAuthService  # Admin Revansa login and session verification
    client_profiles: ClientProfileStore  # client account profiles, kept out of client.yaml
    client_auth: ClientAuthService  # Client login, session and password change
    scheduler: Scheduler
    parsers: (
        ParserTable  # channel provider name -> webhook parser; replaced when credentials change
    )
    clock: Clock
    # Write-only management of stored credentials (status only, never a value). Saving or removing
    # one makes `runtime_manager` apply it to the running clients, without a restart.
    credentials: CredentialService
    runtime_manager: RuntimeManager
    # Non-sensitive projection of `IntegrationBuilders.llms`: provider names only (no callables, no
    # secrets), so a future Admin can list what is actually wired without holding the builders
    # table itself. See sales_app.wiring.integrations.IntegrationBuilders.
    llm_providers: tuple[str, ...] = ()
    # The providers Revansa knows, with the models they declare. The model catalog is built from
    # them and from the Admin's models in Settings (see `sales_app.llm_models.settings_catalog`),
    # each time it is asked for, so a model the Admin adds shows up at once.
    provider_registry: ProviderRegistry = field(default_factory=default_registry)
    # The database connection pool, when accounts and settings live in SQL (None: file fallback).
    account_stores: AccountStores | None = None
    # The Client's own switch for its agent. An agent runs only if the Admin enabled it AND its
    # client started it: no record means it was never started.
    agent_state: AgentStateStore = field(default_factory=InMemoryAgentStateStore)
    # The last real test of each client's prospect source, and what it was made for.
    source_checks: SourceCheckStore = field(default_factory=InMemorySourceCheckStore)
    # Which channels clients may use: the Admin's decision (see `sales_app.channel_policy`).
    channel_policy: ChannelPolicyService | None = None
    # How each channel is run, kept for what the webhook route needs of it (answering a stranger).
    builders: IntegrationBuilders | None = None
    # The Admin's price list for model calls; None when the deployment has no database (prices are
    # then not kept, and the Admin API says so rather than keeping them only in memory).
    llm_pricing: LLMPricingStore | None = None
    # The commercial plans and each client's plan; None without a database (the Admin API says so).
    plans: PlanStore | None = None
    # The monthly invoices; None without a database (the Admin API says so).
    invoices: InvoiceStore | None = None
    # Resolves the payment provider that confirms invoice payments, or None when none is configured
    # (the payment webhook then answers 409). Read on every call: a secret set by the Admin applies
    # at once. See `sales_app.payment_provider`.
    payment_provider: Callable[[], PaymentProvider | None] | None = None
    # The client's daily report (settings, reports kept per day, delivery); None without a database
    # (the Client API says so). See `sales_app.daily_report`.
    daily_reports: DailyReportService | None = None
    # The Admin's daily e-mail cap of each client and the effective limit (cap and plan) every e-mail
    # path obeys; None without a database (e-mails are then refused; the Admin API says so). See
    # `sales_app.send_cap`.
    send_caps: SendCapStore | None = None
    send_limits: SendLimits | None = None

    def agent_may_work(self, client_id: str) -> bool:
        """`_may_work`, failing closed: if either switch cannot be read, the answer is no."""
        try:
            return _may_work(self.agent_state, self.settings.clients_dir, client_id)
        except Exception:  # noqa: BLE001 - an unreadable switch must never mean "go"
            return False

    def close(self) -> None:
        self.runtime_manager.shutdown()  # first: a refresh may be waiting for a job to end
        self.scheduler.shutdown()
        if self.account_stores is not None:
            self.account_stores.close()


def build_application(
    settings: Settings,
    *,
    env: Mapping[str, str] | None = None,
    builders: IntegrationBuilders | None = None,
    clock: Clock | None = None,
    executor: Executor | None = None,
    mode_ceiling: ApprovalMode | None = None,
    require_clients: bool = True,
    config_store: ConfigStore | None = None,
    admin_user_store: AdminUserStore | None = None,
    admin_session_store: AdminSessionStore | None = None,
    client_profile_store: ClientProfileStore | None = None,
    client_session_store: AdminSessionStore | None = None,
    secret_store: SecretStore | None = None,
    secret_audit: SecretAudit | None = None,
    refresh_executor: Executor | None = None,
    agent_state_store: AgentStateStore | None = None,
    source_check_store: SourceCheckStore | None = None,
) -> Application:
    """Load, validate and wire everything, or raise a ConfigurationError listing every problem.

    Strict about structure: if any enabled client's configuration is invalid, nothing starts.
    `enabled: false` is the only way to leave a client out. A client that merely lacks credentials
    is valid but *not ready*: the application starts, and that client is not run until they are set
    (in Admin Settings or the environment). `mode_ceiling` can only make things safer than the
    settings. `refresh_executor` runs credential refreshes (default: one background thread).
    """
    environment = os.environ if env is None else env
    clock = clock or SystemClock()
    # An explicit ceiling (e.g. the CLI's --dry-run) can only make things safer, never raise it.
    ceiling = (
        settings.mode_ceiling
        if mode_ceiling is None
        else clamp_mode(mode_ceiling, settings.mode_ceiling)
    )

    injected = (
        config_store,
        admin_user_store,
        admin_session_store,
        client_profile_store,
        client_session_store,
    )
    # Files or SQL, decided by the settings (see sales_app.persistence.factory). Anything a caller
    # injects (the tests do) wins; the defaults are only built if something is missing.
    defaults = build_account_stores(settings) if any(s is None for s in injected) else None
    try:
        store = config_store if config_store is not None else defaults.config_store
        admin_users = admin_user_store if admin_user_store is not None else defaults.admin_users
        admin_sessions = (
            admin_session_store if admin_session_store is not None else defaults.admin_sessions
        )
        client_profiles = (
            client_profile_store if client_profile_store is not None else defaults.client_profiles
        )
        client_sessions = (
            client_session_store if client_session_store is not None else defaults.client_sessions
        )
        agent_state = (
            agent_state_store
            if agent_state_store is not None
            else (defaults.agent_state if defaults is not None else InMemoryAgentStateStore())
        )
        source_checks = (
            source_check_store
            if source_check_store is not None
            else (defaults.source_checks if defaults is not None else InMemorySourceCheckStore())
        )
        return _assemble(
            settings,
            environment,
            clock,
            ceiling,
            builders,
            executor,
            require_clients,
            refresh_executor,
            secret_store,
            secret_audit,
            store,
            admin_users,
            admin_sessions,
            client_profiles,
            client_sessions,
            defaults,
            agent_state,
            source_checks,
        )
    except BaseException:
        if defaults is not None:
            defaults.close()
        raise


def _assemble(
    settings: Settings,
    environment: Mapping[str, str],
    clock: Clock,
    ceiling: ApprovalMode,
    builders: IntegrationBuilders | None,
    executor: Executor | None,
    require_clients: bool,
    refresh_executor: Executor | None,
    secret_store: SecretStore | None,
    secret_audit: SecretAudit | None,
    store: ConfigStore,
    admin_users: AdminUserStore,
    admin_sessions: AdminSessionStore,
    client_profiles: ClientProfileStore,
    client_sessions: AdminSessionStore,
    account_stores: AccountStores | None,
    agent_state: AgentStateStore,
    source_checks: SourceCheckStore,
) -> Application:
    client_auth = ClientAuthService(
        client_profiles,
        client_sessions,
        clock,
        is_active=lambda client_id: is_client_active(settings.clients_dir, client_id),
        session_ttl=timedelta(hours=settings.client_session_ttl_hours),
    )
    admin_auth = AdminAuthService(
        admin_users,
        admin_sessions,
        clock,
        session_ttl=timedelta(hours=settings.admin_session_ttl_hours),
    )
    try:
        secrets = (
            secret_store
            if secret_store is not None
            else build_secret_store(
                settings.secrets_dir,
                settings.secrets_key.get_secret_value() if settings.secrets_key else None,
                now=clock.now,
            )
        )
    except SecretStoreError as error:  # a wrong or missing master key must stop startup
        raise ConfigurationError([str(error)]) from error
    audit = (
        secret_audit
        if secret_audit is not None
        else FileSecretAudit(settings.secrets_dir / AUDIT_FILENAME)
    )
    credentials = CredentialService(secrets, audit, now=clock.now)
    plans = _plan_store(account_stores)
    send_caps, send_limits = _send_limits(account_stores, plans, clock)
    builders = builders or default_builders(
        settings,
        verify_token=lambda: secrets.get(GLOBAL_SCOPE, WHATSAPP_VERIFY_TOKEN),
        operational_stores=_operational_stores(account_stores),
        consent=lambda client_id: open_file_registry(
            settings.secrets_dir, secrets, audit, client_id
        ),
        writeback=_writeback_support(settings, account_stores),
        llm_usage=_llm_usage_stores(account_stores),
        send_guards=send_limits.guard if send_limits is not None else None,
    )
    if builders.send_guards is None and send_limits is not None:
        # Builders given by the caller still obey the deployment's cap (their metered channels).
        builders = replace(builders, send_guards=send_limits.guard)

    llm_registry = build_llm_registry(settings.allowed_llm_models)
    if settings.model_upstream_ids:
        log.warning(
            "SALES_AGENT_MODEL_UPSTREAM_IDS is ignored: the Model IDs are the ones the Admin "
            "configures in Settings"
        )
    loaded = load_client_configs(
        settings.clients_dir,
        env=environment,
        mode_ceiling=ceiling,
        store=store,
        llm_registry=llm_registry,
        secret_store=secrets,
    )
    errors = list(loaded.errors)
    if require_clients and not loaded.clients and not errors:
        errors.append(f"no enabled clients found in {settings.clients_dir}")

    registry: ClientRegistry | None = None
    try:
        registry = ClientRegistry.build(loaded, builders, clock)
    except ConfigurationError as error:
        errors.extend(error.errors)
    if errors or registry is None:
        raise ConfigurationError(errors)

    parsers = ParserTable(build_parsers(loaded.clients, builders))
    manager = RuntimeManager(
        clients_dir=settings.clients_dir,
        registry=registry,
        builders=builders,
        clock=clock,
        env=environment,
        mode_ceiling=ceiling,
        config_store=store,
        llm_registry=llm_registry,
        secret_store=secrets,
        parsers=parsers,
        executor=refresh_executor,
    )
    credentials.set_on_change(manager.notify_change)
    channel_policy = ChannelPolicyService(secrets, audit, now=clock.now)
    channel_policy.set_on_change(manager.notify_change)

    daily_reports = build_report_service(
        account_stores.engine if account_stores is not None else None,
        registry,
        builders,
        lambda client_id: (
            channel_policy.effective(client_id) if channel_policy else DEFAULT_ALLOWED
        ),
        clock,
        limits=send_limits,
    )
    return Application(
        settings=settings,
        registry=registry,
        config_store=store,
        admin_auth=admin_auth,
        client_profiles=client_profiles,
        client_auth=client_auth,
        scheduler=Scheduler(
            registry,
            clock,
            executor=executor,
            may_run=lambda client_id: _may_work(agent_state, settings.clients_dir, client_id),
            inbox=builders,
            mail_cursors=_mail_cursor_stores(account_stores),
            reports=daily_reports,
        ),
        parsers=parsers,
        clock=clock,
        credentials=credentials,
        runtime_manager=manager,
        llm_providers=tuple(sorted(builders.llms)),
        provider_registry=default_registry(),
        account_stores=account_stores,
        agent_state=agent_state,
        source_checks=source_checks,
        channel_policy=channel_policy,
        builders=builders,
        llm_pricing=_llm_pricing_store(account_stores),
        plans=plans,
        send_caps=send_caps,
        send_limits=send_limits,
        invoices=_invoice_store(account_stores),
        payment_provider=_payment_provider_resolver(secrets),
        daily_reports=daily_reports,
    )


def _operational_stores(
    account_stores: AccountStores | None,
) -> Callable[[str], OperationalStore] | None:
    """One client's operational store in the deployment's database; None if it has no database
    (a client whose source needs the store is then refused, never run on something else)."""
    engine = account_stores.engine if account_stores is not None else None
    if engine is None:
        return None
    return lambda client_id: SqlOperationalStore(SqlOperationalRows(engine, client_id))


def _invoice_store(account_stores: AccountStores | None) -> InvoiceStore | None:
    engine = account_stores.engine if account_stores is not None else None
    return SqlInvoiceStore(engine) if engine is not None else None


def _payment_provider_resolver(secrets: SecretStore) -> Callable[[], PaymentProvider | None]:
    def resolve() -> PaymentProvider | None:
        secret = secrets.get(GLOBAL_SCOPE, PAYMENT_WEBHOOK_SECRET)
        return SignedWebhookProvider(secret) if secret else None

    return resolve


def _plan_store(account_stores: AccountStores | None) -> PlanStore | None:
    engine = account_stores.engine if account_stores is not None else None
    return SqlPlanStore(engine) if engine is not None else None


def _send_limits(
    account_stores: AccountStores | None, plans: PlanStore | None, clock: Clock
) -> tuple[SendCapStore | None, SendLimits | None]:
    """The cap store and the effective limit (Admin cap and plan) of a deployment with a database."""
    engine = account_stores.engine if account_stores is not None else None
    if engine is None:
        return None, None
    caps = SqlSendCapStore(engine)
    quota = (lambda client_id: current_daily_quota(plans, client_id)) if plans is not None else None
    return caps, SendLimits(caps, SqlSendCounter(engine), clock, quota)


def _llm_pricing_store(account_stores: AccountStores | None) -> LLMPricingStore | None:
    engine = account_stores.engine if account_stores is not None else None
    return SqlLLMPricingStore(engine) if engine is not None else None


def _mail_cursor_stores(
    account_stores: AccountStores | None,
) -> Callable[[str], PositionStore] | None:
    """One client's mailbox positions in the deployment's database; None without one (a mailbox
    that is read without being consumed then does not read at all)."""
    engine = account_stores.engine if account_stores is not None else None
    if engine is None:
        return None
    return lambda client_id: SqlMailCursorStore(engine, client_id)


def _llm_usage_stores(
    account_stores: AccountStores | None,
) -> Callable[[str], LLMUsageStore] | None:
    """One client's LLM usage register in the deployment's database; None without one (calls are
    then not recorded)."""
    engine = account_stores.engine if account_stores is not None else None
    if engine is None:
        return None
    return lambda client_id: SqlLLMUsageStore(engine, client_id)


def _writeback_support(
    settings: Settings, account_stores: AccountStores | None
) -> WritebackSupport:
    """Write-back as this deployment offers it: off unless `SALES_AGENT_WRITEBACK_ENABLED`, and its
    ledger in the database (without one, a source that asks for write-back is refused)."""
    engine = account_stores.engine if account_stores is not None else None
    return WritebackSupport(
        enabled=settings.writeback_enabled,
        open_ledger=sql_ledgers(engine) if engine is not None else None,
        policies=default_policies(),
    )


def _may_work(store: AgentStateStore, clients_dir: Path, client_id: str) -> bool:
    """Whether a client's agent may take work now: the Admin's `enabled` as it is stored (read each
    time, so disabling a client stops its work without a restart) **and** the client's own decision
    (started, not paused). Either alone is not enough."""
    state = store.get(client_id)
    return state is not None and state.running and is_client_active(clients_dir, client_id)


def describe_modes(application: Application) -> list[dict[str, Any]]:
    """Startup summary: which mode each client really runs in, and whether it was clamped."""
    return [
        {
            k: v
            for k, v in entry.items()
            if k in {"client_id", "mode", "configured_mode", "mode_clamped"}
        }
        for entry in application.registry.health()
        if entry.get("status") != "disabled"
    ]
