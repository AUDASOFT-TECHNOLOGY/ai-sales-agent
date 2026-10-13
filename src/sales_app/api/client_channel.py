"""Client API: how a client talks to its prospects (its channel), and the password that needs.

Like `client.py`, `client_agent.py` and `client_source.py`, no route takes a client id: who is asking
comes only from the session cookie, so nothing the browser sends can reach another client's channel.

- **The channel** (`GET/PUT /client/v1/channel`): the client sees only the channels the Admin allows
  it, and chooses among them. A channel it is not allowed is refused (403) when chosen, and one the
  Admin takes away afterwards stops working at once: the agent is no longer ready and takes no new
  work, whatever the client had set up. A write carries the version it was based on and is refused
  (409) if the settings moved; it is validated by the same rules as every publication (422) and
  **nothing is written** if it is not valid.
- **The mailbox** (`email` in that body) holds the non-secret settings of the client's own mailbox:
  addresses, servers, ports. WhatsApp is not configured here at all: its account is connected by the
  administrator, so a body that names anything of WhatsApp or Meta is refused (422).
- **The password** (`PUT/DELETE /client/v1/channel/credentials`) is written, never read: no response
  and no message carries it, whatever happens, and a body that is not exactly what is expected is
  refused without being echoed.
- **The test** (`POST /client/v1/channel/test`) logs in to the sending server and to the mailbox
  with what is stored, without sending or reading a message. A success proves the access works at
  that moment and nothing more; a failure is a fixed sentence.

- **Telegram** is the client's own bot. The client gives the token BotFather made
  (`PUT /channel/credentials`, body exactly `{"bot_token": ...}`), then **connects** it
  (`POST /channel/connect`): the platform generates the webhook secret, tells Telegram where to
  deliver and keeps the secret, write-only like the token. A Telegram bot cannot write to a person
  who has not started it, so the prospects who have not are listed with a personal invitation link
  (`GET /channel/invitations`) for the client to send them; they wait, they are never lost.

Switching channel pauses an agent that was running: the client starts it again deliberately.
"""

import contextlib
import secrets
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from threading import Lock
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from sales_app.api.admin_clients import client_wiring_and_document, publish_client_settings
from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_agent import switch_available
from sales_app.bootstrap import Application
from sales_app.channel_status import (
    EMAIL,
    TELEGRAM,
    allowed_channels,
    channel_problem,
    channel_provider_of,
    channel_settings_of,
)
from sales_app.client_profiles import ClientProfile
from sales_app.config.email_settings import EmailChannelSettings
from sales_app.config.secrets import EMAIL_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
from sales_app.config.telegram_settings import TelegramChannelSettings, bot_id_of
from sales_app.config_store import ConfigStoreError
from sales_app.connection_tests import (
    BotProbe,
    BotProbeFailure,
    ConnectionResult,
    MailProbe,
    check_email,
    check_telegram,
)
from sales_app.errors import ClientNotReadyError
from sales_app.secret_store import (
    InvalidCredentialError,
    SecretStoreError,
    UnknownCredentialError,
    client_scope,
)
from sales_app.secret_store.catalog import normalize_value, spec_for
from sales_app.wiring.providers import ProviderKind
from sales_app.wiring.providers import email as email_provider
from sales_app.wiring.providers import telegram as telegram_provider
from sales_integrations.telegram import invitation_link, make_invitation

TEST_MIN_INTERVAL_SECONDS = 3.0

MailProbeFactory = Callable[[EmailChannelSettings, str], MailProbe]
BotProbeFactory = Callable[[str], BotProbe]  # from the bot token

# What this route may change in a stored settings document: the channel and its non-secret settings.
CHANNEL_SETTINGS_SECTIONS: dict[str, tuple[str, ...]] = {"channel": ("provider", "settings")}

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=254)]


# ---- models -----------------------------------------------------------------------------------


class EmailSettingsChanges(BaseModel):
    """The non-secret settings of a mailbox. A key left out keeps its value; none of them may be a
    password (there is no such field: `extra="forbid"`)."""

    model_config = ConfigDict(extra="forbid")

    from_address: str | None = None
    from_name: str | None = None
    username: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: str | None = None
    mailbox: str | None = None
    subject: str | None = None


class TelegramSettingsChanges(BaseModel):
    """The sentences the bot uses before it knows who it is talking to. A key left out keeps its
    value; there is no token field (`extra="forbid"`: that has its own route)."""

    model_config = ConfigDict(extra="forbid")

    contact_prompt: str | None = None
    contact_button: str | None = None
    not_found_text: str | None = None


