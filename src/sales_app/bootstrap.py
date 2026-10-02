"""Composition root: the one place where Core, Brains and integrations are wired together."""

import os
from collections.abc import Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sales_app.admin_auth import (
    AdminAuthService,
    AdminSessionStore,
    AdminUserStore,
    FileAdminSessionStore,
    FileAdminUserStore,
)
from sales_app.client_auth import ClientAuthService, is_client_active
from sales_app.client_profiles import ClientProfileStore, FileClientProfileStore
from sales_app.config import build_llm_registry, clamp_mode, load_client_configs
from sales_app.config.secrets import WHATSAPP_VERIFY_TOKEN
from sales_app.config_store import ConfigStore, FileConfigStore
from sales_app.errors import ConfigurationError
from sales_app.parsers import ParserTable, build_parsers
from sales_app.registry import ClientRegistry
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
from sales_app.settings import Settings
from sales_app.wiring import IntegrationBuilders, default_builders
from sales_core.approval import ApprovalMode
from sales_core.ports import Clock
from sales_integrations.system import SystemClock


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

    def close(self) -> None:
        self.runtime_manager.shutdown()  # first: a refresh may be waiting for a job to end
        self.scheduler.shutdown()


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

    store = config_store if config_store is not None else FileConfigStore(settings.config_dir)
    admin_users = (
        admin_user_store
        if admin_user_store is not None
        else FileAdminUserStore(settings.admin_dir / "users.json")
    )
    admin_sessions = (
        admin_session_store
        if admin_session_store is not None
        else FileAdminSessionStore(settings.admin_dir / "sessions")
    )
    client_profiles = (
        client_profile_store
        if client_profile_store is not None
        else FileClientProfileStore(settings.profiles_dir / "profiles.json")
    )
    client_sessions = (
        client_session_store
        if client_session_store is not None
        else FileAdminSessionStore(settings.profiles_dir / "sessions")
    )
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
    builders = builders or default_builders(
        settings, verify_token=lambda: secrets.get(GLOBAL_SCOPE, WHATSAPP_VERIFY_TOKEN)
    )

    llm_registry = build_llm_registry(settings.allowed_llm_models)
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

    return Application(
        settings=settings,
        registry=registry,
        config_store=store,
        admin_auth=admin_auth,
        client_profiles=client_profiles,
        client_auth=client_auth,
        scheduler=Scheduler(registry, clock, executor=executor),
        parsers=parsers,
        clock=clock,
        credentials=credentials,
        runtime_manager=manager,
        llm_providers=tuple(sorted(builders.llms)),
    )


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
