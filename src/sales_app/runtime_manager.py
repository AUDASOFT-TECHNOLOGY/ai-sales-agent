"""Applying a changed credential to the running application, without a restart.

For one client: resolve its configuration again, build a new Engine offline, and only if that
worked hand it to the client's runtime (`ClientRuntime.stage`), which installs it once the work
already running has finished. If anything fails, the old runtime is untouched and keeps working,
and the reason is kept here for the Admin to show. The runtime object, its `state` and its `health`
are never replaced, so nothing is lost and there is never a second Engine for a client.

Changes are applied one at a time by a single background worker, so two refreshes of one client
can never interleave. Saving a credential therefore returns at once; the outcome is read from
`status()`:

- `applying`: being built or waiting for running work to end.
- `applied`: the new runtime is in force.
- `pending`: running work did not end within `APPLY_WAIT_SECONDS`. The new runtime stays queued
  and is installed as soon as the client is next idle; meanwhile new work is no longer held back.
- `not_ready`: credentials are missing, so the client takes no new work.
- `failed`: the change could not be applied; the previous runtime is still in force.
"""

import logging
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic

from sales_app.config import ClientConfig, LLMRegistry, resolve_client_config
from sales_app.config.secrets import WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN
from sales_app.config_store import ConfigStore
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.errors import RuntimeBuildError
from sales_app.parsers import ParserTable, build_parsers
from sales_app.registry import STAGGER_SECONDS, ClientRegistry
from sales_app.secret_store import GLOBAL_SCOPE, ScopeKind, SecretStore, scope_kind
from sales_app.wiring import ClientRuntime, IntegrationBuilders, build_runtime
from sales_core.approval import ApprovalMode
from sales_core.ports import Clock

log = logging.getLogger(__name__)

# How long a refresh waits for a client's running job or message to end before it stops holding
# back new work and leaves the swap queued. The same grace the application allows running jobs at
# shutdown (`SHUTDOWN_GRACE_SECONDS` in sales_app.api.app).
APPLY_WAIT_SECONDS = 30.0

APPLYING, APPLIED, PENDING, NOT_READY, FAILED, IDLE = (
    "applying",
    "applied",
    "pending",
    "not_ready",
    "failed",
    "idle",
)
_PARSER_ONLY_KEYS = frozenset({WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET})


@dataclass(frozen=True, slots=True)
class RefreshStatus:
    state: str = IDLE
    at: datetime | None = None
    errors: tuple[str, ...] = ()  # what failed, or what is missing; never a secret value


@dataclass(slots=True)
class _Waiter:
    client_id: str
    runtime: ClientRuntime
    installed: threading.Event = field(default_factory=threading.Event)


