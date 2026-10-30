"""One client's runtime: its Engine plus the bookkeeping the scheduler and /health need."""

import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sales_app.agent_activity import ActivityRecorder
from sales_app.brain_registry import BrainRegistry, installed_brains
from sales_app.config import ClientConfig
from sales_app.config.runtime_config import (
    NO_CHANNEL_REASON,
    CanonicalClientConfig,
    channel_provider_key,
)
from sales_app.config.schema import BrainSection
from sales_app.config.telegram_settings import account_mode_of
from sales_app.errors import ClientNotReadyError, ClientUnavailableError, RuntimeBuildError
from sales_app.exchange_rates import today_rates
from sales_app.learning_service import StoreNotes, StoreTraceSink
from sales_app.llm_usage import RecordingLLMProvider
from sales_app.operational_signals import NotifyingChannel, NotifyingLLMProvider
from sales_app.send_cap import CommercialGateChannel, RefusingChannel, SendCapChannel
from sales_app.sourcing.composite import CompositeRepository
from sales_app.wiring.brains import load_brain
from sales_app.wiring.integrations import IntegrationBuilders
from sales_app.wiring.writeback import ClientWriteback, WritebackSummary, build_client_writeback
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel, ClientContext, LaneRoute
from sales_core.engine import (
    ChannelRouter,
    Engine,
    EngineConfig,
    InboundPolicy,
    UnknownSenderPolicy,
)
from sales_core.learning import NO_TRACE, TraceSink
from sales_core.ports import (
    NO_ACTIVITY,
    ActivityReporter,
    Brain,
    Clock,
    FactsReader,
    FactsWriter,
    LLMProvider,
    MessagingChannel,
    SalesRepository,
)
from sales_integrations.system import DisabledChannel

log = logging.getLogger(__name__)

INBOX_JOB = "inbox"  # the scheduler's job that reads a mailbox (`sales_app.scheduler.Job.INBOX`)
SYNC_JOB = "sync"  # the job that writes results back into the sources (`Job.SYNC`)