class UpdateChannelRequest(BaseModel):
    """`extra="forbid"`: any other key (a Meta setting, the model, the mode...) answers 422."""

    model_config = ConfigDict(extra="forbid")

    # The version these changes were made on (null: none was published yet). Required, never
    # defaulted: a write that does not say what it is based on could silently undo someone else's.
    expected_version: int | None
    channel: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
    email: EmailSettingsChanges | None = None
    telegram: TelegramSettingsChanges | None = None


class ChannelOption(BaseModel):
    key: str
    label: str


class EmailSettingsResponse(BaseModel):
    from_address: str | None = None
    from_name: str | None = None
    username: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: str | None = None
    mailbox: str | None = None
    subject: str | None = None


class TelegramSettingsResponse(BaseModel):
    contact_prompt: str
    contact_button: str
    not_found_text: str
    connected: bool  # the bot was connected: it has a webhook secret and Telegram was told


class CredentialStatusResponse(BaseModel):
    """Whether the password is stored. Never its value, never part of it."""

    label: str
    configured: bool
    updated_at: datetime | None = None


class ChannelResponse(BaseModel):
    version: int | None
    channel: str  # the chosen channel's key (the API never says "provider": see the contract test)
    authorized: bool  # False: the Admin does not allow it (any more): the agent cannot run on it
    options: list[ChannelOption]  # only the channels this client may choose
    email: EmailSettingsResponse | None = None  # when the channel is e-mail
    telegram: TelegramSettingsResponse | None = None  # when the channel is Telegram
    credential: CredentialStatusResponse | None = None  # when the channel needs one from the client
    problem: str | None = None  # what to do next, when the channel cannot run the agent yet
    updated_at: datetime | None = None
    updated_by: Literal["client", "administrator"] | None = None


class InvitationResponse(BaseModel):
    prospect_id: str
    display_name: str
    phone: str | None = None
    email: str | None = None
    link: str | None = None  # None: the id is too long for a link; they can share their number


class InvitationsResponse(BaseModel):
    bot_username: str
    bot_link: str  # the bot itself, for anyone: they are asked to share their number
    waiting: int  # prospects who have not started the conversation yet
    invitations: list[InvitationResponse]  # the first `limit` of them


class ChannelTestResponse(BaseModel):
    channel: str
    ok: bool
    message: str  # a fixed sentence: never the password, never what the server said
    details: dict[str, str] = {}
    checked_at: datetime


# ---- helpers ----------------------------------------------------------------------------------


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _read_secret(request: Request, field: str) -> str:
    """The body must be exactly `{"<field>": "<text>"}`. Anything else is refused with a fixed
    sentence that repeats nothing of it (it may be a secret under the wrong name)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(body, dict) or set(body) != {field} or not isinstance(body[field], str):
        raise HTTPException(422, f'body must be {{"{field}": "<text>"}}')
    return body[field]


async def _read_update(request: Request) -> UpdateChannelRequest:
    """The body, validated by the model, with errors that say where and why but never *what*: a
    client who pastes a password into the wrong field must not get it back in the answer (the
    framework's own 422 repeats the offending input)."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    try:
        return UpdateChannelRequest.model_validate(data)
    except ValidationError as error:
        raise HTTPException(
            422,
            [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in error.errors()],
        ) from None


_CHANNEL_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["expected_version", "channel"],
                    "properties": {
                        "expected_version": {"type": ["integer", "null"]},
                        "channel": {"type": "string"},
                        "email": {
                            "type": "object",
                            "properties": {
                                name: {"type": "string"}
                                for name in EmailSettingsChanges.model_fields
                            },
                            "additionalProperties": False,
                        },
                        "telegram": {
                            "type": "object",
                            "properties": {
                                name: {"type": "string"}
                                for name in TelegramSettingsChanges.model_fields
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                }
            }
        },
    }
}

_SECRET_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "password": {"type": "string", "writeOnly": True},
                        "bot_token": {"type": "string", "writeOnly": True},
                    },
                    "minProperties": 1,
                    "maxProperties": 1,
                    "additionalProperties": False,
                }
            }
        },
    }
}


