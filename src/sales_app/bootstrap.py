"""Composition root: the one place where Core, Brains and integrations are wired together."""

import logging
import os
import re
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
from sales_app.client_records import ClientRecordStore
from sales_app.config import build_llm_registry, clamp_mode, load_client_configs
from sales_app.config.channel_access import DEFAULT_ALLOWED
from sales_app.config.client_wiring import wiring_for
from sales_app.config.secrets import WHATSAPP_VERIFY_TOKEN
from sales_app.config_store import ConfigStore
from sales_app.consent_wiring import check_consent_mode, open_consent_registry
from sales_app.daily_report import DailyReportService
from sales_app.email_activation import EmailActivationService
from sales_app.email_platform import AuthLedger
from sales_app.errors import ConfigurationError
from sales_app.gmail_wiring import GmailMailService
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
from sales_app.persistence.resource_rows import SqlResourceStore
from sales_app.persistence.send_cap_rows import SqlSendCapStore, SqlSendCounter
from sales_app.plans import PlanStore, current_daily_quota
from sales_app.registry import ClientRegistry
from sales_app.report_wiring import build_report_service
from sales_app.resources.knowledge import KnowledgeIndex
from sales_app.resources.service import ResourceService
from sales_app.runtime_manager import RuntimeManager
from sales_app.scheduler import Scheduler
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    CredentialService,
    SecretAudit,
    SecretStore,
    SecretStoreError,
)
from sales_app.secret_wiring import open_secrets
from sales_app.send_cap import SendCapStore, SendLimits
from sales_app.settings import Settings
from sales_app.sheets_wiring import SheetsAccessService
from sales_app.hubspot_wiring import HubSpotAccessService
from sales_app.writeback_switch import WritebackSwitch
from sales_app.hubspot_wiring import install as install_hubspot_access
from sales_app.sheets_wiring import install as install_sheets_access
from sales_app.source_checks import InMemorySourceCheckStore, SourceCheckStore
from sales_app.sourcing.ports import OperationalStore
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_app.telegram_account import TelegramAccountService
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
    # Clients' requests to have their e-mail mailbox activated by the Admin (see
    # `sales_app.email_activation`).
    email_activation: EmailActivationService | None = None
    # How each channel is run, kept for what the webhook route needs of it (answering a stranger).
    builders: IntegrationBuilders | None = None
    # The last login test of the platform's mail servers and of each client's mailbox, in memory (see
    # `sales_app.email_platform.AuthLedger`): a mailbox is only "ready" while its proof holds.
    mail_auth: AuthLedger = field(default_factory=AuthLedger)
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
    # The Gmail mailbox mode: the platform's Google application (resolved at each use), the clients'
    # grants in the secret store and the channel/inbox of a client in Gmail mode.
    gmail: GmailMailService | None = None
    # Telegram from a client's own account: the platform's Telegram application, the clients'
    # sessions in the secret store, and the channel/inbox of a client in `account` mode.
    telegram_accounts: TelegramAccountService | None = None
    # Google Sheets by the client's own Google authorisation (OAuth), same platform application as
    # Gmail, grants in the client's scope of the secret store. See `sales_app.sheets_wiring`.
    sheets_access: SheetsAccessService | None = None
    hubspot_access: HubSpotAccessService | None = None
    # The Admin's master switch for write-back (see `sales_app.writeback_switch`).
    writeback_switch: WritebackSwitch | None = None
    # The documents and links clients attach to their products and the search over their text; None
    # without a database (the Client API says so). See `sales_app.resources`.
    product_resources: ResourceService | None = None
    knowledge: KnowledgeIndex | None = None
    # Identity, status and engine of each client, in the database; None without one (the historical
    # `client.yaml` files then decide, as before). See `sales_app.client_records`.
    client_records: ClientRecordStore | None = None

    def client_wiring(self, client_id: str) -> tuple[dict[str, Any] | None, str | None]:
        """A client's operator wiring: its record first, its `client.yaml` as the fallback."""
        return wiring_for(self.settings.client_files_dir, client_id, self.client_records)

    def client_enabled(self, client_id: str) -> bool | None:
        """Whether the Admin has switched the account on (None: no readable record or file)."""
        raw, _problem = self.client_wiring(client_id)
        return None if raw is None else raw.get("enabled", True) is not False

    def set_client_enabled(self, client_id: str, enabled: bool, *, by: str) -> None:
        """Record the Admin's decision in the database and, while a `client.yaml` still exists, in
        the file too, so that going back to a version that reads only files changes nothing."""
        if self.client_records is not None:
            self.client_records.set_enabled(client_id, enabled, at=self.clock.now(), by=by)
        files = self.settings.client_files_dir
        path = files / client_id / "client.yaml" if files is not None else None
        if path is not None and path.is_file():
            write_enabled_line(path, enabled)

    def agent_may_work(self, client_id: str) -> bool:
        """`_may_work`, failing closed: if either switch cannot be read, the answer is no."""
        try:
            return _may_work(self.agent_state, self._is_active, client_id)
        except Exception:  # noqa: BLE001 - an unreadable switch must never mean "go"
            return False

    def _is_active(self, client_id: str) -> bool:
        return is_client_active(self.settings.client_files_dir, client_id, self.client_records)

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
    gmail_service: GmailMailService | None = None,
    client_record_store: ClientRecordStore | None = None,
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
        records = (
            client_record_store
            if client_record_store is not None
            else (defaults.client_records if defaults is not None else None)
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
            gmail_service,
            records,
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
    gmail_service: GmailMailService | None = None,
    client_records: ClientRecordStore | None = None,
) -> Application:
    client_auth = ClientAuthService(
        client_profiles,
        client_sessions,
        clock,
        is_active=lambda client_id: is_client_active(
            settings.client_files_dir, client_id, client_records
        ),
        session_ttl=timedelta(hours=settings.client_session_ttl_hours),
    )
    admin_auth = AdminAuthService(
        admin_users,
        admin_sessions,
        clock,
        session_ttl=timedelta(hours=settings.admin_session_ttl_hours),
    )
    try:
        opened = (
            open_secrets(
                settings,
                account_stores.engine if account_stores is not None else None,
                now=clock.now,
            )
            if secret_store is None or secret_audit is None
            else None
        )
    except SecretStoreError as error:  # a wrong or missing master key must stop startup
        raise ConfigurationError([str(error)]) from error
    secrets = secret_store if secret_store is not None else opened[0]
    audit = secret_audit if secret_audit is not None else opened[1]
    check_consent_mode(settings, account_stores.engine if account_stores is not None else None)
    credentials = CredentialService(secrets, audit, now=clock.now)
    plans = _plan_store(account_stores)
    send_caps, send_limits = _send_limits(account_stores, plans, clock)
    # Resolved at each use from the Admin's credentials: no restart when they change.
    gmail = gmail_service or GmailMailService(
        secrets=secrets, clock=clock, public_base_url=lambda: settings.public_base_url
    )
    telegram_accounts = TelegramAccountService(secrets=secrets, clock=clock)
    sheets_access = SheetsAccessService(
        secrets=secrets, clock=clock, public_base_url=lambda: settings.public_base_url
    )
    install_sheets_access(sheets_access)
    hubspot_access = HubSpotAccessService(
        secrets=secrets, clock=clock, public_base_url=lambda: settings.public_base_url
    )
    install_hubspot_access(hubspot_access)
    writeback_switch = WritebackSwitch(
        secrets, audit, default=settings.writeback_enabled, now=clock.now
    )
    product_resources, knowledge = _resources(account_stores, clock)
    builders = builders or default_builders(
        settings,
        verify_token=lambda: secrets.get(GLOBAL_SCOPE, WHATSAPP_VERIFY_TOKEN),
        operational_stores=_operational_stores(account_stores),
        consent=lambda client_id: open_consent_registry(
            settings, account_stores.engine if account_stores else None, secrets, audit, client_id
        ),
        writeback=_writeback_support(writeback_switch, account_stores),
        llm_usage=_llm_usage_stores(account_stores),
        knowledge=knowledge.for_client if knowledge is not None else None,
        send_guards=send_limits.guard if send_limits is not None else None,
        gmail=gmail,
        telegram_accounts=telegram_accounts,
        first_contacts=(
            (lambda client_id, limit: send_limits.fixed_guard(client_id, "telegram_new", limit))
            if send_limits is not None
            else None
        ),
    )
    if builders.knowledge is None and knowledge is not None:
        builders = replace(builders, knowledge=knowledge.for_client)
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
        settings.client_files_dir,
        env=environment,
        mode_ceiling=ceiling,
        store=store,
        llm_registry=llm_registry,
        secret_store=secrets,
        records=client_records,
    )
    errors = list(loaded.errors)
    # A deployment on the database may start with no client at all (the first is created from the
    # Admin); one that only has files needs at least one.
    if require_clients and not loaded.clients and not errors and client_records is None:
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
        clients_dir=settings.client_files_dir,
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
        client_records=client_records,
    )
    credentials.set_on_change(manager.notify_change)
    channel_policy = ChannelPolicyService(secrets, audit, now=clock.now)
    channel_policy.set_on_change(manager.notify_change)
    writeback_switch.set_on_change(manager.notify_change)

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
            may_run=lambda client_id: _may_work(
                agent_state,
                lambda cid: is_client_active(settings.client_files_dir, cid, client_records),
                client_id,
            ),
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
        email_activation=EmailActivationService(secrets, audit, now=clock.now),
        builders=builders,
        llm_pricing=_llm_pricing_store(account_stores),
        plans=plans,
        send_caps=send_caps,
        send_limits=send_limits,
        gmail=gmail,
        telegram_accounts=telegram_accounts,
        sheets_access=sheets_access,
        hubspot_access=hubspot_access,
        writeback_switch=writeback_switch,
        invoices=_invoice_store(account_stores),
        payment_provider=_payment_provider_resolver(secrets),
        daily_reports=daily_reports,
        product_resources=product_resources,
        knowledge=knowledge,
        client_records=client_records,
    )


