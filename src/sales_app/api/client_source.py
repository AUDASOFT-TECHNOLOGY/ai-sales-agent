"""Client API: where a client's prospects come from, the credential that source needs, and a real
check of the connection.

Like `client.py` and `client_agent.py`, no route takes a client id: who is asking comes only from the
session cookie, so no id supplied by the browser can reach another client's source.

- **The source** (`GET/PUT /client/v1/source`) is the one provider the prospects are read from:
  `google_sheets` (a spreadsheet the client shares with the platform's address) or `hubspot` (the
  client's own HubSpot account). A write carries the version it was based on and is refused (409) if
  the settings moved; it is validated by the same rules as every publication (422) and **nothing is
  written** if it is not valid. A client changes only the source and its spreadsheet and tab: never
  the model, the mode, the quota, the store's tabs or anything of WhatsApp/Meta.
- **The credential** (`PUT/DELETE /client/v1/source/credentials`) is the secret the chosen source
  needs from the client (today the HubSpot Private App token). It is written, never read: no response
  and no message carries it, whatever happens, and a body that is not exactly what is expected is
  refused without being echoed.
- **The test** (`POST /client/v1/source/test`) makes one real, read-only call to the chosen source.
  A success proves the access works at that moment and nothing more; a failure is a fixed sentence.

HubSpot can be chosen, given its token and tested, but the agent cannot start on it until the Admin
has settled what its statuses mean (`readiness`), and switching the source pauses an agent that
was running: the client starts it again deliberately.
"""

import contextlib
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from threading import Lock
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from sales_app.api.admin_clients import (
    client_wiring_and_document,
    publish_client_settings,
)
from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_agent import (
    lane_of_source,
    lane_source_entries,
    lane_sources_fingerprint,
    runs_lanes,
    sheets_of,
    source_fingerprint,
    source_provider,
    switch_available,
)
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.config.secrets import HUBSPOT_ACCESS_TOKEN, SHEETS_CREDENTIALS_FILE
from sales_app.config_store import ConfigStoreError
from sales_app.connection_tests import (
    ConnectionResult,
    HttpClientFactory,
    SheetsProbe,
    check_google_sheets,
    check_hubspot,
    default_http_client,
)
from sales_app.secret_store import (
    DuplicateSecretError,
    InvalidCredentialError,
    ScopeKind,
    SecretStoreError,
    UnknownCredentialError,
    client_scope,
)
from sales_app.source_checks import SourceCheckStoreCorruptError, SourceCheckStoreUnavailableError
from sales_app.wiring.providers import ProviderDescriptor, ProviderKind, google_sheets, hubspot
from sales_app.wiring.providers.types import CredentialDeclaration, Requirement

SHEETS = google_sheets.KEY
SHEETS_TIMEOUT_SECONDS = 15.0
# A real call to an outside service per request: a client may test again only after this long.
TEST_MIN_INTERVAL_SECONDS = 3.0

SheetsProbeFactory = Callable[[str], SheetsProbe]  # (credentials file) -> something to read with

# What this route may change in a stored settings document: the source's provider, and where the
# prospects are inside the spreadsheet. The store's tabs and column mapping stay the Admin's.
SOURCE_SETTINGS_SECTIONS: dict[str, tuple[str, ...]] = {
    "source": ("provider",),
    "sheets": ("spreadsheet_id", "prospects_tab"),
}

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
TabName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


# ---- models -----------------------------------------------------------------------------------


class SheetsSourceChanges(BaseModel):
    """What a client says about its spreadsheet: the link or id it pastes, and the tab. A key left
    out keeps its value."""

    model_config = ConfigDict(extra="forbid")

    spreadsheet: NonEmpty | None = None  # a URL or a bare id: the id is what is stored
    prospects_tab: TabName | None = None


class UpdateSourceRequest(BaseModel):
    """`extra="forbid"`: any other key (the model, the mode, a Meta setting...) answers 422."""

    model_config = ConfigDict(extra="forbid")

    # The version these changes were made on (null: none was published yet). Required, never
    # defaulted: a write that does not say what it is based on could silently undo someone else's.
    expected_version: int | None
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
    google_sheets: SheetsSourceChanges | None = None


class SourceOption(BaseModel):
    key: str
    label: str
    runnable: bool  # False: it can be configured and tested, but an agent cannot run on it yet