class RuntimeManager:
    def __init__(
        self,
        *,
        clients_dir: Path,
        registry: ClientRegistry,
        builders: IntegrationBuilders,
        clock: Clock,
        env: Mapping[str, str],
        mode_ceiling: ApprovalMode,
        config_store: ConfigStore | None,
        llm_registry: LLMRegistry,
        secret_store: SecretStore,
        parsers: ParserTable,
        executor: Executor | None = None,
        apply_wait_seconds: float = APPLY_WAIT_SECONDS,
    ) -> None:
        self._clients_dir = clients_dir
        self._registry = registry
        self._builders = builders
        self._clock = clock
        self._env = env
        self._ceiling = mode_ceiling
        self._config_store = config_store
        self._llm_registry = llm_registry
        self._secrets = secret_store
        self._parsers = parsers
        self._owns_executor = executor is None
        self._executor: Executor = executor or ThreadPoolExecutor(
            1, thread_name_prefix="sales-refresh"
        )
        self._wait = apply_wait_seconds
        self._statuses: dict[str, RefreshStatus] = {}
        self._lock = threading.Lock()
        # Refreshes of one client queued and not started yet: asking again joins the queued one.
        self._queued: dict[str, Future[None]] = {}

    # ---- what the rest of the application calls ------------------------------------------

    def notify_change(self, scope: str, key: str) -> Future[None]:
        """A credential was stored or removed: apply it where it matters."""
        if scope_kind(scope) is ScopeKind.CLIENT:
            return self.refresh_client(scope.removeprefix("client:"))
        if scope == GLOBAL_SCOPE and key in _PARSER_ONLY_KEYS:
            return self._executor.submit(self._rebuild_parsers)  # no runtime uses these
        return self.refresh_all()

    def refresh_client(self, client_id: str) -> Future[None]:
        """Apply this client's current configuration. The worker is one thread shared by every
        client, and each refresh may wait for the client's running work, so several requests made
        while one is still queued are served by that one: a refresh resolves the newest
        configuration when it starts, whatever it was when it was asked for. One that is already
        running is not joined (it may have read an older configuration): a new one is queued."""
        with self._lock:
            queued = self._queued.get(client_id)
            if queued is not None:
                return queued
            future: Future[None] = Future()
            self._queued[client_id] = future

        def run() -> None:
            with self._lock:
                self._queued.pop(client_id, None)  # from now on, a new request queues its own
            try:
                self._refresh([client_id])
                future.set_result(None)
            except BaseException as error:  # noqa: BLE001 - handed to whoever waits on the future
                future.set_exception(error)

        try:
            self._executor.submit(run)
        except BaseException:
            with self._lock:
                self._queued.pop(client_id, None)
            raise
        return future

    def refresh_all(self) -> Future[None]:
        return self._executor.submit(self._refresh, self._known_client_ids())

    def status(self, client_id: str) -> RefreshStatus:
        with self._lock:
            return self._statuses.get(client_id, RefreshStatus())

    def shutdown(self) -> None:
        if self._owns_executor and isinstance(self._executor, ThreadPoolExecutor):
            self._executor.shutdown(wait=True)

    # ---- the work (runs on the worker) ---------------------------------------------------

    def _refresh(self, client_ids: Sequence[str]) -> None:
        try:
            waiters = [w for cid in client_ids if (w := self._apply(cid)) is not None]
            self._await(waiters)
            self._rebuild_parsers()
        except Exception:
            log.exception("refreshing client runtimes failed")

    def _apply(self, client_id: str) -> _Waiter | None:
        """Resolve, build offline, stage. Returns a waiter only if installation is still pending."""
        if not CLIENT_ID_PATTERN.match(client_id):
            return None
        self._set(client_id, APPLYING)
        resolution = resolve_client_config(
            self._clients_dir / client_id,
            env=self._env,
            mode_ceiling=self._ceiling,
            store=self._config_store,
            llm_registry=self._llm_registry,
            secret_store=self._secrets,
        )
        if resolution.disabled:
            self._set(client_id, IDLE)
            return None
        config = resolution.config
        if config is None:
            self._set(client_id, FAILED, resolution.errors)
            return None
        clash = self._phone_clash(config) or self._mailbox_clash(config) or self._bot_clash(config)
        if clash is not None:
            self._set(client_id, FAILED, (clash,))
            return None

        runtime = self._registry.get(client_id)
        if not config.ready:
            if runtime is not None:
                runtime.suspend(config)
            else:
                self._registry.set_not_ready(config)
            self._set(client_id, NOT_READY, config.missing_credentials)
            return None

        try:
            built = build_runtime(
                config,
                self._builders,
                self._clock,
                stagger=timedelta(seconds=STAGGER_SECONDS * len(self._registry.runtimes())),
            )
        except RuntimeBuildError as error:
            self._set(client_id, FAILED, (config.secrets.redact(str(error)),))
            return None
        if runtime is None:
            self._registry.add_runtime(built)
            self._set(client_id, APPLIED)
            return None

        waiter = _Waiter(client_id, runtime)

        def installed() -> None:
            waiter.installed.set()
            self._set(client_id, APPLIED)
            self._queue_parsers()  # a phone number id may have changed

        if runtime.stage(config, built.engine, installed):
            return None
        return waiter

    def _await(self, waiters: Sequence[_Waiter]) -> None:
        """Give running work one shared grace period; after it, stop holding new work back."""
        deadline = monotonic() + self._wait
        for waiter in waiters:
            waiter.installed.wait(max(0.0, deadline - monotonic()))
        for waiter in waiters:
            if waiter.installed.is_set():
                continue
            waiter.runtime.relax()
            with self._lock:
                if not waiter.installed.is_set():
                    self._statuses[waiter.client_id] = RefreshStatus(PENDING, self._clock.now())

    def _queue_parsers(self) -> None:
        try:
            self._executor.submit(self._rebuild_parsers)
        except RuntimeError:  # the application is shutting down: there is nothing left to serve
            log.debug("not rebuilding the webhook parsers: the refresh worker is shut down")

    def _rebuild_parsers(self) -> None:
        """Parsers from the credentials as they are now, plus the accounts already installed, so
        messages for a phone number id that is being replaced are still authenticated."""
        try:
            fresh = self._resolve_all()
            self._parsers.replace(
                build_parsers(fresh, self._builders, installed=self._registry.configs())
            )
        except Exception:
            log.exception("rebuilding the webhook parsers failed")

    # ---- helpers -------------------------------------------------------------------------

    def _resolve_all(self) -> list[ClientConfig]:
        configs: list[ClientConfig] = []
        for client_id in self._known_client_ids():
            resolution = resolve_client_config(
                self._clients_dir / client_id,
                env=self._env,
                mode_ceiling=self._ceiling,
                store=self._config_store,
                llm_registry=self._llm_registry,
                secret_store=self._secrets,
            )
            if resolution.config is not None:
                configs.append(resolution.config)
        return configs

    def _known_client_ids(self) -> list[str]:
        ids = {c.client_id for c in self._registry.configs()}
        if self._clients_dir.is_dir():
            ids |= {
                p.name
                for p in self._clients_dir.iterdir()
                if p.is_dir()
                and not p.name.startswith(("_", "."))
                and CLIENT_ID_PATTERN.match(p.name)
            }
        return sorted(ids)

    def _phone_clash(self, config: ClientConfig) -> str | None:
        """A phone number id must belong to one client; refuse to put it in force for a second."""
        phone = config.phone_number_id
        if not phone:
            return None
        for other in self._registry.configs():
            if other.client_id != config.client_id and other.phone_number_id == phone:
                return f"WhatsApp phone number id is already used by client {other.client_id!r}"
        return None

    def _mailbox_clash(self, config: ClientConfig) -> str | None:
        """A mailbox must belong to one client: two reading it would take each other's replies."""
        mailbox = config.mailbox_key
        if not mailbox:
            return None
        for other in self._registry.configs():
            if other.client_id != config.client_id and other.mailbox_key == mailbox:
                return f"the mailbox is already used by client {other.client_id!r}"
        return None

    def _bot_clash(self, config: ClientConfig) -> str | None:
        """A Telegram bot has one webhook: two clients on it would take each other's messages."""
        bot = config.telegram_bot_id
        if not bot:
            return None
        for other in self._registry.configs():
            if other.client_id != config.client_id and other.telegram_bot_id == bot:
                return f"the Telegram bot is already used by client {other.client_id!r}"
        return None

    def _set(self, client_id: str, state: str, errors: Sequence[str] = ()) -> None:
        with self._lock:
            self._statuses[client_id] = RefreshStatus(state, self._clock.now(), tuple(errors))


__all__ = ["APPLY_WAIT_SECONDS", "RefreshStatus", "RuntimeManager"]