class ClientHealth:
    """What `/health/clients` reports. Written from scheduler threads and webhook tasks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = "ready"
        self._last_runs: dict[str, str] = {}
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._failures = 0
        # The mailbox is followed on its own: the success of the daily cycle must not hide a
        # mailbox that cannot be read, and the mailbox's failures must not inflate the backoff of
        # the jobs that send (`consecutive_failures` stays theirs).
        self._inbox_failures = 0
        # Write-back is followed on its own too, and only as counters: its failures must neither
        # inflate the backoff of the jobs that send nor paint the client as down.
        self._sync_failures = 0
        self._writeback: dict[str, int] | None = None

    def record_writeback(
        self, at: datetime, summary: WritebackSummary, *, failed: bool = False
    ) -> None:
        """One write-back run, as counters (integers) and nothing else."""
        with self._lock:
            self._sync_failures = self._sync_failures + 1 if failed else 0
            self._last_runs[SYNC_JOB] = at.isoformat()
            self._writeback = {**summary.counters(), "consecutive_failures": self._sync_failures}

    def record_writeback_failure(self, at: datetime) -> None:
        """A write-back run that raised: counted, with no message (it could carry a value)."""
        with self._lock:
            self._sync_failures += 1
            self._last_runs[SYNC_JOB] = at.isoformat()
            self._writeback = {
                **(self._writeback or {}),
                "consecutive_failures": self._sync_failures,
            }

    def record_success(self, job: str, at: datetime, errors: Sequence[str] = ()) -> None:
        with self._lock:
            if job == INBOX_JOB:
                self._inbox_failures = 0
            else:
                self._failures = 0
            self._status = "degraded" if self._inbox_failures or self._failures else "ready"
            self._last_runs[job] = at.isoformat()
            if errors:  # the job ran, but reported problems worth seeing
                self._last_error, self._last_error_at = errors[0], at.isoformat()

    def record_failure(self, job: str, at: datetime, error: str) -> None:
        with self._lock:
            self._status = "degraded"
            if job == INBOX_JOB:
                self._inbox_failures += 1
            else:
                self._failures += 1
            self._last_error, self._last_error_at = f"{job}: {error}", at.isoformat()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            snapshot: dict[str, object] = {
                "status": self._status,
                "last_runs": dict(self._last_runs),
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
                "consecutive_failures": self._failures,
            }
            if self._inbox_failures:  # only for a client whose mailbox is failing
                snapshot["inbox_failures"] = self._inbox_failures
            if self._writeback is not None:  # only for a client that writes results back
                snapshot["writeback"] = dict(self._writeback)
            return snapshot


@dataclass
class ScheduleState:
    """In-memory only: every job is idempotent, so losing this on restart is harmless."""

    last_daily_date: date | None = None
    last_prepare_at: datetime | None = None
    last_dispatch_at: datetime | None = None
    last_recover_at: datetime | None = None
    last_inbox_at: datetime | None = None
    # The mailbox backs off on its own: a mailbox that cannot be reached must not stop the client
    # from sending what its team approved.
    inbox_blocked_until: datetime | None = None
    inbox_failures: int = 0
    # Write-back has its own rhythm and backoff too: it never delays, and is never delayed by, the
    # jobs that send.
    last_sync_at: datetime | None = None
    sync_blocked_until: datetime | None = None
    sync_failures: int = 0
    # The daily report has its own rhythm and backoff too (it never delays the jobs that send).
    last_report_at: datetime | None = None
    report_blocked_until: datetime | None = None
    report_failures: int = 0
    # Learning from results runs rarely and off the conversation path; a failure only delays it.
    last_learn_at: datetime | None = None
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
    """One client's runtime. The object stays the same for the client's whole life (its `state` and
    `health` with it); only its Engine and config are replaced, and only through `stage`.

    Every use of the Engine goes through `lease()`. A lease is how "never two Engines for one
    client" is guaranteed: a staged replacement is installed only when no lease is active, and
    leases already running finish on the Engine they started with. Do not take a lease while
    holding one on the same runtime (a pending replacement would make the second wait forever).
    """

    config: ClientConfig
    engine: Engine
    health: ClientHealth = field(default_factory=ClientHealth)
    state: ScheduleState = field(default_factory=ScheduleState)
    stagger: timedelta = timedelta(0)  # spreads clients' triggers to spare API quotas
    missing: tuple[str, ...] = ()  # credentials the client lacks: it takes no new work
    # The syncers of the sources that are written back to; None for a client that has none. Replaced
    # with the Engine, never on its own.
    writeback: ClientWriteback | None = None
    # Where this client's agent tells what it is doing, for the screen that shows it live.
    activity: ActivityReporter = NO_ACTIVITY
    _gate: threading.Condition = field(
        default_factory=threading.Condition, init=False, repr=False, compare=False
    )
    _active: int = field(default=0, init=False, repr=False, compare=False)
    _draining: bool = field(default=False, init=False, repr=False, compare=False)
    _pending: (
        tuple[ClientConfig, Engine, Callable[[], None] | None, ClientWriteback | None] | None
    ) = field(default=None, init=False, repr=False, compare=False)

    @property
    def client_id(self) -> str:
        return self.config.client_id

    @property
    def suspended(self) -> bool:
        return bool(self.missing)

    @property
    def replacement_pending(self) -> bool:
        return self._pending is not None

    @contextmanager
    def lease(self) -> Iterator[Engine]:
        """The Engine, for the duration of one job or one inbound message.

        Raises ClientNotReadyError for a client lacking credentials. While a replacement is being
        drained, new leases wait (the Engine's own lock already serialises its work, so this adds
        no waiting that was not there) and then get the new Engine.
        """
        with self._gate:
            while self._draining:
                self._gate.wait()
            installed = self._install_if_idle()
            if self.missing:
                raise ClientNotReadyError(self.client_id, self.missing)
            self._active += 1
            engine = self.engine
        self._notify(installed)
        try:
            yield engine
        finally:
            with self._gate:
                self._active -= 1
                installed = self._install_if_idle()
                self._gate.notify_all()
            self._notify(installed)

    def stage(
        self,
        config: ClientConfig,
        engine: Engine,
        on_installed: Callable[[], None] | None = None,
        writeback: ClientWriteback | None = None,
    ) -> bool:
        """Queue a replacement built offline and start draining. Returns True if it was installed
        at once (nothing was running). Otherwise it is installed when the last active lease ends,
        and `on_installed` is called then. A newer staged replacement supersedes an older one."""
        with self._gate:
            self._pending = (config, engine, on_installed, writeback)
            self._draining = True
            installed = self._install_if_idle()
            done = self._pending is None
        self._notify(installed)
        return done

    def relax(self) -> None:
        """Stop holding back new leases, but keep the staged replacement: it is installed as soon
        as the runtime is next idle."""
        with self._gate:
            self._draining = False
            self._gate.notify_all()

    def suspend(self, config: ClientConfig) -> None:
        """The client lost a credential: no new work starts. Work already running finishes, and a
        replacement that was staged is dropped."""
        with self._gate:
            self.config = config
            self.missing = config.missing_credentials
            self._pending = None
            self._draining = False
            self._gate.notify_all()

    # ---- internals (the caller holds the gate) -------------------------------------------

    def _install_if_idle(self) -> Callable[[], None] | None:
        if self._pending is None or self._active:
            return None
        config, engine, on_installed, writeback = self._pending
        self.config, self.engine, self.missing = config, engine, ()
        self.writeback = writeback
        self._pending = None
        self._draining = False
        self._gate.notify_all()
        return on_installed or (lambda: None)

    @staticmethod
    def _notify(callback: Callable[[], None] | None) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:
            log.exception("runtime install callback failed")


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


def _build_brain(section: BrainSection, llm: LLMProvider, brains: BrainRegistry | None) -> Brain:
    """A Brain named by key comes from the registry of installed Brains and nothing else; only the
    operator's legacy `factory` is loaded by path."""
    if section.agent is not None:
        return (brains or installed_brains()).create(section.agent, llm, section.settings)
    assert section.factory is not None
    return load_brain(section.factory, llm, section.settings)


