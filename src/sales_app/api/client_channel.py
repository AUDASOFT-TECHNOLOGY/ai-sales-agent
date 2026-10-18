"""Client API: how a client talks to its prospects (its channel), and nothing technical about it.

Like `client.py`, `client_agent.py` and `client_source.py`, no route takes a client id: who is asking
comes only from the session cookie, so nothing the browser sends can reach another client's channel.

**The client never configures a channel's technology.** Servers, mailboxes, passwords, bot tokens and
webhooks are connected by the platform (Revansa Admin) and are not part of this API: it accepts none
of them and returns none of them. There is no route to write, remove, test or connect a credential
here; a body that names any technical setting is refused (422) without repeating it.

- **The channel** (`GET/PUT /client/v1/channel`): the client sees only the channels the Admin allows
  it, and chooses among them. A channel it is not allowed is refused (403) when chosen, and one the
  Admin takes away afterwards stops working at once: the agent is no longer ready and takes no new
  work, whatever the client had set up. A write carries the version it was based on and is refused
  (409) if the settings moved; it is validated by the same rules as every publication (422) and
  **nothing is written** if it is not valid.
- **What the client may say** is business wording only: for e-mail, the name it writes under and the
  subject of its messages; for Telegram, the sentences its bot uses before it knows who it is talking
  to. WhatsApp is not configured here at all: a body that names anything of WhatsApp or Meta is
  refused (422).
- **Where the channel stands** is `status`: `ready`, `waiting_platform` (Revansa has to finish
  setting it up; the client has nothing to do) or `action_required` (the client has to choose or fix
  something of its own), with a sentence that mentions nothing technical.
- **Telegram invitations** (`GET /channel/invitations`): a Telegram bot cannot write to a person who
  has not started it, so the prospects who have not are listed with a personal invitation link for
  the client to send them; they wait, they are never lost.

Switching channel pauses an agent that was running: the client starts it again deliberately.
"""

import contextlib
from collections.abc import Mapping
from datetime import datetime
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
    activation_of,
    allowed_channels,
    channel_provider_of,
    channel_settings_of,
    channel_status,
)
from sales_app.client_profiles import ClientProfile
from sales_app.config.email_activation import ActivationRecord
from sales_app.config.email_settings import (
    CUSTOM_MODE,
    GMAIL_MODE,
    PLATFORM_MODE,
    GmailEmailSettings,
    PlatformEmailSettings,
    email_mode_of,
    parse_email_settings,
)
from sales_app.config.secrets import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
from sales_app.config.telegram_settings import TelegramChannelSettings
from sales_app.config_store import ConfigStoreError
from sales_app.connection_tests import BotProbeFailure
from sales_app.email_activation import ActivationConflictError
from sales_app.errors import ClientNotReadyError
from sales_app.mailbox_access import sender_name
from sales_app.mailbox_exclusivity import mode_conflict
from sales_app.secret_store import SecretStoreError, client_scope
from sales_app.send_cap_status import email_sending
from sales_app.telegram_connection import BotProbeFactory
from sales_app.wiring.providers import ProviderKind
from sales_app.wiring.providers import telegram as telegram_provider
from sales_integrations.telegram import invitation_link, make_invitation

# What this route may change in a stored settings document: the channel and its business wording.
CHANNEL_SETTINGS_SECTIONS: dict[str, tuple[str, ...]] = {"channel": ("provider", "settings")}

# The only e-mail settings a client may state: how its messages are written.
EMAIL_WORDING = ("from_name", "subject")

# Where a channel stands, as the client is told. Mirrors `sales_app.channel_status`.
ChannelStatusName = Literal["ready", "waiting_platform", "action_required"]


# ---- models -----------------------------------------------------------------------------------


class EmailSettingsChanges(BaseModel):
    """How the client's e-mails are written. A key left out keeps its value. There is no field for a
    server, a port, a login or a password, and `extra="forbid"` refuses any that is sent."""

    model_config = ConfigDict(extra="forbid")

    from_name: str | None = None
    subject: str | None = None
    # Which mailbox: true, the client's own Gmail; false, back to the mailbox Revansa provides. They
    # exclude each other: the server refuses (409) a switch that would leave both. A client never
    # names a mode, a server or a mailbox (`extra="forbid"`).
    use_gmail: bool | None = None


class TelegramSettingsChanges(BaseModel):
    """The sentences the bot uses before it knows who it is talking to. A key left out keeps its
    value; there is no token field (`extra="forbid"`)."""

    model_config = ConfigDict(extra="forbid")

    contact_prompt: str | None = None
    contact_button: str | None = None
    not_found_text: str | None = None


class UpdateChannelRequest(BaseModel):
    """`extra="forbid"`: any other key (a Meta setting, a server, the model, the mode...) answers
    422."""

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


class ActivationResponse(BaseModel):
    """Where the client's request to have its e-mail activated stands. `reason` is the Admin's, shown
    when the request was rejected. Nothing here names a mailbox."""

    status: Literal["pending", "in_progress", "rejected", "active"]
    requested_at: datetime
    updated_at: datetime
    reason: str | None = None


