"""The HTTP surface: health and the inbound webhook. Thin: no commercial logic at all.

The webhook authenticates and parses through the channel adapter, finds the client that owns the
receiving account, has that client's Engine store the message (fast path), then queues the real
processing in the background and answers. Deciding what to reply is never done here.
"""

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from sales_app.api.admin import create_admin_router
from sales_app.api.admin_agents import create_admin_agents_router
from sales_app.api.admin_audit import create_admin_audit_router
from sales_app.api.admin_billing import create_admin_billing_router
from sales_app.api.admin_finance import create_admin_finance_router
from sales_app.api.client_billing import create_client_billing_router
from sales_app.api.admin_channels import create_admin_channels_router
from sales_app.api.admin_clients import create_admin_clients_router
from sales_app.api.admin_costs import create_admin_costs_router
from sales_app.api.admin_dashboard import create_admin_dashboard_router
from sales_app.api.admin_email_activation import create_admin_email_activation_router
from sales_app.api.admin_hubspot import create_admin_hubspot_router
from sales_app.api.admin_integrations import create_admin_integrations_router
from sales_app.api.admin_lanes import create_admin_lanes_router
from sales_app.api.admin_llm_pricing import create_admin_llm_pricing_router
from sales_app.api.admin_plans import create_admin_plans_router
from sales_app.api.admin_send_cap import create_admin_send_cap_router
from sales_app.api.admin_settings import create_admin_settings_router
from sales_app.api.admin_whatsapp import create_admin_whatsapp_router
from sales_app.api.audit_middleware import AuditMiddleware
from sales_app.api.client import create_client_router
from sales_app.api.client_agent import create_client_agent_router
from sales_app.api.client_approvals import create_client_approvals_router
from sales_app.api.client_channel import create_client_channel_router
from sales_app.api.client_dashboard import create_client_dashboard_router
from sales_app.api.client_agent_test import create_client_agent_test_router
from sales_app.api.client_conversation_control import create_client_conversation_control_router
from sales_app.api.client_followup import create_client_followup_router
from sales_app.api.client_gmail import create_application_gmail_router
from sales_app.api.client_hubspot_oauth import create_client_hubspot_oauth_router
from sales_app.api.client_connections import create_client_connections_router
from sales_app.api.client_lanes import create_client_lanes_router
from sales_app.api.client_mailbox import create_client_mailbox_router
from sales_app.api.client_notifications import create_client_notifications_router
from sales_app.api.client_payments import create_client_payments_router
from sales_app.api.client_prospects import create_client_prospects_router
from sales_app.api.client_reporting import create_client_reporting_router
from sales_app.api.client_resources import create_client_resources_router
from sales_app.api.client_sheets_oauth import create_client_sheets_oauth_router
from sales_app.api.client_source import (
    SheetsProbeFactory,
    create_client_source_router,
    default_sheets_probe,
)
from sales_app.api.client_training import create_client_training_router
from sales_app.api.client_usage import create_client_usage_router
from sales_app.api.client_whatsapp import create_client_whatsapp_router
from sales_app.api.payments import create_payments_router
from sales_app.bootstrap import Application
from sales_app.connection_tests import HttpClientFactory, default_http_client
from sales_app.email_platform import (
    MailLoginProbe,
    MailLoginProbeFactory,
    PlatformMailProbe,
    PlatformMailProbeFactory,
)
from sales_app.errors import ClientNotReadyError
from sales_app.logs import client_logger
from sales_app.mailbox_proof import verify_all
from sales_app.telegram_connection import BotProbeFactory
from sales_app.wiring import ClientRuntime
from sales_app.wiring.providers import telegram as telegram_provider
from sales_core.engine import InboundReceipt, InboundStatus
from sales_core.ports import InvalidSignatureError, MalformedPayloadError

log = logging.getLogger(__name__)
SHUTDOWN_GRACE_SECONDS = 30


