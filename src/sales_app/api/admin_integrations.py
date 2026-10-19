"""Admin API: credentials for the services Revansa talks to. Write-only.

A credential can be saved, replaced or removed, and its status read (`configured`, when, by whom).
No response ever carries a sensitive value; only the non-sensitive ones (a phone number id, an API
version) are shown. Only the OWNER role may change anything. Reading is open to any admin who can
reach the scope (global, or that client).

Request bodies are read by hand rather than through a Pydantic model: FastAPI's default 422 answer
echoes the rejected input, which here would be the secret. Errors below describe the rule that was
broken, never the value, and responses are marked `no-store`.
"""

import contextlib
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.config.secrets import EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX, OPENAI_MODELS
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.connection_tests import (
    INFERENCE_MAX_TOKENS,
    ConnectionResult,
    HttpClientFactory,
    check_openai,
    check_openai_inference,
    check_whatsapp_client,
    default_http_client,
)
from sales_app.email_platform import (
    AUTHENTICATED,
    NOT_CONFIGURED,
    MailLoginProbe,
    MailLoginProbeFactory,
    PlatformMailProbe,
    PlatformMailProbeFactory,
    check_platform_servers,
    fingerprint,
    login_problem,
    read_profile,
    run_login_check,
)
from sales_app.gmail_wiring import redirect_uri_for
from sales_app.hubspot_oauth import HUBSPOT_SCOPES
from sales_app.hubspot_wiring import CALLBACK_PATH as hubspot_callback_path
from sales_app.llm_models import ModelTest, foreign_model_keys, record_test, stored_models
from sales_app.mailbox_exclusivity import dedicated_credential_conflict
from sales_app.mailbox_proof import (
    NOT_AUTHENTICATED,
    READY,
    client_mailbox,
    protocol_states,
    verify_client_mailbox,
)
from sales_app.model_usage import impact_of_credential_write
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    CredentialStatus,
    DuplicateSecretError,
    InvalidCredentialError,
    ScopeKind,
    SecretStoreError,
    SecretStoreUnavailableError,
    UnknownCredentialError,
    client_scope,
)
from sales_app.secret_store.catalog import normalize_value, spec_for
from sales_app.sheets_wiring import CALLBACK_PATH as sheets_callback_path
from sales_app.telegram_account import AccountLoginError
from sales_app.telegram_connection import (
    BotProbeFactory,
    TelegramConnectionError,
    check_bot_connection,
    connect_bot,
    disconnect_bot,
)
from sales_app.wiring.providers import ProviderKind
from sales_app.wiring.providers import telegram as telegram_provider
from sales_integrations.mail import ImapSettings, SmtpSettings
from sales_integrations.whatsapp.channel import DEFAULT_API_VERSION

_BODY_SCHEMA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string", "writeOnly": True}},
                    "additionalProperties": False,
                }
            }
        },
    }
}


class CredentialResponse(BaseModel):
    key: str
    label: str
    scope: ScopeKind
    sensitive: bool
    configured: bool
    value: str | None = None  # only ever set for a non-sensitive credential
    updated_at: datetime | None = None
    updated_by: str | None = None


class RuntimeStatusResponse(BaseModel):
    """Whether a client is running on its current credentials, and how the last change fared.
    Nothing here is a credential: only names of what is missing and the reasons a change failed."""

    client_id: str
    readiness: str  # "ready" | "not_ready"
    missing_credentials: list[str] = []
    apply_state: str  # "idle" | "applying" | "applied" | "pending" | "not_ready" | "failed"
    apply_at: datetime | None = None
    apply_errors: list[str] = []


class ConnectionTestResponse(BaseModel):
    """The outcome of a real call to the service. Never carries a credential."""

    ok: bool
    message: str
    details: dict[str, str] = {}


class WhatsAppStatusResponse(BaseModel):
    """What is in place for one client's WhatsApp, from what is stored only: nothing here asked
    Meta, so nothing here says the number is connected (the test route does that)."""

    status: str  # ready | waiting_platform | action_required
    verified_with_meta: bool = False  # always false here: only the test route calls Meta
    whatsapp_number_given: bool
    reconnect_required: bool
    missing_platform: list[str]  # credential keys the Admin still has to store (no values)
    message: str


class ModelTestResponse(BaseModel):
    """The outcome of a real request to one model. Never carries a credential."""

    ok: bool  # the model answered: it is usable with the stored credentials
    outcome: str  # usable | no_api_key | invalid_api_key | model_not_found | invalid_parameters |
    #               quota_exceeded | timeout | unreachable | service_error | unexpected_response
    message: str
    model_id: str  # exactly the Model ID that was sent
    details: dict[str, str] = {}  # the service's error code, tokens used: never a credential
    request_sent: bool  # a real request went out (it may be billed); False: none did
    max_tokens: int  # the most the request could generate
    tested_at: str | None = None  # None: nothing was sent, so nothing was tested
    recorded: bool = False  # whether the outcome is kept with the model (the catalog shows it)