class SheetsSourceResponse(BaseModel):
    spreadsheet_id: str
    prospects_tab: str
    share_with: str | None = None  # the platform's address the spreadsheet must be shared with


class CredentialStatusResponse(BaseModel):
    """Whether the source's secret is stored. Never its value, never part of it."""

    label: str
    configured: bool
    updated_at: datetime | None = None


class LastTestResponse(BaseModel):
    """The client's last real test. It counts for the start of the agent only while `current`: once
    the source, the spreadsheet, the tab or the token has changed, it is history."""

    ok: bool
    checked_at: datetime
    current: bool


class SourceResponse(BaseModel):
    version: int | None
    source: str  # the chosen source's key (the API never says "provider": see the contract test)
    runnable: bool
    options: list[SourceOption]
    google_sheets: SheetsSourceResponse | None = None  # when the source is Google Sheets
    credential: CredentialStatusResponse | None = None  # when the source needs one from the client
    last_test: LastTestResponse | None = None  # None: never tested
    updated_at: datetime | None = None
    updated_by: Literal["client", "administrator"] | None = None


class LaneSourceTestResponse(BaseModel):
    lane: str
    ok: bool
    message: str  # a fixed sentence, as for a single source


class SourceTestResponse(BaseModel):
    source: str  # the source tested, or `lanes` when the client runs several
    ok: bool  # for lanes: every source passed
    message: str  # a fixed sentence: never a credential, never what the service said
    details: dict[str, str] = {}
    checked_at: datetime
    lanes: list[LaneSourceTestResponse] | None = None  # one result per lane of a lane client


def sheets_credentials_file(application: Application, client_id: str) -> str | None:
    """The platform's service-account file for this client's spreadsheets, if it has one."""
    config = application.registry.get_config(client_id)
    return config.secrets.get_optional(SHEETS_CREDENTIALS_FILE) if config is not None else None


# ---- helpers ----------------------------------------------------------------------------------


def _writeback_headers(settings: Mapping[str, Any]) -> tuple[str, ...]:
    """The columns the operator chose for write-back in a source's settings, if any. They are the
    operator's: the client's routes cannot set them."""
    block = settings.get("writeback")
    columns = block.get("columns") if isinstance(block, Mapping) else None
    if not isinstance(columns, Mapping):
        return ()
    return tuple(str(header) for header in columns.values())


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _client_credential(descriptor: ProviderDescriptor | None) -> CredentialDeclaration | None:
    """The secret a source needs from the client itself: a stored, sensitive credential in the
    client's scope that its start requires."""
    if descriptor is None:
        return None
    return next(
        (
            c
            for c in descriptor.credentials
            if c.scope is not None
            and c.scope is ScopeKind.CLIENT
            and c.sensitive
            and c.required_when is Requirement.ALWAYS
        ),
        None,
    )


def default_sheets_probe(credentials_file: str) -> SheetsProbe:
    return google_sheets.open_probe(credentials_file, timeout_seconds=SHEETS_TIMEOUT_SECONDS)


async def _read_secret(request: Request) -> str:
    """The body must be exactly `{"access_token": "<text>"}`. Anything else is refused with a fixed
    sentence that repeats nothing of it (it may be a token under the wrong name)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    if (
        not isinstance(body, dict)
        or set(body) != {"access_token"}
        or not isinstance(body["access_token"], str)
    ):
        raise HTTPException(422, 'body must be {"access_token": "<text>"}')
    return body["access_token"]


async def _read_update(request: Request) -> UpdateSourceRequest:
    """The body, validated by the model, with errors that say where and why but never *what*: a
    client who pastes a token into the wrong field must not get it back in the answer (the
    framework's own 422 repeats the offending input)."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    try:
        return UpdateSourceRequest.model_validate(data)
    except ValidationError as error:
        raise HTTPException(
            422,
            [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in error.errors()],
        ) from None


_SOURCE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["expected_version", "source"],
                    "properties": {
                        "expected_version": {"type": ["integer", "null"]},
                        "source": {"type": "string"},
                        "google_sheets": {
                            "type": "object",
                            "properties": {
                                "spreadsheet": {"type": "string"},
                                "prospects_tab": {"type": "string"},
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
                    "required": ["access_token"],
                    "properties": {"access_token": {"type": "string", "writeOnly": True}},
                    "additionalProperties": False,
                }
            }
        },
    }
}