def _resources(
    account_stores: AccountStores | None, clock: Clock
) -> tuple[ResourceService | None, KnowledgeIndex | None]:
    """The product documents of a deployment with a database (None without one)."""
    engine = account_stores.engine if account_stores is not None else None
    if engine is None:
        return None, None
    store = SqlResourceStore(engine)
    return ResourceService(store, clock=clock.now), KnowledgeIndex(store)


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
    switch: WritebackSwitch, account_stores: AccountStores | None
) -> WritebackSupport:
    """Write-back as the platform offers it: the Admin's switch (default: the deployment's), and its
    ledger in the database (without one, a source that asks for write-back is refused)."""
    engine = account_stores.engine if account_stores is not None else None
    return WritebackSupport(
        enabled=switch.enabled,
        open_ledger=sql_ledgers(engine) if engine is not None else None,
        policies=default_policies(),
    )


_ENABLED_LINE = re.compile(r"^enabled:.*$", re.MULTILINE)


def write_enabled_line(path: Path, enabled: bool) -> None:
    """Flip only the `enabled` line of a `client.yaml`, so the operator's comments survive."""
    text = path.read_text(encoding="utf-8")
    line = f"enabled: {'true' if enabled else 'false'}"
    if _ENABLED_LINE.search(text):
        text = _ENABLED_LINE.sub(line, text, count=1)
    else:
        text = f"{line}\n{text}"
    path.write_text(text, encoding="utf-8")


def _may_work(store: AgentStateStore, is_active: Callable[[str], bool], client_id: str) -> bool:
    """Whether a client's agent may take work now: the Admin's `enabled` as it is stored (read each
    time, so disabling a client stops its work without a restart) **and** the client's own decision
    (started, not paused). Either alone is not enough."""
    state = store.get(client_id)
    return state is not None and state.running and is_active(client_id)


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