def activation_response(record: ActivationRecord | None) -> ActivationResponse | None:
    if record is None:
        return None
    return ActivationResponse(
        status=record.status,  # type: ignore[arg-type]  # one of the four statuses
        requested_at=record.requested_at,
        updated_at=record.updated_at,
        reason=record.reason,
    )


class SendingResponse(BaseModel):
    """`ok`, or why no e-mail leaves: `not_set` (the limit is not set yet), `reached` (today's is
    used up), `unavailable` (it cannot be checked). `message` is null when `ok`."""

    state: Literal["ok", "not_set", "reached", "unavailable"]
    message: str | None = None
    daily_limit: int | None = None
    sent_today: int | None = None


class EmailSettingsResponse(BaseModel):
    """How the e-mails are written, and whose mailbox it is (`mode`: `platform`, a mailbox Revansa
    provides, or `custom`, the historical one). In `platform` mode `from_name` is the Sender Name the
    client confirmed, null until it has (then `sender_name_confirmed` is false and the page offers
    `sender_name_proposal` of `ChannelResponse`). Nothing here is technical."""

    mode: Literal["platform", "custom", "gmail"] = "platform"
    from_name: str | None = None
    sender_name_confirmed: bool = False
    subject: str | None = None
    # The request to have the mailbox activated; null until the client has made one. Only the
    # platform's mailbox has one.
    activation: ActivationResponse | None = None
    # Whether e-mails can leave today against the daily sending limit the Revansa team sets.
    sending: SendingResponse | None = None


class TelegramSettingsResponse(BaseModel):
    contact_prompt: str
    contact_button: str
    not_found_text: str


class ChannelResponse(BaseModel):
    version: int | None
    channel: str  # the chosen channel's key (the API never says "provider": see the contract test)
    authorized: bool  # False: the Admin does not allow it (any more): the agent cannot run on it
    options: list[ChannelOption]  # only the channels this client may choose
    status: ChannelStatusName
    status_message: str | None = None  # what the client should know, when it is not ready
    email: EmailSettingsResponse | None = None  # when the channel is e-mail
    # The client's commercial name, as a Sender Name it may start from (a proposal, never a
    # confirmation: only a saved `from_name` is one).
    sender_name_proposal: str | None = None
    telegram: TelegramSettingsResponse | None = None  # when the channel is Telegram
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


# ---- helpers ----------------------------------------------------------------------------------


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


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