def create_client_source_router(
    application: Application,
    http: HttpClientFactory = default_http_client,
    sheets_probe: SheetsProbeFactory = default_sheets_probe,
) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"], dependencies=[Depends(_no_store)])
    registry = application.provider_registry
    last_test: dict[str, float] = {}
    last_test_lock = Lock()

    def source_options() -> list[SourceOption]:
        return [
            SourceOption(key=d.key, label=d.label, runnable=d.runnable)
            for d in registry.list(ProviderKind.SOURCE)
            if d.kind is ProviderKind.SOURCE
        ]

    def platform_sheets_file(client_id: str) -> str | None:
        return sheets_credentials_file(application, client_id)

    def current_state(
        client_id: str,
    ) -> tuple[Any, Mapping[str, Any], Mapping[str, Any], int | None]:
        found = client_wiring_and_document(application, client_id)
        if found is None:
            raise HTTPException(404, "no such client")
        current = application.config_store.current(client_id)
        return current, found[0], found[1], current.version if current is not None else None

    def effective_provider(raw: Mapping[str, Any], document: Mapping[str, Any]) -> str:
        """The source a client's source routes speak of. A client that runs lanes has several: the
        one that needs a secret of the client's own (its token is what these routes write), else
        the first."""
        if not runs_lanes(document):
            return source_provider(raw, document)
        providers = [str(s.get("provider", "")) for s in lane_source_entries(document)]
        with_secret = next((p for p in providers if _client_credential(registry.get(p))), None)
        return with_secret or (providers[0] if providers else source_provider(raw, document))

    def fingerprint_of(client_id: str, provider: str, document: Mapping[str, Any]) -> str:
        if runs_lanes(document):
            return lane_sources_fingerprint(application, client_id, document)
        return source_fingerprint(application, client_id, provider, document)

    def credential_status(client_id: str, declaration: CredentialDeclaration):
        try:
            status = application.credentials.status(client_scope(client_id), declaration.key)
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return CredentialStatusResponse(
            label=declaration.label, configured=status.configured, updated_at=status.updated_at
        )

    def source_response(client_id: str) -> SourceResponse:
        current, raw, document, version = current_state(client_id)
        lanes = runs_lanes(document)
        provider = effective_provider(raw, document)
        descriptor = registry.get(provider)
        spreadsheet_id, prospects_tab = sheets_of(document)
        declaration = _client_credential(descriptor)
        runnable = descriptor is not None and descriptor.runnable
        if lanes:  # runnable only if every source of every lane is
            runnable = bool(lane_source_entries(document)) and all(
                (d := registry.get(str(s.get("provider", "")))) is not None and d.runnable
                for s in lane_source_entries(document)
            )
        try:
            check = application.source_checks.get(client_id)
        except (SourceCheckStoreCorruptError, SourceCheckStoreUnavailableError):
            raise HTTPException(
                503, "the connection test result is temporarily unavailable"
            ) from None
        return SourceResponse(
            version=version,
            source=provider,
            runnable=runnable,
            options=source_options(),
            google_sheets=(
                SheetsSourceResponse(
                    spreadsheet_id=spreadsheet_id,
                    prospects_tab=prospects_tab,
                    share_with=google_sheets.service_account_email(platform_sheets_file(client_id)),
                )
                if provider == SHEETS and not lanes  # a lane client's spreadsheets: `/lanes`
                else None
            ),
            credential=credential_status(client_id, declaration) if declaration else None,
            last_test=(
                LastTestResponse(
                    ok=check.ok,
                    checked_at=check.checked_at,
                    current=check.fingerprint == fingerprint_of(client_id, provider, document),
                )
                if check is not None
                else None
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

    # ---- the source -----------------------------------------------------------------------

    @router.get("/source", response_model=SourceResponse)
    def get_source(client: ClientProfile = Depends(get_current_client)) -> SourceResponse:
        try:
            return source_response(client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None

    @router.put("/source", response_model=SourceResponse, openapi_extra=_SOURCE_BODY)
    async def put_source(
        request: Request, client: ClientProfile = Depends(get_current_client)
    ) -> SourceResponse:
        body = await _read_update(request)
        return await run_in_threadpool(apply_source, body, client)

    def apply_source(body: UpdateSourceRequest, client: ClientProfile) -> SourceResponse:
        client_id = client.client_id
        descriptor = registry.get(body.source)
        if descriptor is None or descriptor.kind is not ProviderKind.SOURCE:
            raise HTTPException(422, "unknown source")
        if body.google_sheets is not None and body.source != SHEETS:
            raise HTTPException(422, "google_sheets settings belong to the Google Sheets source")

        sheet_changes: dict[str, str] = {}
        if body.google_sheets is not None:
            if body.google_sheets.spreadsheet is not None:
                try:
                    sheet_changes["spreadsheet_id"] = google_sheets.spreadsheet_id_of(
                        body.google_sheets.spreadsheet
                    )
                except ValueError as error:
                    raise HTTPException(422, f"google_sheets.spreadsheet: {error}") from None
            if body.google_sheets.prospects_tab is not None:
                sheet_changes["prospects_tab"] = body.google_sheets.prospects_tab
        if not sheet_changes and body.source == SHEETS and body.google_sheets is not None:
            raise HTTPException(422, "no setting to change")

        try:
            _, raw, document, version = current_state(client_id)
            if runs_lanes(document):
                raise HTTPException(
                    409, "your account runs lanes: change its sources in your lane settings"
                )
            if version != body.expected_version:
                raise HTTPException(
                    409, "the settings changed since they were loaded; reload and retry"
                )
            current_provider = source_provider(raw, document)
            current_id, current_tab = sheets_of(document)
            unchanged = body.source == current_provider and all(
                {"spreadsheet_id": current_id, "prospects_tab": current_tab}[key] == value
                for key, value in sheet_changes.items()
            )
            if unchanged:
                return source_response(client_id)

            if body.source != current_provider:
                _pause_agent(client_id)  # a source change is restarted deliberately, never silently
            changes: dict[str, Any] = {"source": {"provider": body.source}}
            if sheet_changes:
                changes["sheets"] = sheet_changes
            publish_client_settings(
                application,
                client_id,
                changes,
                expected_version=body.expected_version,
                author=f"client:{client_id}",
                note="client source",
                top_keys=(),
                sections=SOURCE_SETTINGS_SECTIONS,
            )
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        if application.registry.get_config(client_id) is not None:
            # The running agent takes the new settings without waiting for this request. A source it
            # cannot run yet is refused by the runtime and the old settings stay in force.
            with contextlib.suppress(Exception):  # the Admin's runtime status says how it ended
                application.runtime_manager.refresh_client(client_id)
        return source_response(client_id)

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

    # ---- the credential -------------------------------------------------------------------

    def declared_credential(client_id: str) -> CredentialDeclaration:
        _, raw, document, _ = current_state(client_id)
        declaration = _client_credential(registry.get(effective_provider(raw, document)))
        if declaration is None:
            raise HTTPException(409, "the source you chose needs no access token from you")
        return declaration

    @router.put(
        "/source/credentials", response_model=CredentialStatusResponse, openapi_extra=_SECRET_BODY
    )
    async def put_credentials(
        request: Request, client: ClientProfile = Depends(get_current_client)
    ) -> CredentialStatusResponse:
        value = await _read_secret(request)
        client_id = client.client_id
        try:
            declaration = declared_credential(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        try:
            status = application.credentials.save(
                client_scope(client_id),
                declaration.key,
                value,
                actor=f"client:{client_id}",
                apply=True,  # the agent of a client on this source reads it: rebuild it
            )
        except InvalidCredentialError as error:
            raise HTTPException(422, str(error)) from None  # words the rule, never the value
        except DuplicateSecretError:
            # Never say who holds it: only that it cannot be used.
            raise HTTPException(409, "this access token cannot be used for your account") from None
        except UnknownCredentialError:
            raise HTTPException(
                409, "the source you chose needs no access token from you"
            ) from None
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return CredentialStatusResponse(
            label=declaration.label, configured=status.configured, updated_at=status.updated_at
        )

    @router.delete("/source/credentials", response_model=CredentialStatusResponse)
    def delete_credentials(
        client: ClientProfile = Depends(get_current_client),
    ) -> CredentialStatusResponse:
        client_id = client.client_id
        try:
            declaration = declared_credential(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        try:
            status = application.credentials.remove(
                client_scope(client_id), declaration.key, actor=f"client:{client_id}"
            )
        except SecretStoreError:
            raise HTTPException(503, "credential storage is temporarily unavailable") from None
        return CredentialStatusResponse(
            label=declaration.label, configured=status.configured, updated_at=status.updated_at
        )

    # ---- the test -------------------------------------------------------------------------

    def run_check(client_id: str, provider: str, document: Mapping[str, Any]) -> ConnectionResult:
        if provider == SHEETS:
            spreadsheet_id, prospects_tab = sheets_of(document)
            path = platform_sheets_file(client_id)
            probe = None
            if path:
                try:
                    probe = sheets_probe(path)
                except Exception:  # noqa: BLE001 - an unusable file is the platform's problem
                    probe = None
            return check_google_sheets(
                api=probe, spreadsheet_id=spreadsheet_id, prospects_tab=prospects_tab
            )
        if provider == hubspot.KEY:
            try:
                token = application.credentials.reveal(
                    client_scope(client_id), HUBSPOT_ACCESS_TOKEN
                )
            except SecretStoreError:
                raise HTTPException(503, "credential storage is temporarily unavailable") from None
            return check_hubspot(access_token=token, http=http)
        return ConnectionResult(False, "There is no connection check for this source yet.")

    def run_lane_checks(client_id: str, document: Mapping[str, Any]) -> SourceTestResponse:
        """One real, read-only call per source of the client's lanes, each with its own settings.
        The token a source needs is the client's one, used only for the sources that need it."""
        results: list[LaneSourceTestResponse] = []
        for source in lane_source_entries(document):
            lane = lane_of_source(document, source.get("id"))
            provider = str(source.get("provider", ""))
            settings = source.get("settings")
            settings = settings if isinstance(settings, Mapping) else {}
            nothing = ConnectionResult(False, "There is no connection check for this source yet.")
            if provider == SHEETS:
                path = platform_sheets_file(client_id)
                probe = None
                if path:
                    try:
                        probe = sheets_probe(path)
                    except Exception:  # noqa: BLE001 - an unusable file is the platform's problem
                        probe = None
                result = check_google_sheets(
                    api=probe,
                    spreadsheet_id=str(settings.get("spreadsheet_id", "")) or None,
                    prospects_tab=str(settings.get("prospects_tab", "Prospects")),
                    writeback_headers=_writeback_headers(settings),
                )
            elif provider == hubspot.KEY:
                try:
                    token = application.credentials.reveal(
                        client_scope(client_id), HUBSPOT_ACCESS_TOKEN
                    )
                except SecretStoreError:
                    raise HTTPException(
                        503, "credential storage is temporarily unavailable"
                    ) from None
                result = check_hubspot(access_token=token, http=http)
            else:
                result = nothing
            results.append(LaneSourceTestResponse(lane=lane, ok=result.ok, message=result.message))
        failed = [r.lane for r in results if not r.ok]
        if not results:
            message = "There is no source to test."
        elif failed:
            message = "These lanes cannot reach their source: " + ", ".join(failed)
        else:
            message = "Every source of your lanes answered."
        return SourceTestResponse(
            source="lanes",
            ok=bool(results) and not failed,
            message=message,
            details={r.lane: r.message for r in results},
            checked_at=application.clock.now(),
            lanes=results,
        )

    @router.post("/source/test", response_model=SourceTestResponse)
    def test_source(client: ClientProfile = Depends(get_current_client)) -> SourceTestResponse:
        client_id = client.client_id
        try:
            _, raw, document, _ = current_state(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        with last_test_lock:
            now = time.monotonic()
            if (
                now - last_test.get(client_id, -TEST_MIN_INTERVAL_SECONDS)
                < TEST_MIN_INTERVAL_SECONDS
            ):
                raise HTTPException(429, "a test was just made: wait a few seconds")
            last_test[client_id] = now
        if runs_lanes(document):
            wanted = lane_sources_fingerprint(application, client_id, document)
            response = run_lane_checks(client_id, document)
        else:
            provider = source_provider(raw, document)
            wanted = source_fingerprint(application, client_id, provider, document)
            result = run_check(client_id, provider, document)
            response = SourceTestResponse(
                source=provider,
                ok=result.ok,
                message=result.message,
                details=dict(result.details),
                checked_at=application.clock.now(),
            )
        try:  # what was tested is kept with the result, so a later change cancels it
            application.source_checks.set(
                client_id, fingerprint=wanted, ok=response.ok, at=response.checked_at
            )
        except (SourceCheckStoreCorruptError, SourceCheckStoreUnavailableError):
            raise HTTPException(503, "the connection test result could not be kept") from None
        return response

    return router