def create_app(
    application: Application,
    *,
    run_scheduler: bool = True,
    connection_http: HttpClientFactory = default_http_client,
    sheets_probe: SheetsProbeFactory = default_sheets_probe,
    bot_probe: BotProbeFactory = telegram_provider.TelegramProbe,
    platform_mail_probe: PlatformMailProbeFactory = PlatformMailProbe,
    mail_login_probe: MailLoginProbeFactory = MailLoginProbe,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = asyncio.Event()
        task = None
        verify = None
        billing = None
        if run_scheduler:
            if application.billing_loop is not None:
                billing = asyncio.create_task(application.billing_loop.run(stop))
            task = asyncio.create_task(
                application.scheduler.run(stop, application.settings.tick_seconds)
            )
            # The proof of a login is kept in memory: after a restart the platform mailboxes are
            # checked again (login only, nothing is sent or read) so that clients are not left
            # waiting for an administrator to press a button.
            verify = asyncio.create_task(
                asyncio.to_thread(verify_all, application, mail_login_probe)
            )
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task
            if verify is not None:
                await asyncio.gather(verify, return_exceptions=True)
            if billing is not None:
                await asyncio.gather(billing, return_exceptions=True)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(application.close), timeout=SHUTDOWN_GRACE_SECONDS
                )
            except TimeoutError:
                log.warning("jobs still running after %ss; exiting anyway", SHUTDOWN_GRACE_SECONDS)

    app = FastAPI(title="AI Sales Agent Core", lifespan=lifespan)
    app.add_middleware(AuditMiddleware, application=application)  # the central audit log
    app.include_router(create_admin_router(application))
    app.include_router(create_admin_audit_router(application))
    app.include_router(create_client_notifications_router(application))
    app.include_router(create_admin_clients_router(application))
    app.include_router(create_admin_agents_router(application))
    app.include_router(create_admin_settings_router(application))
    app.include_router(create_admin_llm_pricing_router(application))
    app.include_router(create_admin_costs_router(application))
    app.include_router(create_client_usage_router(application))
    app.include_router(create_admin_plans_router(application))
    app.include_router(create_admin_send_cap_router(application))
    app.include_router(create_admin_lanes_router(application))
    app.include_router(create_admin_hubspot_router(application))
    app.include_router(create_admin_billing_router(application))
    app.include_router(create_admin_finance_router(application))
    app.include_router(create_admin_dashboard_router(application))
    app.include_router(create_payments_router(application))
    app.include_router(create_admin_channels_router(application))
    app.include_router(
        create_admin_integrations_router(
            application, connection_http, platform_mail_probe, bot_probe, mail_login_probe
        )
    )
    admin_whatsapp_router = create_admin_whatsapp_router(application)
    if admin_whatsapp_router is not None:
        app.include_router(admin_whatsapp_router)
    app.include_router(create_admin_email_activation_router(application, mail_login_probe))
    app.include_router(create_client_router(application))
    app.include_router(create_client_agent_router(application))
    app.include_router(create_client_approvals_router(application))
    app.include_router(create_client_payments_router(application))
    app.include_router(create_client_billing_router(application))
    app.include_router(create_client_dashboard_router(application))
    app.include_router(create_client_resources_router(application))
    app.include_router(create_client_training_router(application))
    app.include_router(create_client_prospects_router(application))
    app.include_router(create_client_conversation_control_router(application))
    app.include_router(create_client_agent_test_router(application))
    app.include_router(create_client_followup_router(application))
    app.include_router(create_client_source_router(application, connection_http, sheets_probe))
    sheets_oauth_router = create_client_sheets_oauth_router(application)
    if sheets_oauth_router is not None:
        app.include_router(sheets_oauth_router)
    hubspot_oauth_router = create_client_hubspot_oauth_router(application)
    if hubspot_oauth_router is not None:
        app.include_router(hubspot_oauth_router)
    app.include_router(create_client_channel_router(application, bot_probe))
    app.include_router(create_client_mailbox_router(application))
    whatsapp_router = create_client_whatsapp_router(application)
    if whatsapp_router is not None:
        app.include_router(whatsapp_router)
    gmail_router = create_application_gmail_router(application)
    if gmail_router is not None:
        app.include_router(gmail_router)
    connections_router = create_client_connections_router(application)
    if connections_router is not None:
        app.include_router(connections_router)
    app.include_router(create_client_lanes_router(application))
    app.include_router(create_client_reporting_router(application))

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

    @app.post("/webhooks/whatsapp/deauthorize")
    async def whatsapp_deauthorize(request: Request) -> Response:
        """Meta tells us a person removed the app from their Facebook account. Their WhatsApp
        authorisation is gone: everything held for it is forgotten here."""
        client_id = await _signed_client(application, request)
        if client_id is not None:
            await run_in_threadpool(application.whatsapp.forget_locally, client_id)
            log.info("%s: WhatsApp authorisation removed at Meta's request", client_id)
        return Response(status_code=200)

    @app.post("/webhooks/whatsapp/data-deletion")
    async def whatsapp_data_deletion(request: Request) -> dict[str, str]:
        """Meta's data-deletion request: forget the person's WhatsApp account at once and answer
        with the status address and a confirmation code Meta shows to the person."""
        client_id = await _signed_client(application, request)
        if client_id is not None:
            await run_in_threadpool(application.whatsapp.forget_locally, client_id)
            log.info("%s: WhatsApp data deleted at Meta's request", client_id)
        code = secrets.token_hex(8)
        base = (application.settings.public_base_url or "").rstrip("/")
        return {"url": f"{base}/webhooks/whatsapp/data-deletion/status", "confirmation_code": code}

    @app.get("/webhooks/whatsapp/data-deletion/status")
    def whatsapp_data_deletion_status() -> dict[str, str]:
        return {
            "status": "completed",
            "detail": "The WhatsApp authorisation and the data held for it were deleted on receipt.",
        }

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

        if provider == WHATSAPP_PROVIDER:
            await _whatsapp_notifications(application, parser, body, request.headers)

        handled = 0
        for event in events:
            runtime = application.registry.by_routing_key(provider, event.routing_key)
            if runtime is None:
                # Retrying can never help, and providers disable endpoints that keep failing.
                _drop(application, provider, event.routing_key)
                continue
            if provider == WHATSAPP_PROVIDER and application.whatsapp is not None:
                # The person wrote: their 24-hour window opens before anything else, so the reply
                # that follows may go as free text.
                application.whatsapp.note_inbound(runtime.client_id, event.message)
            try:
                receipt = await run_in_threadpool(_receive, runtime, event.message)
            except ClientNotReadyError:  # it lost a credential since the lookup above
                _drop(application, provider, event.routing_key)
                continue
            except Exception as error:  # noqa: BLE001 - answered with a 500 so the provider retries
                client_logger(__name__, runtime.client_id).exception(
                    "could not store inbound message"
                )
                runtime.health.record_failure("inbound", application.clock.now(), str(error))
                raise HTTPException(
                    500
                ) from None  # the provider redelivers; the Engine de-duplicates
            _report(runtime, "inbound", receipt.errors, application)
            if application.agent_may_work(runtime.client_id):
                if receipt.status is InboundStatus.UNKNOWN_SENDER:
                    background.add_task(
                        _answer_unknown, runtime, provider, event.message, application
                    )
                else:
                    background.add_task(_process, runtime, receipt, application)
            # else: the message is stored; the reply waits (recover_pending_replies) until the
            # client has started its agent again, or the Admin has enabled the client again.
            handled += 1
        return {"received": handled}

    return app