class _NotReady(Exception):
    """A login check that cannot start: the message is a fixed sentence naming what is missing."""


class EmailServiceStatus(BaseModel):
    """Whether the platform's mail servers can be used. Nothing here is a credential."""

    state: str  # "missing_configuration" | "server_not_authenticated" | "ready"
    smtp: str  # "not_configured" | "not_tested" | "authenticated" | "failed"
    imap: str
    missing: list[str] = []  # the server settings still to give
    smtp_problem: str | None = None  # why SMTP is not authenticated, as a fixed sentence
    imap_problem: str | None = None
    # Whether clients created from now on are given Email access (the Admin's switch). Existing
    # clients are never changed by it.
    new_clients_access: bool = False
    # Clients that may use Email and have no mailbox assigned yet: what waits for an assignment.
    clients_awaiting_mailbox: list[str] = []


class NewClientsAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class EmailMailboxStatus(BaseModel):
    """Where one client's mailbox stands. Nothing here is a credential or an address."""

    state: str  # "missing_configuration" | "mailbox_not_assigned" | "password_missing" |
    #            "not_authenticated" | "ready"
    smtp: str | None = None  # "not_tested" | "authenticated" | "failed", once there is a mailbox
    imap: str | None = None
    problem: str | None = None  # a fixed sentence when the mailbox is not ready
    smtp_problem: str | None = None
    imap_problem: str | None = None


def _test_response(result: ConnectionResult) -> ConnectionTestResponse:
    return ConnectionTestResponse(ok=result.ok, message=result.message, details=result.details)


def _response(status: CredentialStatus, scope: ScopeKind) -> CredentialResponse:
    return CredentialResponse(
        key=status.key,
        label=status.label,
        scope=scope,
        sensitive=status.sensitive,
        configured=status.configured,
        value=None if status.sensitive else status.value,  # belt and braces
        updated_at=status.updated_at,
        updated_by=status.updated_by,
    )


class GmailPlatformStatus(BaseModel):
    """Whether the Google application of the Gmail mailbox mode is set. `configured` means the
    credentials are stored and the public URL is usable: it does NOT mean Google accepts them (that
    is only proven by a real consent, and by Google's verification of the application)."""

    configured: bool
    missing: list[str]
    redirect_uri: str | None  # to copy into the Google Cloud OAuth client (not a secret)
    # The same Google OAuth client also serves "Connect Google Sheets": that flow has its own callback,
    # which must be registered next to the Gmail one (otherwise Google answers redirect_uri_mismatch).
    sheets_redirect_uri: str | None = None
    verified: bool = False  # never true here: nothing is checked against Google
    note: str


class HubSpotPlatformStatus(BaseModel):
    """Whether the HubSpot application clients connect their account with is set. `configured`
    means the credentials are stored and the public URL is usable: NOT that HubSpot accepts them."""

    configured: bool
    missing: list[str]
    redirect_uri: str | None  # to register in the HubSpot developer account (not a secret)
    scopes: list[str]  # the permissions the app must declare in HubSpot
    verified: bool = False  # never true here: nothing is checked against HubSpot
    note: str


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _read_value(request: Request) -> str:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(body, dict) or set(body) != {"value"} or not isinstance(body["value"], str):
        raise HTTPException(422, 'body must be {"value": "<text>"}')
    return body["value"]


class TelegramAccountPlatformStatus(BaseModel):
    """Whether the platform's Telegram application (my.telegram.org) is set, so clients can sign in with
    their own account. `configured` means stored and the library is installed: NOT that Telegram
    accepts them, which only a real sign-in proves."""

    configured: bool
    missing: list[str]
    note: str


class TelegramAccountAdminResponse(BaseModel):
    """Where a client's own Telegram account stands, for the Admin. Never a number, a session or a
    name: only whether it is connected, to be renewed, or limited by Telegram."""

    platform_configured: bool  # the platform's Telegram application and library are in place
    platform_missing: list[str]  # what the Admin still has to set (names only)
    state: str  # not_connected | connected | reauth_required | unavailable
    limited: bool  # Telegram limited the account: no first message is sent
    message: str


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, UnknownCredentialError):
        return HTTPException(404, "no such credential")
    if isinstance(error, InvalidCredentialError):
        return HTTPException(422, str(error))
    if isinstance(error, DuplicateSecretError):
        holder = error.holder_scope.removeprefix("client:")
        return HTTPException(409, f"this value is already used by client {holder!r}")
    if isinstance(error, SecretStoreUnavailableError):
        return HTTPException(503, str(error))
    return HTTPException(500, "credential storage failed")  # never echo store internals


