"""Client API: a client's lanes, each with its own source, its own channel and its own products.

Like the other Client routes, none takes a client id: who is asking comes only from the session
cookie, so nothing the browser sends can reach another client's lanes. A lane id in a path is the id
of one of the caller's own lanes.

- **Reading** (`GET /client/v1/lanes`, `GET /client/v1/lanes/{lane_id}`) says what each lane is made
  of, what the client may choose from, and the products of its catalog. A client that runs no lanes
  has one implicit `default` lane (its legacy source and channel, the whole catalog), shown read-only.
- **Writing** (`POST`, `PUT /{lane_id}`, `DELETE /{lane_id}`) is for a client the platform already
  runs on lanes. Turning a single-source client into lanes would change the ids of its prospects, so
  that is the Admin's decision and these routes answer 409 until then.
- Every write carries the version it was based on (409 if the settings moved), is validated by the
  rules of every publication (422 with every problem: unknown product, a lane without product, an
  unknown source, a channel that does not exist...) and **writes nothing** if it is not valid. A
  channel the Admin does not allow is refused (403), as on `/client/v1/channel`.
- A write replaces only the lane it names (and that lane's own source): the other lanes, the shared
  daily quota, the mailbox or bot settings of a channel that is still used, and the model are left
  exactly as they were. Lanes keep their order, which decides who keeps a prospect that two sources
  both hold (the first lane wins); a new lane goes last, so it never takes one from an older lane.
- A change of source or channel, a new lane and a removed lane pause an agent that was running: the
  client starts it again deliberately. A change of products does not.

The lane's source is made with its lane (`source`), from a provider that can run several instances;
the client gives only what is its own (a spreadsheet and its tab). Everything else of a source
(a CRM's statuses, credentials) stays with the Admin and the routes that already own it.
"""

import contextlib
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from sales_app.api.admin_clients import client_wiring_and_document, publish_client_settings
from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_agent import runs_lanes, source_provider, switch_available
from sales_app.api.client_source import sheets_credentials_file
from sales_app.bootstrap import Application
from sales_app.channel_status import allowed_channels, channel_problem, channel_provider_of
from sales_app.client_profiles import ClientProfile
from sales_app.config import validate_settings_document
from sales_app.config.lanes import DEFAULT_LANE
from sales_app.config_store import ConfigStoreError
from sales_app.config_store.models import thaw_document
from sales_app.wiring.providers import ProviderDescriptor, ProviderKind, google_sheets
from sales_core.contracts import ALL_PRODUCTS

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
TabName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


# ---- models -----------------------------------------------------------------------------------


class SheetsChanges(BaseModel):
    """What a client says about a spreadsheet: the link or id it pastes, and the tab. A key left out
    keeps its value."""

    model_config = ConfigDict(extra="forbid")

    spreadsheet: NonEmpty | None = None  # a URL or a bare id: the id is what is stored
    prospects_tab: TabName | None = None


class NewSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Short  # also the prefix of its prospects' ids: it never changes afterwards
    provider: Short
    google_sheets: SheetsChanges | None = None


class CreateLaneRequest(BaseModel):
    """`extra="forbid"`: anything else (the quota, the model, a credential...) answers 422."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None  # required, never defaulted: see `/client/v1/source`
    id: Short
    channel: Short
    products: list[Short] = Field(min_length=1)  # a lane with no product sells nothing; ["ALL"]
    source: NewSource


class UpdateLaneRequest(BaseModel):
    """Only what is sent changes. The lane's id and its source's id never do."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None
    channel: Short | None = None
    products: list[Short] | None = Field(default=None, min_length=1)
    google_sheets: SheetsChanges | None = None  # the settings of this lane's own spreadsheet


class SheetsResponse(BaseModel):
    spreadsheet_id: str
    prospects_tab: str


class LaneSourceResponse(BaseModel):
    id: str
    source: str  # the provider's key (the API never says "provider": see the contract test)
    label: str
    google_sheets: SheetsResponse | None = None


class LaneChannelResponse(BaseModel):
    channel: str
    label: str
    authorized: bool  # False: the Admin does not allow it (any more)
    problem: str | None = None  # what to do next, when it cannot run yet


class LaneResponse(BaseModel):
    id: str
    position: int  # 1 is first: the first lane keeps a prospect that two sources both hold
    source: LaneSourceResponse
    channel: LaneChannelResponse
    products: list[str]
    offers_all_products: bool


class OptionResponse(BaseModel):
    key: str
    label: str


class CatalogProductResponse(BaseModel):
    id: str
    name: str | None = None


