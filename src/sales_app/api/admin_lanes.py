"""Admin API: move a client onto lanes (several sources, each with its own channel and products).

`POST /admin/v1/clients/{client_id}/lanes/adopt` (owner only). A client created from the Admin starts
with one implicit source and channel. Everything that is per source (a second source, a channel and
products for each, the results copied back into the client's sheet or HubSpot, the client's own lane
screens) needs the client to run lanes, and nothing else moves a client there. This does it, once:
the client's current source and channel become its first lane (`main`, the whole catalog), and from
then on the client manages its lanes itself (`/client/v1/lanes`).

Why it is the Admin's decision (and refused without `confirm_new_ids` for a client that has run): the
prospects of a lane are named `<source id>:<native id>`. A client that has never run has nothing to
lose; a client that has would see its prospects counted as new ones. The agent is paused, and the
client starts it again deliberately.
"""

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.api.admin_clients import client_wiring_and_document, publish_client_settings
from sales_app.api.client_agent import runs_lanes, sheets_of, source_provider
from sales_app.api.client_lanes import _pause_agent
from sales_app.bootstrap import Application
from sales_app.channel_status import channel_provider_of
from sales_app.config_store import ConfigStoreError
from sales_app.config_store.models import thaw_document
from sales_app.wiring.providers import ProviderKind, google_sheets, hubspot
from sales_core.contracts import ALL_PRODUCTS

PLACEHOLDER = "TODO"
LANE = "main"


class AdoptLanesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = None  # left out: the settings in force now
    channel: str | None = Field(default=None, max_length=64)  # default: the client's current channel
    confirm_new_ids: bool = False  # the client has run: its prospects get new ids


class AdoptLanesResponse(BaseModel):
    client_id: str
    lane: str
    source: str
    channel: str
    version: int


def first_source(application: Application, raw: Mapping[str, Any], document: Mapping[str, Any]):
    """The client's single source, as the first source of its lanes."""
    provider = source_provider(raw, document)
    settings: dict[str, Any] = {}
    if provider == google_sheets.KEY:
        spreadsheet, tab = sheets_of(document)
        if spreadsheet.strip() not in ("", PLACEHOLDER):
            settings = {"spreadsheet_id": spreadsheet, "prospects_tab": tab or "Prospects"}
    elif provider == hubspot.KEY:
        section = document.get("source")
        held = section.get("settings") if isinstance(section, Mapping) else None
        settings = thaw_document(held) if held else {}
    return provider, settings


def create_admin_lanes_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def has_run(client_id: str) -> bool:
        """Whether the client's agent has ever been started: then its prospects have ids."""
        state = application.agent_state.get(client_id)
        return state is not None

    @router.post(
        "/clients/{client_id}/lanes/adopt", response_model=AdoptLanesResponse, status_code=201
    )
    def adopt_lanes(
        client_id: str, body: AdoptLanesRequest, admin: AdminUser = Depends(require_owner)
    ) -> AdoptLanesResponse:
        ensure_client_access(admin, client_id)  # 404 before existence is even checked
        found = client_wiring_and_document(application, client_id)
        if found is None:
            raise HTTPException(404, "no such client")
        raw, document = found
        if runs_lanes(document):
            raise HTTPException(409, "this client already runs lanes")
        if has_run(client_id) and not body.confirm_new_ids:
            raise HTTPException(
                409,
                "this client's agent has been started: its prospects would get new ids; "
                "confirm to move it anyway",
            )
        expected = body.expected_version
        if "expected_version" not in body.model_fields_set:
            held = application.config_store.current(client_id)
            expected = held.version if held is not None else None
        provider, settings = first_source(application, raw, document)
        descriptor = application.provider_registry.get(provider)
        if (
            descriptor is None
            or not descriptor.plays(ProviderKind.SOURCE)
            or descriptor.build_instance is None
        ):
            raise HTTPException(422, "this client's source cannot run in lanes")
        channel = body.channel or channel_provider_of(raw, document)
        channel_descriptor = application.provider_registry.get(channel)
        if channel_descriptor is None or channel_descriptor.kind is not ProviderKind.CHANNEL:
            raise HTTPException(422, "unknown channel")
        sources = [{"id": LANE, "provider": provider, "settings": settings}]
        lanes = [{"id": LANE, "source": LANE, "channel": channel, "products": [ALL_PRODUCTS]}]
        drop = ["source"]
        current_channel = document.get("channel")
        if isinstance(current_channel, Mapping) and current_channel.get("provider") != channel:
            drop.append("channel")
        try:
            published = publish_client_settings(
                application,
                client_id,
                {"sources": sources, "lanes": lanes},
                expected_version=expected,
                author=f"admin:{admin.username}",
                note="client moved onto lanes",
                top_keys=("sources", "lanes"),
                sections={},
                drop=tuple(drop),
            )
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        _pause_agent(application, client_id)  # only a valid move pauses the agent
        if application.registry.get_config(client_id) is not None:
            try:
                application.runtime_manager.refresh_client(client_id)
            except Exception:  # noqa: BLE001 - the runtime status says how it ended
                pass
        return AdoptLanesResponse(
            client_id=client_id,
            lane=LANE,
            source=provider,
            channel=channel,
            version=published.version,
        )

    return router