def _client_exists(application: Application, client_id: str) -> bool:
    return bool(
        CLIENT_ID_PATTERN.match(client_id) and application.client_wiring(client_id)[0] is not None
    )


def create_admin_integrations_router(
    application: Application,
    http: HttpClientFactory = default_http_client,
    platform_mail_probe: PlatformMailProbeFactory = PlatformMailProbe,
    bot_probe: BotProbeFactory = telegram_provider.TelegramProbe,
    mail_login_probe: MailLoginProbeFactory = MailLoginProbe,
) -> APIRouter:
    credentials = application.credentials
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"], dependencies=[Depends(_no_store)])

    def read_scope(scope: str, kind: ScopeKind) -> list[CredentialResponse]:
        return [_response(s, kind) for s in credentials.describe(scope)]

    def save(
        scope: str, kind: ScopeKind, key: str, value: str, admin: AdminUser
    ) -> CredentialResponse:
        try:
            return _response(credentials.save(scope, key, value, actor=admin.username), kind)
        except (SecretStoreError, UnknownCredentialError, InvalidCredentialError) as error:
            raise _http_error(error) from None

    def remove(scope: str, kind: ScopeKind, key: str, admin: AdminUser) -> CredentialResponse:
        try:
            return _response(credentials.remove(scope, key, actor=admin.username), kind)
        except (SecretStoreError, UnknownCredentialError) as error:
            raise _http_error(error) from None

    def guard_model_lists(key: str, value: str | None) -> None:
        """A model list is changed through its own routes, which tell who is affected. Writing it
        here must not take a model away from the clients that use it, nor switch it off: refused
        with 409, naming them. Any other credential is not concerned."""
        if value is not None:
            try:
                value = normalize_value(spec_for(GLOBAL_SCOPE, key), value)
            except (UnknownCredentialError, InvalidCredentialError):
                return  # the save below reports it, as it always has
        effect = impact_of_credential_write(application, key, value)
        if effect.removed or effect.deactivated:
            affected = {**effect.removed, **effect.deactivated}
            raise HTTPException(
                409,
                {
                    "message": (
                        "clients use a model this change would remove or switch off: change the "
                        "model list through its own routes, after giving them another model"
                    ),
                    "client_ids": sorted({c for clients in affected.values() for c in clients}),
                    "models": {model: list(clients) for model, clients in affected.items()},
                },
            )

    def save_global(key: str, value: str, admin: AdminUser) -> CredentialResponse:
        guard_model_lists(key, value)
        return save(GLOBAL_SCOPE, ScopeKind.GLOBAL, key, value, admin)

    def remove_global(key: str, admin: AdminUser) -> CredentialResponse:
        guard_model_lists(key, None)
        return remove(GLOBAL_SCOPE, ScopeKind.GLOBAL, key, admin)

    def require_confirmation(confirm: bool) -> None:
        if not confirm:
            raise HTTPException(400, "removal must be confirmed: pass confirm=true")

    # ---- Revansa (global) ----------------------------------------------------------------

    @router.get("/settings/integrations", response_model=list[CredentialResponse])
    def list_global(_admin: AdminUser = Depends(get_current_admin)) -> list[CredentialResponse]:
        return read_scope(GLOBAL_SCOPE, ScopeKind.GLOBAL)

    @router.put(
        "/settings/integrations/{key}",
        response_model=CredentialResponse,
        openapi_extra=_BODY_SCHEMA,
    )
    async def put_global(
        key: str, request: Request, admin: AdminUser = Depends(require_owner)
    ) -> CredentialResponse:
        value = await _read_value(request)
        return await run_in_threadpool(save_global, key, value, admin)

    @router.delete("/settings/integrations/{key}", response_model=CredentialResponse)
    def delete_global(
        key: str, confirm: bool = False, admin: AdminUser = Depends(require_owner)
    ) -> CredentialResponse:
        require_confirmation(confirm)
        return remove_global(key, admin)

    def stored(scope: str, key: str) -> str | None:
        return credentials.reveal(scope, key)

    @router.post("/settings/integrations/openai/test", response_model=ConnectionTestResponse)
    def test_openai(_admin: AdminUser = Depends(require_owner)) -> ConnectionTestResponse:
        """Calls the real OpenAI API with the stored key, organization, project and base URL."""
        return _test_response(
            check_openai(
                api_key=stored(GLOBAL_SCOPE, "openai.api_key"),
                organization=stored(GLOBAL_SCOPE, "openai.organization"),
                project=stored(GLOBAL_SCOPE, "openai.project"),
                base_url=stored(GLOBAL_SCOPE, "openai.base_url"),
                models={m.name: m.id for m in stored_models(credentials) or [] if m.active},
                http=http,
            )
        )

    @router.post("/settings/integrations/email/test", response_model=ConnectionTestResponse)
    def test_email_servers(_admin: AdminUser = Depends(require_owner)) -> ConnectionTestResponse:
        """Opens an encrypted connection to each server of the platform mail profile and closes it.
        No login is attempted and no message is sent or read: it proves the servers can be reached
        from here, nothing more."""
        return _test_response(
            check_platform_servers(lambda key: stored(GLOBAL_SCOPE, key), platform_mail_probe)
        )

    # ---- e-mail: logging in (platform test login, and one client's assigned mailbox) ---------

    def platform_login(protocol: str) -> tuple[SmtpSettings | None, ImapSettings | None, str, str]:
        """The platform's test login for one protocol, as the settings the probe takes, with the
        subject and fingerprint its result is remembered under. Raises `_NotReady` with a fixed
        sentence when the profile or the login is incomplete."""
        profile, missing = read_profile(lambda key: stored(GLOBAL_SCOPE, key))
        if profile is None:
            raise _NotReady(f"the server settings are not complete yet ({', '.join(missing)})")
        username = stored(GLOBAL_SCOPE, f"email.platform_{protocol}_username")
        password = stored(GLOBAL_SCOPE, f"email.platform_{protocol}_password")
        if not username or not password:
            raise _NotReady(f"the {protocol.upper()} username or password is not set yet")
        if protocol == "smtp":
            smtp = SmtpSettings(
                host=profile.smtp_host,
                port=profile.smtp_port,
                security=profile.smtp_security,
                username=username,
                password=password,
                from_address=username,
            )
            print_ = fingerprint(
                profile.smtp_host, str(profile.smtp_port), profile.smtp_security, username, password
            )
            return smtp, None, f"platform:{protocol}", print_
        imap = ImapSettings(
            host=profile.imap_host,
            port=profile.imap_port,
            security=profile.imap_security,
            username=username,
            password=password,
        )
        print_ = fingerprint(
            profile.imap_host, str(profile.imap_port), profile.imap_security, username, password
        )
        return None, imap, f"platform:{protocol}", print_

    def platform_login_test(protocol: str) -> ConnectionTestResponse:
        label = f"Email {protocol.upper()}"
        try:
            smtp, imap, subject, print_ = platform_login(protocol)
        except _NotReady as problem:
            return ConnectionTestResponse(
                ok=False, message=f"{label}: {problem}.", details={protocol: "not_ready"}
            )
        probe = mail_login_probe(smtp, imap)
        return _test_response(
            run_login_check(protocol, probe, application.mail_auth, subject, print_, label)
        )

    @router.post("/settings/integrations/email/test-smtp", response_model=ConnectionTestResponse)
    def test_email_smtp_login(_admin: AdminUser = Depends(require_owner)) -> ConnectionTestResponse:
        """Connects to the SMTP server over an encrypted connection and logs in with the stored
        username and password. Nothing is sent."""
        return platform_login_test("smtp")

    @router.post("/settings/integrations/email/test-imap", response_model=ConnectionTestResponse)
    def test_email_imap_login(_admin: AdminUser = Depends(require_owner)) -> ConnectionTestResponse:
        """Connects to the IMAP server over an encrypted connection, logs in and opens the folder.
        No message is read or changed."""
        return platform_login_test("imap")

    def platform_state() -> tuple[str, dict[str, str], dict[str, str | None], list[str]]:
        """(overall state, state of each protocol, why, settings still missing)."""
        _, missing = read_profile(lambda key: stored(GLOBAL_SCOPE, key))
        states: dict[str, str] = {}
        problems: dict[str, str | None] = {}
        for protocol in ("smtp", "imap"):
            try:
                _s, _i, subject, print_ = platform_login(protocol)
            except _NotReady as reason:
                states[protocol] = NOT_CONFIGURED
                problems[protocol] = f"{reason}"
            else:
                states[protocol] = application.mail_auth.state(subject, print_)
                kind = application.mail_auth.failure(subject, print_)
                problems[protocol] = login_problem(kind) if kind else None
        if NOT_CONFIGURED in states.values():
            overall = "missing_configuration"
        elif all(state == AUTHENTICATED for state in states.values()):
            overall = "ready"
        else:
            overall = "server_not_authenticated"
        return overall, states, problems, missing

    def known_channels() -> list[str]:
        return [
            d.key
            for d in application.provider_registry.list(ProviderKind.CHANNEL)
            if d.kind is ProviderKind.CHANNEL and d.runnable
        ]

    def awaiting_mailbox() -> list[str]:
        """Clients that may use Email and have no mailbox yet. Revansa has no way to create one at
        the provider: an administrator assigns it (see docs/email-channel.md)."""
        policy = application.channel_policy
        if policy is None:
            return []
        waiting = []
        for profile in application.client_profiles.list():
            try:
                if "email" in policy.effective(profile.client_id) and not credential_present(
                    profile.client_id
                ):
                    waiting.append(profile.client_id)
            except SecretStoreError:
                continue
        return sorted(waiting)

    def credential_present(client_id: str) -> bool:
        return credentials.status(client_scope(client_id), EMAIL_PLATFORM_MAILBOX).configured

    @router.get("/settings/integrations/email/status", response_model=EmailServiceStatus)
    def email_platform_status(_admin: AdminUser = Depends(get_current_admin)) -> EmailServiceStatus:
        """Whether the platform's mail servers are set up and have been proven to accept a login.
        The proof is the last test of the current settings: a changed setting voids it."""
        overall, states, problems, missing = platform_state()
        policy = application.channel_policy
        return EmailServiceStatus(
            state=overall,
            missing=missing,
            smtp_problem=problems["smtp"],
            imap_problem=problems["imap"],
            new_clients_access=policy is not None and "email" in policy.new_client_channels(),
            clients_awaiting_mailbox=awaiting_mailbox(),
            **states,
        )

    @router.put("/settings/integrations/email/new-clients", response_model=EmailServiceStatus)
    def set_email_for_new_clients(
        body: NewClientsAccessRequest, admin: AdminUser = Depends(require_owner)
    ) -> EmailServiceStatus:
        """Turn on (or off) Email access for the clients created from now on. Turning it on needs
        the platform's servers to have been proven (`ready`); nothing changes for the clients that
        already exist. Turning it off stops giving access to new clients and takes nothing away."""
        policy = application.channel_policy
        if policy is None:
            raise HTTPException(503, "channel authorizations are not available")
        if body.enabled:
            overall, _states, _problems, _missing = platform_state()
            if overall != "ready":
                raise HTTPException(
                    409,
                    "the email servers are not ready: save the settings and test both logins first",
                )
        channels = set(policy.new_client_channels())
        channels.add("email") if body.enabled else channels.discard("email")
        try:
            policy.set_new_client_channels(channels, actor=admin.username, known=known_channels())
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return email_platform_status(admin)

    def mailbox_status(client_id: str) -> EmailMailboxStatus:
        mailbox = client_mailbox(application, client_id)
        if mailbox.access is None:
            return EmailMailboxStatus(state=mailbox.state, problem=mailbox.problem)
        smtp, imap = protocol_states(application, client_id, mailbox)
        ledger = application.mail_auth
        kinds = {
            p: ledger.failure(f"client:{client_id}:{p}", mailbox.print_) for p in ("smtp", "imap")
        }
        ready = smtp == imap == AUTHENTICATED
        return EmailMailboxStatus(
            state=READY if ready else NOT_AUTHENTICATED,
            smtp=smtp,
            imap=imap,
            problem=None if ready else "the mailbox has not been authenticated on both servers",
            smtp_problem=login_problem(kinds["smtp"]) if kinds["smtp"] else None,
            imap_problem=login_problem(kinds["imap"]) if kinds["imap"] else None,
        )

    @router.get("/clients/{client_id}/integrations/email/status", response_model=EmailMailboxStatus)
    def client_mailbox_status(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> EmailMailboxStatus:
        """Where one client's mailbox stands: servers set up, mailbox assigned, password stored,
        login proven. `ready` only when both logins were accepted with the current settings."""
        client_scope_for(admin, client_id)
        return mailbox_status(client_id)

    @router.post(
        "/clients/{client_id}/integrations/email/test", response_model=ConnectionTestResponse
    )
    def test_client_mailbox(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> ConnectionTestResponse:
        """Logs in to the sending server and to the mailbox assigned to this client, with its own
        address and password. Nothing is sent and nothing is read."""
        client_scope_for(admin, client_id)
        mailbox, results = verify_client_mailbox(application, client_id, mail_login_probe)
        if mailbox.access is None:
            return ConnectionTestResponse(ok=False, message=f"Email: {mailbox.problem}.")
        ok = all(r.ok for r in results)
        return ConnectionTestResponse(
            ok=ok,
            message=(
                "Email: both logins were accepted. No message was sent and none was read."
                if ok
                else "Email: " + " ".join(r.message for r in results if not r.ok)
            ),
            details={k: v for r in results for k, v in r.details.items()},
        )

    @router.get("/settings/integrations/gmail/status", response_model=GmailPlatformStatus)
    def gmail_platform_status(
        _admin: AdminUser = Depends(get_current_admin),
    ) -> GmailPlatformStatus:
        """Config-only (no network): the Google OAuth credentials and the public base URL
        (`SALES_AGENT_PUBLIC_BASE_URL`). Values are never returned."""
        gmail = application.gmail
        missing = gmail.missing() if gmail is not None else ["Gmail service"]
        return GmailPlatformStatus(
            configured=not missing,
            missing=missing,
            redirect_uri=redirect_uri_for(application.settings.public_base_url),
            sheets_redirect_uri=redirect_uri_for(
                application.settings.public_base_url, sheets_callback_path
            ),
            note=(
                "Stored is not verified: Google accepts the application only after a consent has "
                "succeeded, and for other people's accounts only once Google has verified it."
            ),
        )

    @router.get(
        "/settings/integrations/telegram-account/status",
        response_model=TelegramAccountPlatformStatus,
    )
    def telegram_account_platform_status(
        _admin: AdminUser = Depends(get_current_admin),
    ) -> TelegramAccountPlatformStatus:
        """Config-only (no network): the platform's Telegram application and the library on the
        server. Values are never returned."""
        service = getattr(application, "telegram_accounts", None)
        missing = service.missing() if service is not None else ["Telegram account service"]
        return TelegramAccountPlatformStatus(
            configured=not missing,
            missing=missing,
            note=(
                "Stored is not verified: Telegram accepts the application only once a client has "
                "signed in. Register it with the platform operator's own account at "
                "my.telegram.org (Telegram's API terms, 2.1)."
            ),
        )

    @router.get("/settings/integrations/hubspot/status", response_model=HubSpotPlatformStatus)
    def hubspot_platform_status(
        _admin: AdminUser = Depends(get_current_admin),
    ) -> HubSpotPlatformStatus:
        """Config-only (no network): the HubSpot app credentials and the public base URL. Values are
        never returned."""
        hubspot = application.hubspot_access
        missing = hubspot.missing() if hubspot is not None else ["HubSpot service"]
        return HubSpotPlatformStatus(
            configured=not missing,
            missing=missing,
            redirect_uri=redirect_uri_for(
                application.settings.public_base_url, hubspot_callback_path
            ),
            scopes=list(HUBSPOT_SCOPES),
            note=(
                "Stored is not verified: HubSpot accepts the application only after a consent has "
                "succeeded. The app must declare exactly these scopes and this redirect URL."
            ),
        )

    @router.post("/settings/openai/models/{model_id}/test", response_model=ModelTestResponse)
    def test_openai_model(
        model_id: str, confirm: bool = False, admin: AdminUser = Depends(require_owner)
    ) -> ModelTestResponse:
        """Asks one model of the Settings list, by its exact Model ID, for a few tokens.

        Unlike the connection test (`/settings/integrations/openai/test`, which only lists what the
        account sees and is free) this is a real request that the account may be billed for, so it
        needs `confirm=true`. The model may be switched off: testing it does not switch it on. How
        the test ended is kept with the model, and the catalog of `/settings/llm` shows it."""
        if all(m.id != model_id for m in stored_models(credentials) or []):
            raise HTTPException(404, "no such model")
        if not confirm:
            raise HTTPException(
                400,
                "this sends a real request to OpenAI (at most "
                f"{INFERENCE_MAX_TOKENS} tokens of output) and the account may be billed for it: "
                "pass confirm=true to go ahead",
            )
        result = check_openai_inference(
            api_key=stored(GLOBAL_SCOPE, "openai.api_key"),
            organization=stored(GLOBAL_SCOPE, "openai.organization"),
            project=stored(GLOBAL_SCOPE, "openai.project"),
            base_url=stored(GLOBAL_SCOPE, "openai.base_url"),
            model_id=model_id,
            http=http,
        )
        tested_at = None
        recorded = False
        if result.sent_a_request:
            tested_at = application.clock.now().astimezone(UTC).isoformat(timespec="seconds")
            recorded = record_test(
                credentials,
                model_id,
                ModelTest(result.outcome.value, tested_at),
                actor=admin.username,
                reserved=foreign_model_keys(application.provider_registry, OPENAI_MODELS),
            )
        return ModelTestResponse(
            ok=result.ok,
            outcome=result.outcome.value,
            message=result.message,
            model_id=model_id,
            details=result.details,
            request_sent=result.sent_a_request,
            max_tokens=INFERENCE_MAX_TOKENS,
            tested_at=tested_at,
            recorded=recorded,
        )

    # ---- are the clients running on them -----------------------------------------------

    def runtime_status(client_id: str) -> RuntimeStatusResponse:
        missing = application.registry.missing(client_id)
        applied = application.runtime_manager.status(client_id)
        return RuntimeStatusResponse(
            client_id=client_id,
            readiness="not_ready" if missing else "ready",
            missing_credentials=list(missing),
            apply_state=applied.state,
            apply_at=applied.at,
            apply_errors=list(applied.errors),
        )

    @router.get("/settings/runtime-status", response_model=list[RuntimeStatusResponse])
    def list_runtime_status(
        admin: AdminUser = Depends(get_current_admin),
    ) -> list[RuntimeStatusResponse]:
        ids = sorted(c.client_id for c in application.registry.configs())
        return [runtime_status(cid) for cid in ids if admin.can_reach(cid)]

    # ---- one client ----------------------------------------------------------------------

    @router.get("/clients/{client_id}/runtime-status", response_model=RuntimeStatusResponse)
    def get_runtime_status(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> RuntimeStatusResponse:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if application.registry.get_config(client_id) is None:
            raise HTTPException(404, "no such client")
        return runtime_status(client_id)

    def client_scope_for(admin: AdminUser, client_id: str) -> str:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        if not _client_exists(application, client_id):
            raise HTTPException(404, "no such client")
        return client_scope(client_id)

    @router.get("/clients/{client_id}/integrations", response_model=list[CredentialResponse])
    def list_client(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> list[CredentialResponse]:
        return read_scope(client_scope_for(admin, client_id), ScopeKind.CLIENT)

    @router.put(
        "/clients/{client_id}/integrations/{key}",
        response_model=CredentialResponse,
        openapi_extra=_BODY_SCHEMA,
    )
    async def put_client(
        client_id: str, key: str, request: Request, admin: AdminUser = Depends(require_owner)
    ) -> CredentialResponse:
        scope = client_scope_for(admin, client_id)
        value = await _read_value(request)
        if key in (EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX):
            from sales_app.api.admin_clients import client_wiring_and_document

            found = client_wiring_and_document(application, client_id)
            conflict = dedicated_credential_conflict(
                application, client_id, found[1] if found is not None else {}
            )
            if conflict is not None:
                raise HTTPException(409, conflict)
        return await run_in_threadpool(save, scope, ScopeKind.CLIENT, key, value, admin)

    @router.delete("/clients/{client_id}/integrations/{key}", response_model=CredentialResponse)
    def delete_client(
        client_id: str,
        key: str,
        confirm: bool = False,
        admin: AdminUser = Depends(require_owner),
    ) -> CredentialResponse:
        scope = client_scope_for(admin, client_id)
        require_confirmation(confirm)
        return remove(scope, ScopeKind.CLIENT, key, admin)

    @router.post(
        "/clients/{client_id}/integrations/whatsapp/test", response_model=ConnectionTestResponse
    )
    def test_client_whatsapp(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> ConnectionTestResponse:
        """Asks Meta for this client's phone number with its stored access token."""
        scope = client_scope_for(admin, client_id)
        return _test_response(
            check_whatsapp_client(
                access_token=stored(scope, "whatsapp.access_token"),
                phone_number_id=stored(scope, "whatsapp.phone_number_id"),
                api_version=stored(GLOBAL_SCOPE, "whatsapp.api_version") or DEFAULT_API_VERSION,
                http=http,
            )
        )

    @router.get(
        "/clients/{client_id}/integrations/whatsapp/status", response_model=WhatsAppStatusResponse
    )
    def client_whatsapp_status(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> WhatsAppStatusResponse:
        """Read-only: which WhatsApp pieces are stored. Meta is not called. The client supplies only
        its number; every Meta credential is the Admin's."""
        scope = client_scope_for(admin, client_id)
        profile = application.client_profiles.get(client_id)
        number = bool(profile and profile.whatsapp_number)
        reconnect = bool(profile and profile.whatsapp_reconnect_required)
        wanted = [(GLOBAL_SCOPE, "whatsapp.app_secret"), (GLOBAL_SCOPE, "whatsapp.verify_token")]
        wanted += [(scope, "whatsapp.access_token"), (scope, "whatsapp.phone_number_id")]
        missing = [key for sc, key in wanted if not stored(sc, key)]
        if missing:
            status, message = "waiting_platform", "Meta credentials are still to be stored."
        elif not number or reconnect:
            status = "action_required"
            message = (
                "The client's WhatsApp number is missing or was changed and is not reconnected."
            )
        else:
            status = "ready"
            message = "Credentials are stored; run the test to check them with Meta."
        return WhatsAppStatusResponse(
            status=status,
            whatsapp_number_given=number,
            reconnect_required=reconnect,
            missing_platform=missing,
            message=message,
        )

    @router.post(
        "/clients/{client_id}/integrations/telegram/connect", response_model=ConnectionTestResponse
    )
    async def connect_client_telegram(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> ConnectionTestResponse:
        """Tells Telegram to deliver this client's bot to the platform. The client never does this:
        its bot token is a credential the Admin stored above."""
        client_scope_for(admin, client_id)
        try:
            result = await run_in_threadpool(
                connect_bot, application, client_id, bot_probe, actor=f"admin:{admin.username}"
            )
        except TelegramConnectionError as error:
            raise HTTPException(error.status, error.message) from None
        return _test_response(result)

    @router.post(
        "/clients/{client_id}/integrations/telegram/test", response_model=ConnectionTestResponse
    )
    async def test_client_telegram(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> ConnectionTestResponse:
        """A real, read-only question to Telegram (who is the bot, where does it deliver). Never a
        simulation: `details.check` says so. Sends no message and changes nothing."""
        client_scope_for(admin, client_id)
        try:
            result = await run_in_threadpool(
                check_bot_connection, application, client_id, bot_probe
            )
        except TelegramConnectionError as error:
            raise HTTPException(error.status, error.message) from None
        return _test_response(result)

    @router.post(
        "/clients/{client_id}/integrations/telegram/disconnect",
        response_model=ConnectionTestResponse,
    )
    async def disconnect_client_telegram(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> ConnectionTestResponse:
        """Tells Telegram to stop delivering this client's bot, and forgets the webhook secret."""
        client_scope_for(admin, client_id)
        try:
            result = await run_in_threadpool(
                disconnect_bot, application, client_id, bot_probe, actor=f"admin:{admin.username}"
            )
        except TelegramConnectionError as error:
            raise HTTPException(error.status, error.message) from None
        return _test_response(result)

    # ---- Telegram from the client's own account -------------------------------------------

    def account_service():
        service = getattr(application, "telegram_accounts", None)
        if service is None:
            raise HTTPException(409, "Telegram accounts are not available on this platform")
        return service

    def account_answer(client_id: str) -> TelegramAccountAdminResponse:
        service = account_service()
        status = service.status(client_id)
        missing = service.missing()
        if missing:
            message = "The platform's Telegram application is not set up yet: " + ", ".join(missing)
        elif status.state == "not_connected":
            message = "The client has not connected its Telegram account."
        elif status.state == "reauth_required":
            message = "Telegram ended the session: the client has to sign in again."
        elif status.limited:
            message = (
                "Telegram limited this account for messages to people who did not expect them. "
                "No first message is sent. Lift the limit here once @SpamBot says it is over."
            )
        elif status.state == "connected":
            message = "The account is connected."
        else:
            message = "The account state cannot be read just now."
        return TelegramAccountAdminResponse(
            platform_configured=not missing,
            platform_missing=missing,
            state=status.state,
            limited=status.limited,
            message=message,
        )

    @router.get(
        "/clients/{client_id}/integrations/telegram/account",
        response_model=TelegramAccountAdminResponse,
    )
    def client_telegram_account(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> TelegramAccountAdminResponse:
        """Read-only. Telegram is not called."""
        client_scope_for(admin, client_id)
        return account_answer(client_id)

    @router.post(
        "/clients/{client_id}/integrations/telegram/account/disconnect",
        response_model=TelegramAccountAdminResponse,
    )
    async def disconnect_client_telegram_account(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> TelegramAccountAdminResponse:
        """Ends the client's session at Telegram (best effort) and forgets it here: the agent sends
        nothing until the client signs in again."""
        client_scope_for(admin, client_id)
        service = account_service()
        try:
            await run_in_threadpool(service.disconnect, client_id)
        except AccountLoginError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        if application.registry.get_config(client_id) is not None:
            with contextlib.suppress(Exception):  # the runtime status says how it ended
                application.runtime_manager.refresh_client(client_id)
        return account_answer(client_id)

    @router.post(
        "/clients/{client_id}/integrations/telegram/account/clear-limit",
        response_model=TelegramAccountAdminResponse,
    )
    def clear_client_telegram_limit(
        client_id: str, admin: AdminUser = Depends(require_owner)
    ) -> TelegramAccountAdminResponse:
        """Lets first messages go again, once Telegram itself has lifted the limit. A decision of the
        Admin: Revansa never tries to find out by sending."""
        client_scope_for(admin, client_id)
        try:
            account_service().clear_limited(client_id)
        except AccountLoginError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return account_answer(client_id)

    return router
