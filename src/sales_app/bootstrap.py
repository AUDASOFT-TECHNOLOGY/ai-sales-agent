"""Composition root: the one place where Core, Brains and integrations are wired together."""

import os
from collections.abc import Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any

from sales_app.config import clamp_mode, load_client_configs
from sales_app.config_store import ConfigStore, FileConfigStore
from sales_app.errors import ConfigurationError
from sales_app.registry import ClientRegistry
from sales_app.scheduler import Scheduler
from sales_app.settings import Settings
from sales_app.wiring import IntegrationBuilders, default_builders
from sales_core.approval import ApprovalMode
from sales_core.ports import Clock, WebhookParser
from sales_integrations.system import SystemClock


@dataclass
class Application:
    settings: Settings
    registry: ClientRegistry
    config_store: ConfigStore  # kept for the CLI and, later, runtime refresh and the Admin API
    scheduler: Scheduler
    parsers: Mapping[str, WebhookParser]  # channel provider name -> webhook parser
    clock: Clock

    def close(self) -> None:
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
) -> Application:
    """Load, validate and wire everything, or raise a ConfigurationError listing every problem.

    Strict: if any enabled client is invalid, nothing starts. `enabled: false` is the only way
    to leave a client out. `mode_ceiling` can only make things safer than the settings.
    """
    environment = os.environ if env is None else env
    clock = clock or SystemClock()
    builders = builders or default_builders(settings)
    # An explicit ceiling (e.g. the CLI's --dry-run) can only make things safer, never raise it.
    ceiling = (
        settings.mode_ceiling
        if mode_ceiling is None
        else clamp_mode(mode_ceiling, settings.mode_ceiling)
    )

    store = config_store if config_store is not None else FileConfigStore(settings.config_dir)
    loaded = load_client_configs(
        settings.clients_dir, env=environment, mode_ceiling=ceiling, store=store
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

    parsers: dict[str, WebhookParser] = {}
    for name, provider in builders.channels.items():
        secrets: dict[str, str] = {}
        for config in loaded.clients:
            if config.spec.whatsapp.provider != name:
                continue
            key, secret = provider.routing_key(config), provider.webhook_secret(config)
            if key and secret:
                secrets[key] = secret
        if secrets or any(c.spec.whatsapp.provider == name for c in loaded.clients):
            parsers[name] = provider.build_parser(secrets)

    return Application(
        settings=settings,
        registry=registry,
        config_store=store,
        scheduler=Scheduler(registry, clock, executor=executor),
        parsers=parsers,
        clock=clock,
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