def create_client_channel_router(
    application: Application,
    bot_probe: BotProbeFactory = telegram_provider.TelegramProbe,
) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"], dependencies=[Depends(_no_store)])
    registry = application.provider_registry

    def channel_options(client_id: str) -> list[ChannelOption]:
        allowed = allowed_channels(application, client_id)
        return [
            ChannelOption(key=d.key, label=d.label)
            for d in registry.list(ProviderKind.CHANNEL)
            if d.kind is ProviderKind.CHANNEL and d.runnable and d.key in allowed
        ]

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
            stored = channel_settings_of(document)
            mode = _email_mode(stored)
            email_settings = EmailSettingsResponse(
                mode=mode,
                from_name=stored.get("from_name"),
                # Only the platform's mailbox has a name to confirm: a historical one is the
                # client's own setting, as it always was.
                sender_name_confirmed=mode == "platform" and bool(stored.get("from_name")),
                subject=stored.get("subject"),
                activation=(
                    activation_response(activation_of(application, client_id))
                    if mode == "platform"
                    else None
                ),
                sending=SendingResponse(**email_sending(application, client_id)),
            )
        telegram_settings = None
        if provider == TELEGRAM:
            try:
                shown = TelegramChannelSettings.model_validate(channel_settings_of(document))
            except ValueError:  # settings that cannot be read are shown as the defaults they act as
                shown = TelegramChannelSettings()
            telegram_settings = TelegramSettingsResponse(**shown.model_dump())
        if descriptor is None:
            status, message = (
                "action_required",
                "The channel you chose is not available. Choose another one.",
            )
        else:
            status, message = channel_status(application, client_id, provider, document)
        return ChannelResponse(
            version=version,
            channel=provider,
            authorized=provider in allowed_channels(application, client_id),
            options=channel_options(client_id),
            status=status,  # type: ignore[arg-type]  # one of the three names of channel_status
            status_message=message,
            email=email_settings,
            telegram=telegram_settings,
            sender_name_proposal=sender_name(raw.get("display_name")),
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
            if body.channel == TELEGRAM:
                settings = _merged_telegram_settings(
                    channel_settings_of(document) if current_provider == TELEGRAM else {},
                    body.telegram,
                )
            if body.channel == EMAIL:
                conflict = mode_conflict(
                    application,
                    client_id,
                    channel_settings_of(document) if current_provider == EMAIL else {},
                    settings,
                )
                if conflict is not None:
                    raise HTTPException(409, conflict)
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

    @router.post("/channel/email-activation", response_model=ChannelResponse, status_code=201)
    def request_email_activation(client: ClientProfile = Depends(get_current_client)):
        """Ask for the e-mail to be activated. The Sender Name must have been confirmed (saved), the
        channel must be e-mail on a mailbox Revansa provides, and the Admin must allow Email to this
        client: a request is not that permission. One open or active request at most; after a
        rejection the client may ask again."""
        client_id = client.client_id
        service = application.email_activation
        if service is None:
            raise HTTPException(503, "email activation is not available")
        try:
            raw, document, _ = current_state(client_id)
            if EMAIL not in allowed_channels(application, client_id):
                raise HTTPException(403, "this channel is not available for your account")
            if channel_provider_of(raw, document) != EMAIL:
                raise HTTPException(409, "your channel is not email")
            stored = channel_settings_of(document)
            if _email_mode(stored) != "platform":
                raise HTTPException(409, "this account's email does not need an activation")
            if not sender_name(stored.get("from_name")):
                raise HTTPException(409, "confirm your sender name first")
            service.request(client_id, actor=f"client:{client_id}")
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        except SecretStoreError:
            raise HTTPException(503, "request storage is temporarily unavailable") from None
        except ActivationConflictError as conflict:
            raise HTTPException(409, str(conflict)) from None
        with contextlib.suppress(Exception):  # the Admin's runtime status says how it ended
            if application.registry.get_config(client_id) is not None:
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

    # ---- Telegram invitations -------------------------------------------------------------

    def reveal(client_id: str, key: str) -> str | None:
        try:
            return application.credentials.reveal(client_scope(client_id), key)
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None

    def telegram_ready(client_id: str) -> tuple[str, str]:
        """The bot token and the webhook secret of a client that is on Telegram, may be, and whose
        bot the platform has connected. Said as plainly for each way it is not."""
        try:
            raw, document, _ = current_state(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        if channel_provider_of(raw, document) != TELEGRAM:
            raise HTTPException(409, "your channel is not Telegram")
        if TELEGRAM not in allowed_channels(application, client_id):
            raise HTTPException(403, "this channel is not available for your account")
        token = reveal(client_id, TELEGRAM_BOT_TOKEN)
        secret = reveal(client_id, TELEGRAM_WEBHOOK_SECRET)
        if not token or not secret:
            raise HTTPException(409, "your Telegram channel is not ready yet")
        return token, secret

    @router.get("/channel/invitations", response_model=InvitationsResponse)
    def get_invitations(
        limit: int = Query(200, ge=1, le=1000),
        client: ClientProfile = Depends(get_current_client),
    ) -> InvitationsResponse:
        """The prospects who have not started the conversation with the bot, each with the link to
        send them. The links are personal: pressing Start on one attaches that prospect to that
        person's Telegram, and to no one else's."""
        client_id = client.client_id
        token, secret = telegram_ready(client_id)
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


def _email_mode(stored: Mapping[str, Any]) -> Literal["platform", "custom", "gmail"]:
    """`gmail` for the client's own Gmail, `custom` only for a valid historical mailbox. Anything
    else (the platform's mailbox, or settings not made yet) is what the next save turns into the
    platform's mailbox."""
    try:
        settings = parse_email_settings(stored)
    except ValueError:
        return "gmail" if email_mode_of(stored) == GMAIL_MODE else "platform"
    if isinstance(settings, GmailEmailSettings):
        return "gmail"
    return "platform" if isinstance(settings, PlatformEmailSettings) else "custom"


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


def _email_base(current: Mapping[str, Any]) -> dict[str, Any]:
    """The settings the client's wording is applied to. A mailbox that already works is kept exactly
    as it is, whatever mode it is in (the client never sees how it is wired). Anything else starts
    as the platform's mailbox, which holds nothing technical."""
    try:
        parse_email_settings(current)
    except ValueError:
        kept = {key: current[key] for key in EMAIL_WORDING if key in current}
        return {"mode": PLATFORM_MODE, **kept}
    return dict(current)


def _merged_email_settings(
    current: Mapping[str, Any], changes: EmailSettingsChanges | None
) -> dict[str, Any]:
    """The e-mail settings after the client's wording, valid, or a 422 that names the fields but
    never repeats a value."""
    merged = _email_base(current)
    if changes is not None:
        data = changes.model_dump(exclude_none=True)
        choice = data.pop("use_gmail", None)
        wanted = None if choice is None else (GMAIL_MODE if choice else PLATFORM_MODE)
        if wanted is not None and wanted != email_mode_of(merged):
            if email_mode_of(merged) == CUSTOM_MODE:
                # The historical mailbox is the Admin's: the client cannot swap it for another.
                raise HTTPException(
                    409, "your mailbox is set up by Revansa and cannot be changed here"
                )
            merged = {"mode": wanted, **{k: merged[k] for k in EMAIL_WORDING if k in merged}}
        merged.update(data)
    try:
        return parse_email_settings(merged).model_dump(mode="json", exclude_none=True)
    except ValidationError as error:
        raise HTTPException(
            422,
            [
                {"loc": ["email", *map(str, e["loc"])], "type": e["type"], "msg": e["msg"]}
                for e in error.errors()
            ],
        ) from None