WHATSAPP_PROVIDER = "whatsapp"


async def _whatsapp_notifications(
    application: Application, parser: Any, body: bytes, headers: Any
) -> None:
    """Delivery statuses, template approvals and account changes. They are authenticated with the
    app secret (they carry no phone number to route by) and applied to the client they concern.
    One that cannot be applied is logged and skipped: Meta is not made to retry statuses forever."""
    service = application.whatsapp
    notifications = getattr(parser, "notifications", None)
    if service is None or notifications is None:
        return
    try:
        found = notifications(body, headers)
    except InvalidSignatureError:
        log.warning("rejected whatsapp webhook: invalid signature")
        raise HTTPException(401) from None
    except MalformedPayloadError as error:
        log.warning("whatsapp notifications unreadable: %s", error)
        return
    for notification in found:
        try:
            await run_in_threadpool(service.handle, notification)
        except Exception:
            log.exception("could not apply a whatsapp %s notification", notification.kind)


async def _signed_client(application: Application, request: Request) -> str | None:
    """The client a Meta `signed_request` (deauthorisation, data deletion) is about. A call that
    the app secret does not sign is refused (401); an unknown person is answered as handled."""
    service = application.whatsapp
    if service is None:
        raise HTTPException(404)
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    signed = (form.get("signed_request") or [""])[0]
    client_id, valid = await run_in_threadpool(service.client_of_signed_request, signed)
    if not valid:
        raise HTTPException(401)
    return client_id


def _drop(application: Application, provider: str, routing_key: str) -> None:
    """Answer 200 and forget the message: a retry cannot help, and providers disable endpoints
    that keep failing. A client that is only waiting for credentials is counted and logged."""
    registry = application.registry
    waiting = registry.not_ready_client_for(provider, routing_key)
    if waiting is None:
        log.error("no client for %s routing key %r; message dropped", provider, routing_key)
        return
    registry.note_dropped(waiting, application.clock.now())
    client_logger(__name__, waiting).error(
        "client is not ready (missing credentials); inbound message dropped"
    )


def _receive(runtime: ClientRuntime, message: Any) -> InboundReceipt:
    with runtime.lease() as engine:
        return engine.receive_inbound(message)


def _report(
    runtime: ClientRuntime, job: str, errors: tuple[str, ...], application: Application
) -> None:
    logger = client_logger(__name__, runtime.client_id)
    for message in errors:
        logger.error("%s: %s", job, message)
    runtime.health.record_success(job, application.clock.now(), errors)


def _answer_unknown(
    runtime: ClientRuntime, provider: str, message: Any, application: Application
) -> None:
    """Background task: a stranger wrote on a channel that has something to say to them (Telegram
    asks for their phone number). Never fatal: the message was stored or audited already."""
    builders = application.builders
    channel = builders.channels.get(provider) if builders is not None else None
    if channel is None or channel.answer_unknown is None:
        return
    try:
        channel.answer_unknown(runtime.config, message)
    except Exception:  # noqa: BLE001 - nothing to retry: the person can simply write again
        client_logger(__name__, runtime.client_id).exception("could not answer an unknown sender")


def _process(runtime: ClientRuntime, receipt: InboundReceipt, application: Application) -> None:
    """Background task: let the client's Engine handle the stored reply."""
    try:
        with runtime.lease() as engine:
            report = engine.process_inbound(receipt)
    except ClientNotReadyError:
        return  # stored already; recover_pending_replies() handles it once the client is ready
    except Exception as error:  # noqa: BLE001 - recover_pending_replies() will pick it up later
        client_logger(__name__, runtime.client_id).exception("processing an inbound reply failed")
        runtime.health.record_failure("inbound", application.clock.now(), str(error))
        return
    _report(runtime, "inbound", report.errors, application)
