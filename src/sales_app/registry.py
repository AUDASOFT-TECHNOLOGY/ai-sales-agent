"""The client registry: one runtime per enabled client, looked up by id or inbound routing key.

It treats every client identically. All differences come from the client's config or Brain.
"""

from collections.abc import Sequence
from datetime import timedelta

from sales_app.config import LoadedConfig
from sales_app.errors import ConfigurationError, RuntimeBuildError
from sales_app.wiring import ClientRuntime, IntegrationBuilders, build_runtime
from sales_core.ports import Clock

STAGGER_SECONDS = 5


class ClientRegistry:
    def __init__(
        self,
        runtimes: Sequence[ClientRuntime],
        routes: dict[tuple[str, str], str],
        disabled: Sequence[str] = (),
    ) -> None:
        self._runtimes = {r.client_id: r for r in runtimes}
        self._routes = dict(routes)  # (channel provider, routing key) -> client_id
        self._disabled = tuple(disabled)

    @classmethod
    def build(
        cls, loaded: LoadedConfig, builders: IntegrationBuilders, clock: Clock
    ) -> "ClientRegistry":
        """Build every enabled client, collecting all failures into one ConfigurationError."""
        runtimes: list[ClientRuntime] = []
        routes: dict[tuple[str, str], str] = {}
        errors: list[str] = []
        for index, config in enumerate(loaded.clients):
            try:
                runtime = build_runtime(
                    config, builders, clock, stagger=timedelta(seconds=STAGGER_SECONDS * index)
                )
            except RuntimeBuildError as error:
                errors.append(str(error))
                continue
            runtimes.append(runtime)
            provider = builders.channels[config.spec.whatsapp.provider]
            key = provider.routing_key(config)
            if key:
                routes[(config.spec.whatsapp.provider, key)] = config.client_id
        if errors:
            raise ConfigurationError(errors)
        return cls(runtimes, routes, loaded.disabled)

    def get(self, client_id: str) -> ClientRuntime | None:
        return self._runtimes.get(client_id)

    def by_routing_key(self, provider: str, key: str) -> ClientRuntime | None:
        client_id = self._routes.get((provider, key))
        return self._runtimes.get(client_id) if client_id else None

    def runtimes(self) -> list[ClientRuntime]:
        return list(self._runtimes.values())

    def health(self) -> list[dict[str, object]]:
        """Per-client status for /health/clients. Contains no secrets."""
        report: list[dict[str, object]] = []
        for runtime in self._runtimes.values():
            config = runtime.config
            report.append(
                {
                    "client_id": runtime.client_id,
                    "mode": str(config.effective_mode),
                    "configured_mode": str(config.spec.mode),
                    "mode_clamped": config.mode_clamped,
                    **runtime.health.snapshot(),
                }
            )
        report.extend({"client_id": name, "status": "disabled"} for name in self._disabled)
        return report
