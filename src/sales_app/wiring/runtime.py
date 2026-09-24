"""One client's runtime: its Engine plus the bookkeeping the scheduler and /health need."""

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sales_app.config import ClientConfig
from sales_app.errors import RuntimeBuildError
from sales_app.wiring.brains import load_brain
from sales_app.wiring.integrations import IntegrationBuilders
from sales_core.approval import ApprovalMode
from sales_core.contracts import ClientContext
from sales_core.engine import Engine, EngineConfig, InboundPolicy, UnknownSenderPolicy
from sales_core.ports import Clock, FactsReader
from sales_integrations.system import DisabledChannel


class ClientHealth:
    """What `/health/clients` reports. Written from scheduler threads and webhook tasks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = "ready"
        self._last_runs: dict[str, str] = {}
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._failures = 0

    def record_success(self, job: str, at: datetime, errors: Sequence[str] = ()) -> None:
        with self._lock:
            self._status = "ready"
            self._failures = 0
            self._last_runs[job] = at.isoformat()
            if errors:  # the job ran, but reported problems worth seeing
                self._last_error, self._last_error_at = errors[0], at.isoformat()

    def record_failure(self, job: str, at: datetime, error: str) -> None:
        with self._lock:
            self._status = "degraded"
            self._failures += 1
            self._last_error, self._last_error_at = f"{job}: {error}", at.isoformat()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": self._status,
                "last_runs": dict(self._last_runs),
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
                "consecutive_failures": self._failures,
            }


@dataclass
class ScheduleState:
    """In-memory only: every job is idempotent, so losing this on restart is harmless."""

    last_daily_date: date | None = None
    last_prepare_at: datetime | None = None
    last_dispatch_at: datetime | None = None
    last_recover_at: datetime | None = None
    blocked_until: datetime | None = None  # backoff after a failed job
    _busy: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def try_begin(self) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            return True

    def end(self) -> None:
        with self._lock:
            self._busy = False


@dataclass
class ClientRuntime:
    config: ClientConfig
    engine: Engine
    health: ClientHealth = field(default_factory=ClientHealth)
    state: ScheduleState = field(default_factory=ScheduleState)
    stagger: timedelta = timedelta(0)  # spreads clients' triggers to spare API quotas

    @property
    def client_id(self) -> str:
        return self.config.client_id


def _choose[V](client_id: str, table: Mapping[str, V], provider: str, what: str) -> V:
    try:
        return table[provider]
    except KeyError:
        raise RuntimeBuildError(f"{client_id}: unknown {what} provider {provider!r}") from None


def _stage[T](client_id: str, stage: str, build: Callable[[], T]) -> T:
    try:
        return build()
    except Exception as error:
        raise RuntimeBuildError(f"{client_id}: {stage}: {error}") from error


def build_runtime(
    config: ClientConfig,
    builders: IntegrationBuilders,
    clock: Clock,
    *,
    stagger: timedelta = timedelta(0),
) -> ClientRuntime:
    """Assemble one client's Engine. Everything here comes from that client's own config."""
    spec, client_id = config.spec, config.client_id

    repository = _stage(
        client_id,
        "repository",
        lambda: _choose(client_id, builders.repositories, spec.sheets.provider, "sheets")(config),
    )
    llm = _stage(
        client_id,
        "llm",
        lambda: _choose(client_id, builders.llms, spec.llm.provider, "llm")(config),
    )
    brain = _stage(
        client_id, "brain", lambda: load_brain(spec.brain.factory, llm, spec.brain.settings)
    )
    provider = _choose(client_id, builders.channels, spec.whatsapp.provider, "whatsapp")
    if config.effective_mode is ApprovalMode.DRY_RUN:
        channel = DisabledChannel(provider.kind)  # a dry run cannot send, by construction
    else:
        channel = _stage(client_id, "channel", lambda: provider.build(config))

    engine = _stage(
        client_id,
        "engine",
        lambda: Engine(
            client=ClientContext(
                client_id=client_id,
                display_name=spec.display_name,
                timezone=spec.timezone,
                settings=spec.brain.settings,
            ),
            repository=repository,
            brain=brain,
            channel=channel,
            clock=clock,
            facts=repository if isinstance(repository, FactsReader) else None,
            config=EngineConfig(
                mode=config.effective_mode,
                daily_action_limit=spec.daily_action_limit,
                max_attempts=spec.max_attempts,
                opt_out_keywords=frozenset(spec.opt_out_keywords),
                inbound=InboundPolicy(
                    spontaneous_conversations=spec.inbound.spontaneous_conversations.enabled,
                    unknown_sender=UnknownSenderPolicy(spec.inbound.unknown_contacts.policy),
                    max_new_per_day=spec.inbound.unknown_contacts.max_new_per_day,
                ),
            ),
        ),
    )
    return ClientRuntime(config=config, engine=engine, stagger=stagger)