def create_client_channel_router(
    application: Application,
    mail_probe: MailProbeFactory = email_provider.MailProbe,
    bot_probe: BotProbeFactory = telegram_provider.TelegramProbe,
) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"], dependencies=[Depends(_no_store)])
    registry = application.provider_registry
    last_test: dict[str, float] = {}
    last_test_lock = Lock()

    def channel_options(client_id: str) -> list[ChannelOption]:
        allowed = allowed_channels(application, client_id)
        return [
            ChannelOption(key=d.key, label=d.label)
            for d in registry.list(ProviderKind.CHANNEL)
            if d.kind is ProviderKind.CHANNEL and d.runnable and d.key in allowed
        ]

    def _stored(client_id: str, key: str) -> bool:
        try:
            return application.credentials.status(client_scope(client_id), key).configured
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None

    def current_state(client_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any], int | None]:
        found = client_wiring_and_document(application, client_id)
        if found is None:
            raise HTTPException(404, "no such client")
        current = application.config_store.current(client_id)
        return found[0], found[1], current.version if current is not None else None

    def channel_response(client_id: str) -> ChannelResponse:
        raw, document, version = current_state(client_id)
        provider = channel_provider_of(raw, document)
        current = application.config_store.current(client_id)
        descriptor = registry.get(provider)
        email_settings = None
        if provider == EMAIL:
            email_settings = EmailSettingsResponse(
                **{
                    k: v
                    for k, v in channel_settings_of(document).items()
                    if k in EmailSettingsResponse.model_fields
                }
            )
        telegram_settings = None
        if provider == TELEGRAM:
            try:
                shown = TelegramChannelSettings.model_validate(channel_settings_of(document))
            except ValueError:  # settings that cannot be read are shown as the defaults they act as
                shown = TelegramChannelSettings()
            telegram_settings = TelegramSettingsResponse(
                **shown.model_dump(),
                connected=_stored(client_id, TELEGRAM_WEBHOOK_SECRET),
            )
        credential = None
        secret_key = _secret_key_for(provider)
        if secret_key is not None:
            try:
                status = application.credentials.status(client_scope(client_id), secret_key)
            except SecretStoreError:
                raise HTTPException(503, "credential storage is temporarily unavailable") from None
            credential = CredentialStatusResponse(
                label=status.label, configured=status.configured, updated_at=status.updated_at
            )
        return ChannelResponse(
            version=version,
            channel=provider,
            authorized=provider in allowed_channels(application, client_id),
            options=channel_options(client_id),
            email=email_settings,
            telegram=telegram_settings,
            credential=credential,
            problem=(
                channel_problem(application, client_id, provider, document)
                if descriptor is not None
                else "The channel you chose is not available. Choose another one."
            ),
            updated_at=current.created_at if current is not None else None,
            updated_by=(
                None
                if current is None
                else "client"
                if current.author == f"client:{client_id}"
                else "administrator"
            ),
        )

    # ---- the channel ----------------------------------------------------------------------

    @router.get("/channel", response_model=ChannelResponse)
    def get_channel(client: ClientProfile = Depends(get_current_client)) -> ChannelResponse:
        try:
            return channel_response(client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None

    @router.put("/channel", response_model=ChannelResponse, openapi_extra=_CHANNEL_BODY)
    async def put_channel(
        request: Request, client: ClientProfile = Depends(get_current_client)
    ) -> ChannelResponse:
        body = await _read_update(request)
        return await run_in_threadpool(apply_channel, body, client)

    def apply_channel(body: UpdateChannelRequest, client: ClientProfile) -> ChannelResponse:
        client_id = client.client_id
        descriptor = registry.get(body.channel)
        if (
            descriptor is None
            or descriptor.kind is not ProviderKind.CHANNEL
            or not descriptor.runnable
        ):
            raise HTTPException(422, "unknown channel")
        if body.channel not in allowed_channels(application, client_id):
            # Said as plainly for a channel that exists as for one that does not: a client learns
            # only what is open to it.
            raise HTTPException(403, "this channel is not available for your account")
        if body.email is not None and body.channel != EMAIL:
            raise HTTPException(422, "email settings belong to the email channel")
        if body.telegram is not None and body.channel != TELEGRAM:
            raise HTTPException(422, "telegram settings belong to the telegram channel")

        try:
            raw, document, version = current_state(client_id)
            if version != body.expected_version:
                raise HTTPException(
                    409, "the settings changed since they were loaded; reload and retry"
                )
            current_provider = channel_provider_of(raw, document)
            settings: dict[str, Any] = {}
            if body.channel == EMAIL:
                settings = _merged_email_settings(
                    channel_settings_of(document) if current_provider == EMAIL else {}, body.email
                )
                _require_unused_mailbox(application, client_id, settings)
            if body.channel == TELEGRAM:
                settings = _merged_telegram_settings(
                    channel_settings_of(document) if current_provider == TELEGRAM else {},
                    body.telegram,
                )
            if body.channel == current_provider and settings == (
                channel_settings_of(document) if current_provider in (EMAIL, TELEGRAM) else {}
            ):
                return channel_response(client_id)

            if body.channel != current_provider:
                _pause_agent(
                    client_id
                )  # a channel change is restarted deliberately, never silently
            publish_client_settings(
                application,
                client_id,
                {"channel": {"provider": body.channel, "settings": settings}},
                expected_version=body.expected_version,
                author=f"client:{client_id}",
                note="client channel",
                top_keys=(),
                sections=CHANNEL_SETTINGS_SECTIONS,
            )
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        if application.registry.get_config(client_id) is not None:
            with contextlib.suppress(Exception):  # the Admin's runtime status says how it ended
                application.runtime_manager.refresh_client(client_id)
        return channel_response(client_id)

    def _pause_agent(client_id: str) -> None:
        with switch_available():
            state = application.agent_state.get(client_id)
            if state is not None and state.running:
                application.agent_state.set(
                    client_id,
                    running=False,
                    by=f"client:{client_id}",
                    at=application.clock.now(),
                )

    # ---- the password ---------------------------------------------------------------------

    def require_secret_channel(client_id: str) -> tuple[str, str, str]:
        """(channel, the credential it holds for the client, the name of that credential in a body):
        what the client may write, for the channel it is on."""
        try:
            raw, document, _ = current_state(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        provider = channel_provider_of(raw, document)
        key = _secret_key_for(provider)
        if key is None:
            raise HTTPException(409, "the channel you chose needs no password from you")
        if provider not in allowed_channels(application, client_id):
            raise HTTPException(403, "this channel is not available for your account")
        return provider, key, _SECRET_FIELD[provider]

    @router.put(
        "/channel/credentials", response_model=CredentialStatusResponse, openapi_extra=_SECRET_BODY
    )
    async def put_credentials(
        request: Request, client: ClientProfile = Depends(get_current_client)
    ) -> CredentialStatusResponse:
        client_id = client.client_id
        provider, key, field = require_secret_channel(client_id)
        value = await _read_secret(request, field)
        try:
            if provider == TELEGRAM and reveal(client_id, key) != value.strip():
                # Another bot is not the one that was connected: the connection (and the
                # invitations signed under it) is dropped, and the client connects the new one. A
                # value that is not a token is refused first, so a mistake never disconnects a bot.
                normalize_value(spec_for(client_scope(client_id), key), value)
                application.credentials.remove(
                    client_scope(client_id), TELEGRAM_WEBHOOK_SECRET, actor=f"client:{client_id}"
                )
            status = application.credentials.save(
                client_scope(client_id),
                key,
                value,
                actor=f"client:{client_id}",
                apply=True,  # the agent of a client on this channel reads it: rebuild it
            )
        except InvalidCredentialError as error:
            raise HTTPException(422, str(error)) from None  # words the rule, never the value
        except UnknownCredentialError:
            raise HTTPException(409, "the channel you chose needs no password from you") from None
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return CredentialStatusResponse(
            label=status.label, configured=status.configured, updated_at=status.updated_at
        )

    @router.delete("/channel/credentials", response_model=CredentialStatusResponse)
    def delete_credentials(
        client: ClientProfile = Depends(get_current_client),
    ) -> CredentialStatusResponse:
        client_id = client.client_id
        provider, key, _ = require_secret_channel(client_id)
        try:
            if provider == TELEGRAM:
                application.credentials.remove(
                    client_scope(client_id), TELEGRAM_WEBHOOK_SECRET, actor=f"client:{client_id}"
                )
            status = application.credentials.remove(
                client_scope(client_id), key, actor=f"client:{client_id}"
            )
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return CredentialStatusResponse(
            label=status.label, configured=status.configured, updated_at=status.updated_at
        )

    # ---- the test -------------------------------------------------------------------------

    def throttle(client_id: str) -> None:
        with last_test_lock:
            now = time.monotonic()
            if (
                now - last_test.get(client_id, -TEST_MIN_INTERVAL_SECONDS)
                < TEST_MIN_INTERVAL_SECONDS
            ):
                raise HTTPException(429, "a test was just made: wait a few seconds")
            last_test[client_id] = now

    def reveal(client_id: str, key: str) -> str | None:
        try:
            return application.credentials.reveal(client_scope(client_id), key)
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None

    @router.post("/channel/test", response_model=ChannelTestResponse)
    def test_channel(client: ClientProfile = Depends(get_current_client)) -> ChannelTestResponse:
        client_id = client.client_id
        try:
            raw, document, _ = current_state(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        provider = channel_provider_of(raw, document)
        if provider not in allowed_channels(application, client_id):
            raise HTTPException(403, "this channel is not available for your account")
        if provider not in (EMAIL, TELEGRAM):
            raise HTTPException(409, "there is nothing for you to test on this channel")
        throttle(client_id)
        if provider == TELEGRAM:
            token = reveal(client_id, TELEGRAM_BOT_TOKEN)
            result = check_telegram(
                probe=bot_probe(token) if token else None,
                expected_url=telegram_provider.webhook_url(application.settings.public_base_url),
            )
            return ChannelTestResponse(
                channel=provider,
                ok=result.ok,
                message=result.message,
                details=dict(result.details),
                checked_at=application.clock.now(),
            )
        probe = None
        try:
            settings = EmailChannelSettings.model_validate(channel_settings_of(document))
            password = application.credentials.reveal(client_scope(client_id), EMAIL_PASSWORD)
        except ValueError:
            settings, password = None, None
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        if settings is not None and password:
            probe = mail_probe(settings, password)
        result: ConnectionResult = check_email(probe=probe)
        return ChannelTestResponse(
            channel=provider,
            ok=result.ok,
            message=result.message,
            details=dict(result.details),
            checked_at=application.clock.now(),
        )

    # ---- connecting a Telegram bot ---------------------------------------------------------

    def require_telegram(client_id: str) -> str:
        """The bot token of a client that is on Telegram and may be."""
        try:
            raw, document, _ = current_state(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        if channel_provider_of(raw, document) != TELEGRAM:
            raise HTTPException(409, "your channel is not Telegram")
        if TELEGRAM not in allowed_channels(application, client_id):
            raise HTTPException(403, "this channel is not available for your account")
        token = reveal(client_id, TELEGRAM_BOT_TOKEN)
        if not token:
            raise HTTPException(409, "add your Telegram bot token first")
        return token

    @router.post("/channel/connect", response_model=ChannelTestResponse)
    def connect_channel(client: ClientProfile = Depends(get_current_client)) -> ChannelTestResponse:
        """Tell Telegram to deliver this bot's messages to the platform, with a secret only the
        platform and Telegram know. Safe to repeat: the secret, and the links signed under it, stay."""
        client_id = client.client_id
        token = require_telegram(client_id)
        throttle(client_id)
        url = telegram_provider.webhook_url(application.settings.public_base_url)
        if url is None:
            raise HTTPException(
                409,
                "the platform has no public https address to receive Telegram messages on: "
                "contact your administrator",
            )
        bot = bot_id_of(token)
        for config in application.registry.configs():
            if config.client_id != client_id and config.telegram_bot_id == bot:
                raise HTTPException(409, "this bot cannot be used for your account")
        probe = bot_probe(token)
        secret = reveal(client_id, TELEGRAM_WEBHOOK_SECRET) or secrets.token_urlsafe(32)
        try:
            probe.check_bot()
            probe.register_webhook(url, secret)
        except BotProbeFailure as failure:
            result = check_telegram(probe=_Failed(failure), expected_url=url)
            return ChannelTestResponse(
                channel=TELEGRAM,
                ok=False,
                message=result.message,
                details=dict(result.details),
                checked_at=application.clock.now(),
            )
        try:
            # Stored only now that Telegram was told: "connected" must never be said before it is.
            application.credentials.save(
                client_scope(client_id),
                TELEGRAM_WEBHOOK_SECRET,
                secret,
                actor=f"client:{client_id}",
                apply=True,
            )
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        result = check_telegram(probe=probe, expected_url=url)
        return ChannelTestResponse(
            channel=TELEGRAM,
            ok=result.ok,
            message=result.message,
            details=dict(result.details),
            checked_at=application.clock.now(),
        )

    @router.get("/channel/invitations", response_model=InvitationsResponse)
    def get_invitations(
        limit: int = Query(200, ge=1, le=1000),
        client: ClientProfile = Depends(get_current_client),
    ) -> InvitationsResponse:
        """The prospects who have not started the conversation with the bot, each with the link to
        send them. The links are personal: pressing Start on one attaches that prospect to that
        person's Telegram, and to no one else's."""
        client_id = client.client_id
        token = require_telegram(client_id)
        secret = reveal(client_id, TELEGRAM_WEBHOOK_SECRET)
        if not secret:
            raise HTTPException(409, "connect your Telegram bot first")
        runtime = application.registry.get(client_id)
        if runtime is None:
            raise HTTPException(409, "your agent is not ready yet")
        try:
            username = bot_probe(token).check_bot()
        except BotProbeFailure:
            raise HTTPException(502, "Telegram could not be asked about your bot") from None
        if not username:
            raise HTTPException(502, "Telegram did not name your bot")
        try:
            with runtime.lease() as engine:
                waiting = engine.prospects_awaiting_opt_in()
        except ClientNotReadyError:
            raise HTTPException(409, "your agent is not ready yet") from None
        except Exception:  # noqa: BLE001 - the source answers or it does not; never its words
            raise HTTPException(502, "your prospects could not be read just now") from None
        items = []
        for prospect in waiting[:limit]:
            payload = make_invitation(secret, prospect.prospect_id)
            items.append(
                InvitationResponse(
                    prospect_id=prospect.prospect_id,
                    display_name=prospect.display_name,
                    phone=prospect.contact.phone,
                    email=prospect.contact.email,
                    link=invitation_link(username, payload) if payload else None,
                )
            )
        return InvitationsResponse(
            bot_username=username,
            bot_link=f"https://t.me/{username}",
            waiting=len(waiting),
            invitations=items,
        )

    return router


class _Failed:
    """A probe that is already known to have failed, so the same sentence is told as for a test."""

    def __init__(self, failure: BotProbeFailure) -> None:
        self._failure = failure

    def check_bot(self) -> str:
        raise self._failure

    def register_webhook(self, url: str, secret: str) -> None:
        raise self._failure

    def webhook_state(self, expected_url: str | None) -> str:
        raise self._failure


def _merged_telegram_settings(
    current: Mapping[str, Any], changes: TelegramSettingsChanges | None
) -> dict[str, Any]:
    """The bot's sentences after the changes, valid, or a 422 that names the fields but never
    repeats a value. Only what the client set is kept: the rest stays the default."""
    merged = dict(current)
    if changes is not None:
        merged.update(changes.model_dump(exclude_none=True))
    try:
        TelegramChannelSettings.model_validate(merged)
    except ValidationError as error:
        raise HTTPException(
            422,
            [
                {"loc": ["telegram", *map(str, e["loc"])], "type": e["type"], "msg": e["msg"]}
                for e in error.errors()
            ],
        ) from None
    return merged


_SECRET_FIELD = {EMAIL: "password", TELEGRAM: "bot_token"}
_SECRET_KEY = {EMAIL: EMAIL_PASSWORD, TELEGRAM: TELEGRAM_BOT_TOKEN}


def _secret_key_for(provider: str) -> str | None:
    """The credential a client writes for its channel, or None when it has none to give."""
    return _SECRET_KEY.get(provider)


def _merged_email_settings(
    current: Mapping[str, Any], changes: EmailSettingsChanges | None
) -> dict[str, Any]:
    """The mailbox settings after the changes, complete and valid, or a 422 that names the fields
    but never repeats a value."""
    merged = dict(current)
    if changes is not None:
        merged.update(changes.model_dump(exclude_none=True))
    try:
        return EmailChannelSettings.model_validate(merged).model_dump(
            mode="json", exclude_none=True
        )
    except ValidationError as error:
        raise HTTPException(
            422,
            [
                {"loc": ["email", *map(str, e["loc"])], "type": e["type"], "msg": e["msg"]}
                for e in error.errors()
            ],
        ) from None


def _require_unused_mailbox(
    application: Application, client_id: str, settings: Mapping[str, Any]
) -> None:
    """A mailbox belongs to one client: two reading it would take each other's replies. Never says
    whose it is."""
    key = EmailChannelSettings.model_validate(settings).mailbox_key
    for config in application.registry.configs():
        if config.client_id != client_id and config.mailbox_key == key:
            raise HTTPException(409, "this mailbox cannot be used for your account")
