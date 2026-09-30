"""The HTTP surface: health and the inbound webhook. Thin: no commercial logic at all.

The webhook authenticates and parses through the channel adapter, finds the client that owns the
receiving account, has that client's Engine store the message (fast path), then queues the real
processing in the background and answers. Deciding what to reply is never done here.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from sales_app.api.admin import create_admin_router
from sales_app.api.admin_agents import create_admin_agents_router
from sales_app.api.admin_clients import create_admin_clients_router
from sales_app.bootstrap import Application
from sales_app.logs import client_logger
from sales_app.wiring import ClientRuntime
from sales_core.engine import InboundReceipt
from sales_core.ports import InvalidSignatureError, MalformedPayloadError

log = logging.getLogger(__name__)
SHUTDOWN_GRACE_SECONDS = 30


def create_app(application: Application, *, run_scheduler: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = asyncio.Event()
        task = None
        if run_scheduler:
            task = asyncio.create_task(
                application.scheduler.run(stop, application.settings.tick_seconds)
            )
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(application.close), timeout=SHUTDOWN_GRACE_SECONDS
                )
            except TimeoutError:
                log.warning("jobs still running after %ss; exiting anyway", SHUTDOWN_GRACE_SECONDS)

    app = FastAPI(title="AI Sales Agent Core", lifespan=lifespan)
    app.include_router(create_admin_router(application))
    app.include_router(create_admin_clients_router(application))
    app.include_router(create_admin_agents_router(application))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/clients")
    def health_clients() -> list[dict[str, object]]:
        return application.registry.health()

    @app.get("/webhooks/{provider}")
    def handshake(provider: str, request: Request) -> PlainTextResponse:
        parser = application.parsers.get(provider)
        if parser is None:
            raise HTTPException(404)
        challenge = parser.handshake(dict(request.query_params))
        if challenge is None:
            raise HTTPException(403)
        return PlainTextResponse(challenge)

    @app.post("/webhooks/{provider}")
    async def receive(provider: str, request: Request, background: BackgroundTasks):
        parser = application.parsers.get(provider)
        if parser is None:
            raise HTTPException(404)
        body = await request.body()
        try:
            events = parser.parse(body, request.headers)
        except InvalidSignatureError:
            log.warning("rejected %s webhook: invalid signature", provider)
            raise HTTPException(401) from None
        except MalformedPayloadError as error:
            log.warning("rejected %s webhook: malformed payload: %s", provider, error)
            raise HTTPException(400) from None

        handled = 0
        for event in events:
            runtime = application.registry.by_routing_key(provider, event.routing_key)
            if runtime is None:
                # Retrying can never help, and providers disable endpoints that keep failing.
                log.error(
                    "no client for %s routing key %r; message dropped", provider, event.routing_key
                )
                continue
            try:
                receipt = await run_in_threadpool(runtime.engine.receive_inbound, event.message)
            except Exception as error:  # noqa: BLE001 - answered with a 500 so the provider retries
                client_logger(__name__, runtime.client_id).exception(
                    "could not store inbound message"
                )
                runtime.health.record_failure("inbound", application.clock.now(), str(error))
                raise HTTPException(
                    500
                ) from None  # the provider redelivers; the Engine de-duplicates
            _report(runtime, "inbound", receipt.errors, application)
            background.add_task(_process, runtime, receipt, application)
            handled += 1
        return {"received": handled}

    return app


def _report(
    runtime: ClientRuntime, job: str, errors: tuple[str, ...], application: Application
) -> None:
    logger = client_logger(__name__, runtime.client_id)
    for message in errors:
        logger.error("%s: %s", job, message)
    runtime.health.record_success(job, application.clock.now(), errors)


def _process(runtime: ClientRuntime, receipt: InboundReceipt, application: Application) -> None:
    """Background task: let the client's Engine handle the stored reply."""
    try:
        report = runtime.engine.process_inbound(receipt)
    except Exception as error:  # noqa: BLE001 - recover_pending_replies() will pick it up later
        client_logger(__name__, runtime.client_id).exception("processing an inbound reply failed")
        runtime.health.record_failure("inbound", application.clock.now(), str(error))
        return
    _report(runtime, "inbound", report.errors, application)