def _attach_knowledge(brain: Brain, builders: IntegrationBuilders, client_id: str) -> None:
    """Lets a Brain that can use reference documents consult this client's own. Never fatal: a Brain
    without that ability, or a deployment without documents, is built exactly as before."""
    attach = getattr(brain, "attach_knowledge", None)
    if builders.knowledge is None or not callable(attach):
        return
    try:
        attach(builders.knowledge(client_id))
    except Exception as error:  # noqa: BLE001 - documents are optional
        log.warning("%s: reference documents not attached: %s", client_id, error.__class__.__name__)


def _record_usage(
    config: ClientConfig, builders: IntegrationBuilders, llm: LLMProvider
) -> LLMProvider:
    """The client's model, recording each call in its usage register. A deployment with no register
    (no database) gets the model as it is; a register that cannot be opened must not stop the client
    either."""
    if builders.llm_usage is None:
        return llm
    client_id, section = config.client_id, config.spec.llm
    try:
        store = builders.llm_usage(client_id)
    except Exception as error:  # noqa: BLE001 - recording never blocks the client
        log.warning("%s: LLM usage not recorded: %s", client_id, error.__class__.__name__)
        return llm
    return RecordingLLMProvider(
        llm, store, client_id=client_id, provider=section.provider, model=section.model
    )


def _learning_ports(builders: IntegrationBuilders, client_id: str) -> tuple[TraceSink, Any]:
    """Where the agent keeps how it decided, and where it reads what it learned. A store that cannot
    be opened must not stop the client: it then keeps nothing and learns nothing."""
    if builders.learning is None:
        return NO_TRACE, None
    try:
        store = builders.learning(client_id)
    except Exception as error:  # noqa: BLE001
        log.warning("%s: learning not kept: %s", client_id, error.__class__.__name__)
        return NO_TRACE, None
    return StoreTraceSink(store), StoreNotes(store)


def _activity_reporter(builders: IntegrationBuilders, client_id: str) -> ActivityReporter:
    """Where the agent tells what it does. A store that cannot be opened must not stop the client."""
    if builders.activity is None:
        return NO_ACTIVITY
    try:
        return ActivityRecorder(builders.activity(client_id))
    except Exception as error:  # noqa: BLE001 - telling never blocks the client
        log.warning("%s: activity not shown: %s", client_id, error.__class__.__name__)
        return NO_ACTIVITY


def _require_deployment(config: ClientConfig, builders: IntegrationBuilders, what: str) -> None:
    """What a source apart from the store needs of the deployment, checked before anything is
    built: an operational store and a consent registry."""
    client_id = config.client_id
    if builders.operational_stores is None:
        raise RuntimeBuildError(
            f"{client_id}: operational store: {what} needs one, and this deployment "
            "has none (set SALES_AGENT_DATABASE_URL)"
        )
    if builders.consent is None:
        raise ClientUnavailableError(
            f"{client_id}: consent registry: {what} needs one, and this deployment has none"
        )


def _open_state(config: ClientConfig, builders: IntegrationBuilders):
    """The client's operational store (verified) and its consent registry (healthy): the state
    Revansa keeps itself for every source that is only a source. Each failure is explicit."""
    client_id = config.client_id
    open_store, open_consent = builders.operational_stores, builders.consent
    assert open_store is not None and open_consent is not None  # `_require_deployment`
    store = _stage(client_id, "operational store", lambda: open_store(client_id))
    checked = _stage(client_id, "operational store", store.verify)
    problems = [p for p in checked if not p.startswith("warning:")]
    if problems:
        raise RuntimeBuildError(f"{client_id}: operational store: {'; '.join(problems)}")
    try:
        consent = open_consent(client_id)
        snapshot = consent.snapshot()
    except Exception as error:
        raise ClientUnavailableError(
            f"{client_id}: consent registry: {error.__class__.__name__}"
        ) from error
    if not snapshot.healthy:  # it would block everyone: say so rather than run on nothing
        raise ClientUnavailableError(
            f"{client_id}: consent registry: not ready ({snapshot.reason or snapshot.health.value})"
        )
    return store, consent


