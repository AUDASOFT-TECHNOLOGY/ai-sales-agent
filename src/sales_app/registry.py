"""The client registry: one runtime per enabled client, looked up by id or inbound routing key.

It treats every client identically. All differences come from the client's config or Brain.

A client whose credentials are not all set is *not ready*: it is known (listed, shown in Admin) but
has no running Engine, or its Engine has stopped taking new work. Routing is derived from each
client's current config on every lookup, so a changed phone number id takes effect the moment its
new runtime is installed, with no table to keep in step.
"""

import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sales_app.brain_registry import BrainRegistry
from sales_app.config import ClientConfig, LoadedConfig
from sales_app.config.runtime_config import channel_provider_key
from sales_app.errors import ConfigurationError, RuntimeBuildError
from sales_app.wiring import ClientRuntime, IntegrationBuilders, build_runtime
from sales_core.ports import Clock

STAGGER_SECONDS = 5

Routing = Callable[[str, ClientConfig], str | None]  # (channel provider, config) -> routing key


class ClientRegistry:
    def __init__(
        self,
        runtimes: Sequence[ClientRuntime],
        routes: dict[tuple[str, str], str] | None = None,
        disabled: Sequence[str] = (),
        *,
        not_ready: Sequence[ClientConfig] = (),
        routing: Routing | None = None,
    ) -> None:
        self._runtimes = {r.client_id: r for r in runtimes}
        self._static_routes = dict(routes or {})  # (channel provider, routing key) -> client_id
        self._routing = routing
        self._disabled = tuple(disabled)
        self._not_ready = {c.client_id: c for c in not_ready}  # clients that have no runtime yet
        self._dropped: dict[str, tuple[int, datetime]] = {}
        self._lock = threading.RLock()

    @classmethod
    def build(
        cls,
        loaded: LoadedConfig,
        builders: IntegrationBuilders,
        clock: Clock,
        brains: BrainRegistry | None = None,
    ) -> "ClientRegistry":
        """Build every ready client, collecting all failures into one ConfigurationError. A client
        that lacks credentials is not an error: it is recorded as not ready and not built."""
        runtimes: list[ClientRuntime] = []
        not_ready: list[ClientConfig] = []
        errors: list[str] = []
        for index, config in enumerate(loaded.clients):
            if not config.ready:
                not_ready.append(config)
                continue
            try:
                runtime = build_runtime(
                    config,
                    builders,
                    clock,
                    stagger=timedelta(seconds=STAGGER_SECONDS * index),
                    brains=brains,
                )
            except RuntimeBuildError as error:
                errors.append(str(error))
                continue
            runtimes.append(runtime)
        if errors:
            raise ConfigurationError(errors)
        return cls(
            runtimes,
            disabled=loaded.disabled,
            not_ready=not_ready,
            routing=routing_from(builders),
        )

    # ---- membership ----------------------------------------------------------------------

    def add_disabled(self, client_id: str) -> None:
        """Record a client created after startup as disabled (it has no runtime until a restart)."""
        with self._lock:
            if client_id not in self._disabled:
                self._disabled = (*self._disabled, client_id)

    def add_runtime(self, runtime: ClientRuntime) -> None:
        """A client that was not ready now has a runtime."""
        with self._lock:
            self._runtimes[runtime.client_id] = runtime
            self._not_ready.pop(runtime.client_id, None)

    def set_not_ready(self, config: ClientConfig) -> None:
        """A client without a runtime still lacks credentials (its config may have changed)."""
        with self._lock:
            self._not_ready[config.client_id] = config

    # ---- lookup --------------------------------------------------------------------------

    def get(self, client_id: str) -> ClientRuntime | None:
        """The client's runtime, if it has ever been built (it may be suspended: see `runtimes`)."""
        return self._runtimes.get(client_id)

    def runtimes(self) -> list[ClientRuntime]:
        """The runtimes that take work: not-ready clients are left out."""
        with self._lock:
            return [r for r in self._runtimes.values() if not r.suspended]

    def configs(self) -> list[ClientConfig]:
        """Every enabled client's current config, ready or not."""
        with self._lock:
            return [*(r.config for r in self._runtimes.values()), *self._not_ready.values()]

    def get_config(self, client_id: str) -> ClientConfig | None:
        with self._lock:
            runtime = self._runtimes.get(client_id)
            return runtime.config if runtime is not None else self._not_ready.get(client_id)

    def missing(self, client_id: str) -> tuple[str, ...]:
        """What the client still lacks; empty if it is ready (or unknown)."""
        with self._lock:
            runtime = self._runtimes.get(client_id)
            if runtime is not None:
                return runtime.missing
            config = self._not_ready.get(client_id)
            return config.missing_credentials if config is not None else ()

    def by_routing_key(self, provider: str, key: str) -> ClientRuntime | None:
        """The runtime that takes work for this inbound account; None for a client that is not
        ready or unknown (see `not_ready_client_for` to tell them apart)."""
        owner = self._owner(provider, key)
        runtime = self._runtimes.get(owner) if owner else None
        return runtime if runtime is not None and not runtime.suspended else None

    def not_ready_client_for(self, provider: str, key: str) -> str | None:
        """The id of a client that owns this inbound account but cannot take work yet."""
        owner = self._owner(provider, key)
        if owner is None:
            return None
        return owner if self.missing(owner) else None

    def note_dropped(self, client_id: str, at: datetime) -> None:
        """Count an inbound message that was dropped because its client was not ready."""
        with self._lock:
            count, _ = self._dropped.get(client_id, (0, at))
            self._dropped[client_id] = (count + 1, at)

    def _owner(self, provider: str, key: str) -> str | None:
        with self._lock:
            if self._routing is None:
                return self._static_routes.get((provider, key))
            for config in self.configs():
                if channel_provider_key(config) == provider and (
                    self._routing(provider, config) == key
                ):
                    return config.client_id
        return None

    # ---- health --------------------------------------------------------------------------

    def health(self) -> list[dict[str, object]]:
        """Per-client status for /health/clients. Contains no secrets."""
        report: list[dict[str, object]] = []
        with self._lock:
            runtimes = list(self._runtimes.values())
            waiting = list(self._not_ready.values())
            disabled = self._disabled
            dropped = dict(self._dropped)
        for runtime in runtimes:
            config = runtime.config
            entry = {
                "client_id": runtime.client_id,
                "mode": str(config.effective_mode),
                "configured_mode": str(config.spec.mode),
                "mode_clamped": config.mode_clamped,
                "config_version": config.config_version,
                "config_source": config.config_source,
                **runtime.health.snapshot(),
            }
            if runtime.suspended:
                entry |= _not_ready_fields(runtime.missing, dropped.get(runtime.client_id))
            report.append(entry)
        for config in waiting:
            report.append(
                {
                    "client_id": config.client_id,
                    "mode": str(config.effective_mode),
                    "configured_mode": str(config.spec.mode),
                    "mode_clamped": config.mode_clamped,
                    "config_version": config.config_version,
                    "config_source": config.config_source,
                    "last_runs": {},
                    "consecutive_failures": 0,
                    **_not_ready_fields(config.missing_credentials, dropped.get(config.client_id)),
                }
            )
        report.extend({"client_id": name, "status": "disabled"} for name in disabled)
        return report


def routing_from(builders: IntegrationBuilders) -> Routing:
    def routing(provider: str, config: ClientConfig) -> str | None:
        channel = builders.channels.get(provider)
        return channel.routing_key(config) if channel is not None else None

    return routing


def _not_ready_fields(
    missing: Sequence[str], dropped: tuple[int, datetime] | None
) -> dict[str, object]:
    fields: dict[str, object] = {
        "status": "not_ready",
        "missing_credentials": list(missing),
        "last_error": f"missing credentials: {'; '.join(missing)}",
        "last_error_at": None,
        "dropped_inbound": dropped[0] if dropped else 0,
    }
    if dropped:
        fields["last_dropped_inbound_at"] = dropped[1].isoformat()
    return fields
