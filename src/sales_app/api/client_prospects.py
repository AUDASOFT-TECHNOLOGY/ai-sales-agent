"""Client API: the prospects the agent works with and their conversations, read-only.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie. The data is read through the client's running Engine, whose repository is bound to that one
client, so no id sent by the browser can reach another client's prospects or messages. Every answer
is `Cache-Control: no-store`.

- `GET /client/v1/prospects?limit=50&offset=0`: a page of the client's prospects, ordered by name
  then id. `limit` is 1..100 (default 50), `offset` is >= 0 (default 0); anything else answers 422.
  The answer holds `total` (all the client's prospects), the `limit` and `offset` used, and the page.
- `GET /client/v1/summary`: counts computed from that same read: the total, one count per status,
  and the upcoming actions (prospects that are new or active with a next action recorded, by type,
  and the earliest due date). Nothing is estimated: if the data cannot be read the answer is 409
  or 503, never zeros. The agent's own state is `GET /client/v1/agent`, not repeated here.
- `GET /client/v1/conversations/{prospect_id}`: the messages exchanged with one prospect, oldest
  first. 404, with the same words, for a prospect that does not exist and for one that belongs to
  another client. 409 if the agent is not loaded or not ready, 503 if the store fails.

Only fields the stored model holds are returned. What the source keeps in free columns
(`attributes`), the message metadata, the channel provider's ids and the Telegram chat id are not.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.errors import ClientNotReadyError
from sales_core.contracts import ProspectId
from sales_core.engine import Engine

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
NOT_FOUND = "no such prospect"
UNAVAILABLE = "prospects are temporarily unavailable"


class ProspectResponse(BaseModel):
    id: str
    display_name: str
    status: str  # new | active | paused | opted_out | closed
    stage: str | None
    locale: str | None
    lane_id: str | None
    phone: str | None
    email: str | None
    preferred_channel: str | None
    next_action_type: str | None
    next_action_due_at: datetime | None
    last_contacted_at: datetime | None
    last_inbound_at: datetime | None
    follow_up_count: int


class ProspectsResponse(BaseModel):
    total: int
    limit: int
    offset: int
    prospects: list[ProspectResponse]


class UpcomingActionsResponse(BaseModel):
    scheduled: int  # prospects the agent may still contact that have a next action recorded
    by_type: dict[str, int]  # only the types present, never a zero invented for an absent one
    next_due_at: datetime | None  # the earliest of them, None when nothing is scheduled


class SummaryResponse(BaseModel):
    total_prospects: int
    by_status: dict[str, int]  # every status, 0 only when the client really has none
    upcoming_actions: UpcomingActionsResponse


class MessageResponse(BaseModel):
    id: str
    direction: str  # inbound | outbound
    channel: str
    text: str
    timestamp: datetime


class ConversationResponse(BaseModel):
    prospect_id: str
    conversation_id: str
    origin: str  # outbound | inbound | unknown: who started it
    messages: list[MessageResponse]


def create_client_prospects_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    def run[T](client: ClientProfile, response: Response, work: Callable[[Engine], T]) -> T:
        response.headers["Cache-Control"] = "no-store"
        runtime = application.registry.get(client.client_id)
        if runtime is None:
            raise HTTPException(409, "your agent is not loaded by the platform yet")
        try:
            with runtime.lease() as engine:
                return work(engine)
        except ClientNotReadyError:
            raise HTTPException(409, "your agent is not ready: see your readiness checks") from None
        except Exception:  # noqa: BLE001 - a store failure says nothing about its cause
            raise HTTPException(503, UNAVAILABLE) from None

    @router.get("/prospects", response_model=ProspectsResponse)
    def list_prospects(
        response: Response,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
        client: ClientProfile = Depends(get_current_client),
    ) -> ProspectsResponse:
        found = run(client, response, Engine.list_prospects)
        return ProspectsResponse(
            total=len(found),
            limit=limit,
            offset=offset,
            prospects=[
                ProspectResponse(
                    id=p.prospect_id,
                    display_name=p.display_name,
                    status=p.status.value,
                    stage=p.stage,
                    locale=p.locale,
                    lane_id=p.lane_id,
                    phone=p.contact.phone,
                    email=p.contact.email,
                    preferred_channel=(
                        p.contact.preferred_channel.value if p.contact.preferred_channel else None
                    ),
                    next_action_type=(
                        p.schedule.next_action_type.value if p.schedule.next_action_type else None
                    ),
                    next_action_due_at=p.schedule.next_action_due_at,
                    last_contacted_at=p.schedule.last_contacted_at,
                    last_inbound_at=p.schedule.last_inbound_at,
                    follow_up_count=p.schedule.follow_up_count,
                )
                for p in found[offset : offset + limit]
            ],
        )

    @router.get("/summary", response_model=SummaryResponse)
    def get_summary(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> SummaryResponse:
        summary = run(client, response, Engine.summarize_prospects)
        return SummaryResponse(
            total_prospects=summary.total,
            by_status=summary.by_status,
            upcoming_actions=UpcomingActionsResponse(
                scheduled=summary.upcoming.scheduled,
                by_type=summary.upcoming.by_type,
                next_due_at=summary.upcoming.next_due_at,
            ),
        )

    @router.get("/conversations/{prospect_id}", response_model=ConversationResponse)
    def get_conversation(
        prospect_id: str, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ConversationResponse:
        found = run(
            client, response, lambda engine: engine.conversation_of(ProspectId(prospect_id))
        )
        if found is None:
            raise HTTPException(404, NOT_FOUND)
        return ConversationResponse(
            prospect_id=found.prospect_id,
            conversation_id=found.conversation_id,
            origin=found.origin.value,
            messages=[
                MessageResponse(
                    id=m.message_id,
                    direction=m.direction.value,
                    channel=m.channel.value,
                    text=m.text,
                    timestamp=m.timestamp,
                )
                for m in sorted(found.messages, key=lambda m: m.timestamp)
            ],
        )

    return router