def _build_repository(config: ClientConfig, builders: IntegrationBuilders) -> SalesRepository:
    """What the Core reads and writes for a client with one source, from the source it chose.

    The store's own provider as the source (a spreadsheet that holds everything) is used as it
    always was. Any other source is composed with the client's operational store and consent
    registry. There is no third way: a source that cannot be run, a deployment without a store or a
    registry, a registry that is not ready, a store that cannot be used: each is an explicit error,
    and the client never silently keeps running on the store's own provider instead.
    """
    spec, client_id = config.spec, config.client_id
    if not spec.source_apart_from_store:
        return _stage(
            client_id,
            "repository",
            lambda: _choose(client_id, builders.repositories, spec.sheets.provider, "sheets")(
                config
            ),
        )

    provider = spec.source_provider
    build_source = builders.sources.get(provider)
    if build_source is None:
        raise RuntimeBuildError(
            f"{client_id}: source: {provider!r} apart from the store "
            f"({spec.sheets.provider!r}) cannot be run yet"
        )
    _require_deployment(config, builders, f"source {provider!r}")
    source = _stage(client_id, "source", lambda: build_source(config))
    store, consent = _open_state(config, builders)
    return _stage(client_id, "repository", lambda: CompositeRepository(source, store, consent))


def _converts_prices(settings: object) -> bool:
    """Whether a payment method names the currency its buyers pay in (`pay_currency`): only then is
    a rate ever needed, so only then is the rate source consulted."""
    return "pay_currency" in json.dumps(settings, default=str)


def _telegram_first_contact(config: ClientConfig) -> bool:
    """Whether a client on its own Telegram account may be written to first: that is the account mode
    (a bot cannot write to someone who never started it)."""
    if "telegram" not in config.spec.channel_providers:
        return False
    return account_mode_of(config.spec.channel_settings_for("telegram"))


def _metered(
    provider: Any, config: ClientConfig, builders: IntegrationBuilders
) -> MessagingChannel:
    """The channel of this client as the Engine uses it. A metered channel (e-mail, whichever mode)
    goes behind the daily send cap here, the one place a client's channel is built for the Engine:
    prospecting, replies and follow-ups all pass through it. Without a guard (no database) it can
    send nothing. The daily report builds its own channel and takes its place in the same counter."""
    channel = provider.build(config)
    if provider.metered:
        if builders.send_guards is None:
            channel = RefusingChannel(provider.kind)
        else:
            channel = SendCapChannel(
                channel, builders.send_guards(config.client_id, provider.kind.value)
            )
    if builders.send_allowed is not None:  # a Free account sends nothing, on any channel
        channel = CommercialGateChannel(channel, config.client_id, builders.send_allowed)
    if getattr(builders, "signals", None) is not None:  # failed sends open a notification, a good one closes it
        channel = NotifyingChannel(channel, config.client_id, builders.signals)
    return channel