class LanesResponse(BaseModel):
    version: int | None
    editable: bool  # False: the client runs no lanes of its own (a single implicit lane)
    lanes: list[LaneResponse]
    source_options: list[OptionResponse]
    channel_options: list[OptionResponse]  # only the channels this client may choose
    catalog: list[CatalogProductResponse]
    # The platform's address a spreadsheet must be shared with (an identifier, not a secret); None
    # when the platform has no readable Google file for this client.
    share_with: str | None = None


# ---- helpers ----------------------------------------------------------------------------------


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _read[M: BaseModel](request: Request, model: type[M]) -> M:
    """The body, validated by the model, with errors that say where and why but never *what* (the
    framework's own 422 repeats the offending input)."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply a bad request
        raise HTTPException(400, "body must be JSON") from None
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise HTTPException(
            422,
            [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in error.errors()],
        ) from None


def _mappings(value: object) -> list[dict[str, Any]]:
    return (
        [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, list | tuple)
        else []
    )


def _catalog(document: Mapping[str, Any]) -> list[CatalogProductResponse]:
    brain = document.get("brain")
    settings = brain.get("settings") if isinstance(brain, Mapping) else None
    catalog = settings.get("catalog") if isinstance(settings, Mapping) else None
    products = catalog.get("products") if isinstance(catalog, Mapping) else None
    shown = []
    for product in _mappings(products):
        if isinstance(product.get("id"), str):
            name = product.get("name")
            shown.append(
                CatalogProductResponse(
                    id=product["id"], name=name if isinstance(name, str) else None
                )
            )
    return shown


def _pause_agent(application: Application, client_id: str) -> None:
    """A change of where prospects come from or are written to is restarted deliberately."""
    with switch_available():
        state = application.agent_state.get(client_id)
        if state is not None and state.running:
            application.agent_state.set(
                client_id, running=False, by=f"client:{client_id}", at=application.clock.now()
            )


def create_client_lanes_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"], dependencies=[Depends(_no_store)])
    registry = application.provider_registry

    def current_state(client_id: str) -> tuple[Mapping[str, Any], dict[str, Any], int | None]:
        found = client_wiring_and_document(application, client_id)
        if found is None:
            raise HTTPException(404, "no such client")
        current = application.config_store.current(client_id)
        return found[0], thaw_document(found[1]), current.version if current is not None else None

    def label_of(descriptor: ProviderDescriptor | None, key: str) -> str:
        return descriptor.label if descriptor is not None else key

    def source_options() -> list[OptionResponse]:
        return [
            OptionResponse(key=d.key, label=d.label)
            for d in registry.list(ProviderKind.SOURCE)
            if d.plays(ProviderKind.SOURCE) and d.runnable and d.build_instance is not None
        ]

    def channel_options(client_id: str) -> list[OptionResponse]:
        allowed = allowed_channels(application, client_id)
        return [
            OptionResponse(key=d.key, label=d.label)
            for d in registry.list(ProviderKind.CHANNEL)
            if d.kind is ProviderKind.CHANNEL and d.runnable and d.key in allowed
        ]

    def lane_view(
        client_id: str,
        document: Mapping[str, Any],
        position: int,
        lane: Mapping[str, Any],
        source: Mapping[str, Any],
    ) -> LaneResponse:
        provider = str(source.get("provider", ""))
        channel = str(lane.get("channel", ""))
        settings = source.get("settings")
        settings = settings if isinstance(settings, Mapping) else {}
        sheets = None
        if provider == google_sheets.KEY:
            sheets = SheetsResponse(
                spreadsheet_id=str(settings.get("spreadsheet_id", "")),
                prospects_tab=str(settings.get("prospects_tab", "Prospects")),
            )
        products = [str(p) for p in lane.get("products") or ()]
        channel_descriptor = registry.get(channel)
        return LaneResponse(
            id=str(lane.get("id", "")),
            position=position,
            source=LaneSourceResponse(
                id=str(source.get("id", "")),
                source=provider,
                label=label_of(registry.get(provider), provider),
                google_sheets=sheets,
            ),
            channel=LaneChannelResponse(
                channel=channel,
                label=label_of(channel_descriptor, channel),
                authorized=channel in allowed_channels(application, client_id),
                problem=(
                    channel_problem(application, client_id, channel, document)
                    if channel_descriptor is not None
                    else "The channel of this lane is not available. Choose another one."
                ),
            ),
            products=products,
            offers_all_products=products in ([], [ALL_PRODUCTS]),
        )

    def lanes_response(client_id: str) -> LanesResponse:
        raw, document, version = current_state(client_id)
        if runs_lanes(document):
            sources = {s.get("id"): s for s in _mappings(document["sources"])}
            lanes = [
                lane_view(client_id, document, index, lane, sources.get(lane.get("source"), {}))
                for index, lane in enumerate(_mappings(document["lanes"]), start=1)
            ]
        else:  # the legacy client: its one source and channel, the whole catalog
            provider = source_provider(raw, document)
            implicit_source = {"id": provider, "provider": provider}
            implicit_lane = {
                "id": DEFAULT_LANE,
                "channel": channel_provider_of(raw, document),
                "products": [ALL_PRODUCTS],
            }
            lanes = [lane_view(client_id, document, 1, implicit_lane, implicit_source)]
        return LanesResponse(
            version=version,
            editable=runs_lanes(document),
            lanes=lanes,
            source_options=source_options(),
            channel_options=channel_options(client_id),
            catalog=_catalog(document),
            share_with=google_sheets.service_account_email(
                sheets_credentials_file(application, client_id)
            ),
        )

    def require_lanes(document: Mapping[str, Any], version: int | None, expected: int | None):
        if not runs_lanes(document):
            raise HTTPException(
                409,
                "your account does not run several lanes yet: contact your administrator",
            )
        if version != expected:
            raise HTTPException(
                409, "the settings changed since they were loaded; reload and retry"
            )

    def check_channel(client_id: str, channel: str) -> None:
        descriptor = registry.get(channel)
        if (
            descriptor is None
            or descriptor.kind is not ProviderKind.CHANNEL
            or not descriptor.runnable
        ):
            raise HTTPException(422, "unknown channel")
        if channel not in allowed_channels(application, client_id):
            # Said as plainly for a channel that exists as for one that does not.
            raise HTTPException(403, "this channel is not available for your account")

    def sheet_settings(changes: SheetsChanges | None) -> dict[str, str]:
        found: dict[str, str] = {}
        if changes is None:
            return found
        if changes.spreadsheet is not None:
            try:
                found["spreadsheet_id"] = google_sheets.spreadsheet_id_of(changes.spreadsheet)
            except ValueError as error:
                raise HTTPException(422, f"google_sheets.spreadsheet: {error}") from None
        if changes.prospects_tab is not None:
            found["prospects_tab"] = changes.prospects_tab
        return found

    def publish(
        client_id: str,
        raw: Mapping[str, Any],
        document: dict[str, Any],
        sources: list[dict[str, Any]],
        lanes: list[dict[str, Any]],
        *,
        expected: int | None,
        pause: bool,
        note: str,
    ) -> None:
        drop = ["source"]  # lanes replace the single source
        channel = document.get("channel")
        used = {lane["channel"] for lane in lanes}
        if isinstance(channel, Mapping) and channel.get("provider") not in used:
            drop.append("channel")  # the settings of a channel no lane uses any more
        candidate = {k: v for k, v in document.items() if k not in drop}
        candidate["sources"], candidate["lanes"] = sources, lanes
        check = validate_settings_document(client_id, raw, candidate)
        if check.errors or check.document is None:
            raise HTTPException(422, detail=list(check.errors) or ["invalid settings"])
        try:
            if pause:  # only a write that is valid pauses the agent
                _pause_agent(application, client_id)
            publish_client_settings(
                application,
                client_id,
                {"sources": sources, "lanes": lanes},
                expected_version=expected,
                author=f"client:{client_id}",
                note=note,
                top_keys=("sources", "lanes"),
                sections={},
                drop=tuple(drop),
            )
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        if application.registry.get_config(client_id) is not None:
            with contextlib.suppress(Exception):  # the Admin's runtime status says how it ended
                application.runtime_manager.refresh_client(client_id)

    # ---- reading --------------------------------------------------------------------------

    @router.get("/lanes", response_model=LanesResponse)
    def get_lanes(client: ClientProfile = Depends(get_current_client)) -> LanesResponse:
        try:
            return lanes_response(client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None

    @router.get("/lanes/{lane_id}", response_model=LaneResponse)
    def get_lane(lane_id: str, client: ClientProfile = Depends(get_current_client)) -> LaneResponse:
        try:
            shown = lanes_response(client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        lane = next((lane for lane in shown.lanes if lane.id == lane_id), None)
        if lane is None:
            raise HTTPException(404, "no such lane")
        return lane

    # ---- writing --------------------------------------------------------------------------

    @router.post("/lanes", response_model=LaneResponse, status_code=201)
    async def post_lane(
        request: Request, client: ClientProfile = Depends(get_current_client)
    ) -> LaneResponse:
        body = await _read(request, CreateLaneRequest)
        return await run_in_threadpool(create_lane, body, client)

    def create_lane(body: CreateLaneRequest, client: ClientProfile) -> LaneResponse:
        client_id = client.client_id
        check_channel(client_id, body.channel)
        descriptor = registry.get(body.source.provider)
        if (
            descriptor is None
            or not descriptor.plays(ProviderKind.SOURCE)
            or not descriptor.runnable
            or descriptor.build_instance is None
        ):
            raise HTTPException(422, "unknown source")
        sheets = sheet_settings(body.source.google_sheets)
        if body.source.google_sheets is not None and body.source.provider != google_sheets.KEY:
            raise HTTPException(422, "google_sheets settings belong to the Google Sheets source")
        if body.source.provider == google_sheets.KEY and "spreadsheet_id" not in sheets:
            raise HTTPException(422, "google_sheets.spreadsheet: give the spreadsheet of this lane")
        try:
            raw, document, version = current_state(client_id)
            require_lanes(document, version, body.expected_version)
            sources = _mappings(document["sources"])
            lanes = _mappings(document["lanes"])
            if any(lane.get("id") == body.id for lane in lanes):
                raise HTTPException(409, "a lane with this id already exists")
            if any(source.get("id") == body.source.id for source in sources):
                raise HTTPException(409, "a source with this id already exists")
            sources.append(
                {"id": body.source.id, "provider": body.source.provider, "settings": sheets}
            )
            lanes.append(
                {
                    "id": body.id,
                    "source": body.source.id,
                    "channel": body.channel,
                    "products": list(body.products),
                }
            )
            publish(
                client_id,
                raw,
                document,
                sources,
                lanes,
                expected=version,
                pause=True,
                note="client lane added",
            )
            return lane_of(client_id, body.id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None

    def lane_of(client_id: str, lane_id: str) -> LaneResponse:
        lane = next((lane for lane in lanes_response(client_id).lanes if lane.id == lane_id), None)
        if lane is None:  # published a moment ago: only a concurrent removal can explain this
            raise HTTPException(404, "no such lane")
        return lane

    @router.put("/lanes/{lane_id}", response_model=LaneResponse)
    async def put_lane(
        lane_id: str, request: Request, client: ClientProfile = Depends(get_current_client)
    ) -> LaneResponse:
        body = await _read(request, UpdateLaneRequest)
        return await run_in_threadpool(update_lane, lane_id, body, client)

    def update_lane(lane_id: str, body: UpdateLaneRequest, client: ClientProfile) -> LaneResponse:
        client_id = client.client_id
        if body.channel is None and body.products is None and body.google_sheets is None:
            raise HTTPException(422, "no setting to change")
        if body.channel is not None:
            check_channel(client_id, body.channel)
        sheets = sheet_settings(body.google_sheets)
        try:
            raw, document, version = current_state(client_id)
            require_lanes(document, version, body.expected_version)
            sources = _mappings(document["sources"])
            lanes = _mappings(document["lanes"])
            index = next((i for i, lane in enumerate(lanes) if lane.get("id") == lane_id), None)
            if index is None:
                raise HTTPException(404, "no such lane")
            lane = lanes[index]
            source = next(s for s in sources if s.get("id") == lane.get("source"))
            before = (dict(lane), dict(source), dict(source.get("settings") or {}))
            structural = False
            if body.channel is not None and body.channel != lane["channel"]:
                lane["channel"] = body.channel
                structural = True
            if body.products is not None:
                lane["products"] = list(body.products)
            if body.google_sheets is not None:
                if source.get("provider") != google_sheets.KEY:
                    raise HTTPException(
                        422, "google_sheets settings belong to a Google Sheets source"
                    )
                if not sheets:
                    raise HTTPException(422, "no setting to change")
                merged = {**(source.get("settings") or {}), **sheets}
                structural = structural or merged != before[2]
                source["settings"] = merged
            if (lane, source, dict(source.get("settings") or {})) == before:
                return lane_of(client_id, lane_id)  # nothing to change: nothing is published
            publish(
                client_id,
                raw,
                document,
                sources,
                lanes,
                expected=version,
                pause=structural,
                note="client lane changed",
            )
            return lane_of(client_id, lane_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None

    @router.delete("/lanes/{lane_id}", response_model=LanesResponse)
    def delete_lane(
        lane_id: str,
        expected_version: int = Query(),
        client: ClientProfile = Depends(get_current_client),
    ) -> LanesResponse:
        client_id = client.client_id
        try:
            raw, document, version = current_state(client_id)
            require_lanes(document, version, expected_version)
            sources = _mappings(document["sources"])
            lanes = _mappings(document["lanes"])
            lane = next((lane for lane in lanes if lane.get("id") == lane_id), None)
            if lane is None:
                raise HTTPException(404, "no such lane")
            if len(lanes) == 1:
                raise HTTPException(409, "a client keeps at least one lane")
            lanes.remove(lane)
            # A source belongs to one lane: leaving it would have the agent read a source nobody
            # sells from.
            sources = [s for s in sources if s.get("id") != lane.get("source")]
            publish(
                client_id,
                raw,
                document,
                sources,
                lanes,
                expected=version,
                pause=True,
                note="client lane removed",
            )
            return lanes_response(client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None

    return router