def _build_lane_parts(
    config: ClientConfig, builders: IntegrationBuilders, clock: Clock
) -> tuple[SalesRepository, ChannelRouter, ClientWriteback | None]:
    """The repository, the router and the write-back of a client that runs lanes: one source per `sources` entry,
    one channel per channel provider its lanes use, and the lanes that tie them together.

    The client starts only if its whole configuration is valid. What is wrong with it that can be
    known before any connection (the lanes themselves) is reported at once; what building reveals (a
    source or a channel that cannot be made from its settings and credentials) is collected across
    every source and channel and reported together, never one at a time. Lanes need Revansa's own
    operational store (no source here holds the client's state) and the consent registry.
    """
    from sales_app.config.lanes import lane_problems  # here: it needs the provider registry

    spec, client_id = config.spec, config.client_id
    assert spec.lanes is not None and spec.sources is not None
    invalid = [f"lanes: {problem}" for problem in lane_problems(spec)]
    if invalid:
        raise RuntimeBuildError(f"{client_id}: " + "; ".join(invalid))
    _require_deployment(config, builders, "a client with lanes")

    problems: list[str] = []
    sources: dict[str, Any] = {}
    for instance in spec.sources:
        build = builders.source_instances.get(instance.provider)
        if build is None:
            problems.append(f"source {instance.id!r}: {instance.provider!r} cannot be run yet")
            continue
        try:
            sources[instance.id] = build(config, instance)
        except Exception as error:  # noqa: BLE001 - each builder words its own failure
            problems.append(f"source {instance.id!r}: {error}")

    adapters: dict[Channel, MessagingChannel] = {}
    kinds: dict[str, Channel] = {}
    for key in spec.channel_providers:
        provider = builders.channels.get(key)
        if provider is None:
            problems.append(f"channel {key!r}: unknown channel provider")
            continue
        kinds[key] = provider.kind
        if config.effective_mode is ApprovalMode.DRY_RUN:
            adapters[provider.kind] = DisabledChannel(provider.kind)  # cannot send, by construction
            continue
        try:
            adapters[provider.kind] = _metered(provider, config, builders)
        except Exception as error:  # noqa: BLE001
            problems.append(f"channel {key!r}: {error}")
    if problems:
        raise RuntimeBuildError(
            f"{client_id}: " + "; ".join(config.secrets.redact(p) for p in problems)
        )

    store, consent = _open_state(config, builders)
    repository = _stage(
        client_id, "repository", lambda: CompositeRepository(sources, store, consent)
    )
    router = _stage(
        client_id,
        "router",
        lambda: ChannelRouter(
            adapters,
            [
                LaneRoute(
                    lane_id=lane.id,
                    channel=kinds[lane.channel],
                    product_ids=() if lane.offers_all_products else lane.products,
                    source_id=lane.source,
                )
                for lane in spec.lanes or ()
            ],
        ),
    )
    writeback, problems = build_client_writeback(
        config, sources, store, consent, builders.writeback, clock
    )
    if problems:
        raise RuntimeBuildError(
            f"{client_id}: " + "; ".join(config.secrets.redact(p) for p in problems)
        )
    return repository, router, writeback


def build_runtime(
    config: ClientConfig,
    builders: IntegrationBuilders,
    clock: Clock,
    *,
    stagger: timedelta = timedelta(0),
    brains: BrainRegistry | None = None,
) -> ClientRuntime:
    """Assemble one client's Engine. Everything here comes from that client's own config."""
    spec, client_id = config.spec, config.client_id

    router: ChannelRouter | None = None
    writeback: ClientWriteback | None = None
    if spec.lanes is not None:
        repository, router, writeback = _build_lane_parts(config, builders, clock)
    else:
        repository = _build_repository(config, builders)
    llm = _stage(
        client_id,
        "llm",
        lambda: _choose(client_id, builders.llms, spec.llm.provider, "llm")(config),
    )
    llm = _record_usage(config, builders, llm)
    if getattr(builders, "signals", None) is not None:  # quota / credential / model errors open a notification
        llm = NotifyingLLMProvider(llm, client_id, builders.signals)
    brain = _stage(client_id, "brain", lambda: _build_brain(spec.brain, llm, brains))
    _attach_knowledge(brain, builders, client_id)
    channel: MessagingChannel | None = None
    if router is None:
        channel_key = channel_provider_key(config)
        if channel_key is None:
            raise RuntimeBuildError(f"{client_id}: {NO_CHANNEL_REASON}")
        what = "channel" if isinstance(config, CanonicalClientConfig) else "whatsapp"
        provider = _choose(client_id, builders.channels, channel_key, what)
        if config.effective_mode is ApprovalMode.DRY_RUN:
            channel = DisabledChannel(provider.kind)  # a dry run cannot send, by construction
        else:
            channel = _stage(client_id, "channel", lambda: _metered(provider, config, builders))

    reporter = _activity_reporter(builders, client_id)
    learning_sink, learning_notes = _learning_ports(builders, client_id)
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
            router=router,
            clock=clock,
            facts=repository if isinstance(repository, FactsReader) else None,
            facts_writer=(
                repository
                if isinstance(repository, FactsReader) and isinstance(repository, FactsWriter)
                else None
            ),
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
                telegram_first_contact=_telegram_first_contact(config),
            ),
            exchange_rates=today_rates if _converts_prices(spec.brain.settings) else None,
            control=(
                builders.conversation_control(client_id)
                if builders.conversation_control is not None
                else None
            ),
            activity_reporter=reporter,
            trace_sink=learning_sink,
            learned_notes=learning_notes,
        ),
    )
    return ClientRuntime(
        config=config, engine=engine, stagger=stagger, writeback=writeback, activity=reporter
    )
