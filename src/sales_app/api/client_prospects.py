"""Client API: the prospects the agent works with and their conversations, read-only.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie. Every statement of the read model filters on that client, and the Engine fallback is bound to
that one client, so no id sent by the browser can reach another client's prospects or messages. Every
answer is `Cache-Control: no-store`.

Where the data comes from. When the deployment has a database, the lists are read from the platform's
read model (`sales_app.read_model`): filtered, searched, sorted and paged by SQL, **without the
Engine**, so they answer whether or not the agent is loaded, as long as the database does. A client
whose read model is still empty (nothing has been indexed yet) is listed through its running Engine
instead, which also fills the read model; there the same filters are applied in memory, except
`has_error` (the Engine's view does not know it: it is then never true, so asking for it lists
nobody). Without a database only the Engine path exists (409 if the agent is not loaded).

- `GET /client/v1/prospects`: a page of the client's prospects. `limit` 1..100 (default 50), `offset`
  >= 0; anything else answers 422. Optional narrowing, all combinable: `q` (name, e-mail address or
  phone number; every word must be found), `status` (repeatable: new, active, paused, opted_out,
  closed), `lane` (the circuit), `product` (the lanes that sell it), `channel`, `source`,
  `needs_human`, `has_error`, `active_since` / `active_until` (ISO 8601; last activity in
  [since, until)). `sort` is `name` (default: by name then id) or `recent` (latest activity first).
  The answer holds `total` (how many match), `limit`, `offset` and the page.
- `GET /client/v1/conversations`: the same narrowing, over the prospects with at least one stored
  message, each with its message count and its latest message. `sort` defaults to `recent`.
- `GET /client/v1/conversations/{prospect_id}`: the messages exchanged with one prospect, with the
  prospect's status, its next planned follow-up and the actions that failed. `limit` 1..500 (default
  200), `offset`, `order` (`asc` oldest first, default, or `desc`); the answer holds `total_messages`,
  so a longer history is read page after page. 404, with the same words, for a prospect that does not
  exist and for one that belongs to another client. 409 if the agent is needed and not loaded or not
  ready, 503 if the store fails.
  The answer also tells who is in charge (`control`: the agent, or a person, awaiting one when the
  agent handed the conversation over) and who wrote each message (`author`: prospect, agent, human).
  Taking over, writing yourself and giving back are in `client_conversation_control`.
- `GET /client/v1/summary`: counts computed from the Engine's read (unchanged).

Only fields the stored model holds are returned. What the source keeps in free columns
(`attributes`), the message metadata, the channel provider's ids and the Telegram chat id are not.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_agent import runs_lanes
from sales_app.api.client_conversation_control import ControlResponse, control_response
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.errors import ClientNotReadyError
from sales_app.metrics import lanes_selling
from sales_app.read_model import (
    DEFAULT_MESSAGE_PAGE,
    DEFAULT_PAGE,
    MAX_MESSAGE_PAGE,
    MAX_PAGE,
    ConversationDetail,
    ConversationItem,
    LifecycleStatus,
    MessageItem,
    MessageOrder,
    Page,
    ProspectFilter,
    ProspectItem,
    ProspectListQuery,
    ProspectReadModel,
    ProspectSort,
    ReadModelUnavailableError,
    conversations_in_memory,
    list_in_memory,
)
from sales_core.contracts import Channel, ProspectId
from sales_core.engine import Engine

DEFAULT_LIMIT = DEFAULT_PAGE
MAX_LIMIT = MAX_PAGE
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
    # Added with the read model; the fields above are unchanged.
    source_id: str | None = None
    last_activity_at: datetime | None = None  # the latest of last_contacted_at, last_inbound_at
    needs_human: bool = False  # paused: waiting for a person
    status_reason: str | None = None
    # True/False when read from the actions; null when the answer's source cannot tell.
    has_error: bool | None = None


class ProspectsResponse(BaseModel):
    total: int  # how many prospects match the filters
    limit: int
    offset: int
    prospects: list[ProspectResponse]


class ConversationListItemResponse(BaseModel):
    prospect: ProspectResponse
    message_count: int | None  # null: not known (listed through the Engine)
    last_message_at: datetime | None
    last_message_direction: str | None  # inbound | outbound


class ConversationsResponse(BaseModel):
    total: int
    limit: int
    offset: int
    conversations: list[ConversationListItemResponse]


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
    # For a WhatsApp message we sent: what WhatsApp reported (sent | delivered | read | failed).
    # None when the channel reports nothing or nothing has been reported yet.
    delivery: str | None = None
    # Who wrote it: prospect | agent | human (a person of the client, see take-over). Null: not known.
    author: str | None = None


class FollowUpResponse(BaseModel):
    action_type: str  # follow_up | reminder | callback | ...
    due_at: datetime


class ActionErrorResponse(BaseModel):
    action_id: str
    action_type: str
    due_at: datetime | None
    attempts: int
    reason: str | None


class ConversationResponse(BaseModel):
    prospect_id: str
    conversation_id: str
    origin: str  # outbound | inbound | unknown: who started it
    messages: list[MessageResponse]
    # Added with the read model; the fields above are unchanged.
    total_messages: int | None = None  # all the messages of the conversation
    limit: int | None = None
    offset: int | None = None
    order: str | None = None
    prospect: ProspectResponse | None = None  # status, circuit, channel... (null: not known)
    next_follow_up: FollowUpResponse | None = None  # the planned next action, if any
    errors: list[ActionErrorResponse] = []  # actions that failed, newest first
    # Who is in charge: the agent (mode "agent"), or a person (mode "human"), awaiting one when the
    # agent handed the conversation over and nobody took it yet. Null: not known.
    control: ControlResponse | None = None


def prospect_response(item: ProspectItem) -> ProspectResponse:
    return ProspectResponse(
        id=item.prospect_id,
        display_name=item.display_name,
        status=item.status,
        stage=item.stage,
        locale=item.locale,
        lane_id=item.lane_id,
        phone=item.phone,
        email=item.email,
        preferred_channel=item.channel,
        next_action_type=item.next_action_type,
        next_action_due_at=item.next_action_at,
        last_contacted_at=item.last_contacted_at,
        last_inbound_at=item.last_inbound_at,
        follow_up_count=item.follow_up_count,
        source_id=item.source_id,
        last_activity_at=item.last_activity_at,
        needs_human=item.needs_human,
        status_reason=item.status_reason,
        has_error=item.has_error,
    )


def item_of_prospect(p: Any) -> ProspectItem:
    """The Engine's `Prospect` in the read model's shape (for a client not indexed yet)."""
    moments = [m for m in (p.schedule.last_contacted_at, p.schedule.last_inbound_at) if m]
    return ProspectItem(
        prospect_id=p.prospect_id,
        display_name=p.display_name,
        status=p.status.value,
        stage=p.stage,
        locale=p.locale,
        lane_id=p.lane_id,
        phone=p.contact.phone,
        email=p.contact.email,
        channel=p.contact.preferred_channel.value if p.contact.preferred_channel else None,
        next_action_type=p.schedule.next_action_type.value if p.schedule.next_action_type else None,
        next_action_at=p.schedule.next_action_due_at,
        last_contacted_at=p.schedule.last_contacted_at,
        last_inbound_at=p.schedule.last_inbound_at,
        last_activity_at=max(moments) if moments else None,
        follow_up_count=p.schedule.follow_up_count,
        needs_human=p.status.value == LifecycleStatus.PAUSED,
        conversation_id=p.conversation_id,
        has_error=None,
    )


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def lane_products(application: Application, client_id: str) -> Mapping[str, list[str]] | None:
    """Lane id -> the products it sells (empty list: the whole catalog), from the client's settings.
    None for a client that runs no lanes of its own (one implicit lane, the whole catalog), or whose
    settings cannot be read."""
    try:
        found = client_wiring_and_document(application, client_id)
    except Exception:  # noqa: BLE001 - settings that cannot be read are not a reason to refuse a list
        return None
    if found is None or not runs_lanes(found[1]):
        return None
    lanes = found[1].get("lanes") or ()
    return {
        str(lane.get("id")): [str(x) for x in (lane.get("products") or ())]
        for lane in lanes
        if hasattr(lane, "get")
    }


class Filters:
    """The narrowing shared by both lists, as query parameters (one definition, so the two lists
    can never disagree)."""

    def __init__(
        self,
        q: Annotated[str | None, Query(max_length=200)] = None,
        status: Annotated[list[LifecycleStatus] | None, Query()] = None,
        lane: Annotated[str | None, Query(max_length=128)] = None,
        product: Annotated[str | None, Query(max_length=128)] = None,
        channel: Channel | None = None,
        source: Annotated[str | None, Query(max_length=128)] = None,
        needs_human: bool | None = None,
        has_error: bool | None = None,
        active_since: datetime | None = None,
        active_until: datetime | None = None,
    ) -> None:
        self.values: dict[str, Any] = {
            "q": q,
            "status": status or [],
            "lane": lane,
            "product": product,
            "channel": channel,
            "source": source,
            "needs_human": needs_human,
            "has_error": has_error,
            "active_since": active_since,
            "active_until": active_until,
        }


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

    def read_model(client: ClientProfile, response: Response) -> ProspectReadModel | None:
        """The client's read model if it holds anything; None to go through the Engine."""
        response.headers["Cache-Control"] = "no-store"
        factory = application.read_models
        if factory is None:
            return None
        model = factory(client.client_id)
        try:
            return model if model.is_populated() else None
        except ReadModelUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None

    def ask[T](model: ProspectReadModel, work: Callable[[ProspectReadModel], T]) -> T:
        try:
            return work(model)
        except ReadModelUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None

    def build_query(
        client: ClientProfile,
        *,
        limit: int,
        offset: int,
        sort: ProspectSort,
        q: str | None,
        status: Iterable[LifecycleStatus],
        lane: str | None,
        product: str | None,
        channel: Channel | None,
        source: str | None,
        needs_human: bool | None,
        has_error: bool | None,
        active_since: datetime | None,
        active_until: datetime | None,
    ) -> ProspectListQuery:
        lanes: tuple[str, ...] | None = None
        if product is not None:
            mapping = lane_products(application, client.client_id)
            lanes = None if mapping is None else lanes_selling(product, mapping)
        if lane is not None:  # both given: the lane must also sell the product
            lanes = (lane,) if lanes is None or lane in lanes else ()
        try:
            return ProspectListQuery(
                filter=ProspectFilter(
                    statuses=tuple(s.value for s in status),
                    lane_ids=lanes,
                    channel=channel.value if channel else None,
                    source_id=source,
                    needs_human=needs_human,
                    has_error=has_error,
                    active_since=_utc(active_since),
                    active_until=_utc(active_until),
                    search=(q or "").strip() or None,
                ),
                sort=sort,
                limit=limit,
                offset=offset,
            )
        except ValueError as problem:
            raise HTTPException(422, str(problem)) from None

    def engine_items(client: ClientProfile, response: Response) -> list[ProspectItem]:
        return [item_of_prospect(p) for p in run(client, response, Engine.list_prospects)]

    @router.get("/prospects", response_model=ProspectsResponse)
    def list_prospects(
        response: Response,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
        sort: ProspectSort = ProspectSort.NAME,
        filters: Filters = Depends(),
        client: ClientProfile = Depends(get_current_client),
    ) -> ProspectsResponse:
        query = build_query(client, limit=limit, offset=offset, sort=sort, **filters.values)
        model = read_model(client, response)
        if model is not None:
            page: Page[ProspectItem] = ask(model, lambda m: m.list_prospects(query))
        else:
            page = list_in_memory(engine_items(client, response), query)
        return ProspectsResponse(
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            prospects=[prospect_response(i) for i in page.items],
        )

    @router.get("/conversations", response_model=ConversationsResponse)
    def list_conversations(
        response: Response,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
        sort: ProspectSort = ProspectSort.RECENT,
        filters: Filters = Depends(),
        client: ClientProfile = Depends(get_current_client),
    ) -> ConversationsResponse:
        query = build_query(client, limit=limit, offset=offset, sort=sort, **filters.values)
        model = read_model(client, response)
        if model is not None:
            page: Page[ConversationItem] = ask(model, lambda m: m.list_conversations(query))
        else:
            page = conversations_in_memory(engine_items(client, response), query)
        return ConversationsResponse(
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            conversations=[
                ConversationListItemResponse(
                    prospect=prospect_response(c.prospect),
                    message_count=c.message_count,
                    last_message_at=c.last_message_at,
                    last_message_direction=c.last_message_direction,
                )
                for c in page.items
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
        prospect_id: str,
        response: Response,
        limit: Annotated[int, Query(ge=1, le=MAX_MESSAGE_PAGE)] = DEFAULT_MESSAGE_PAGE,
        offset: Annotated[int, Query(ge=0)] = 0,
        order: MessageOrder = MessageOrder.ASC,
        client: ClientProfile = Depends(get_current_client),
    ) -> ConversationResponse:
        detail: ConversationDetail | None = None
        model = read_model(client, response)
        if model is not None:
            detail = ask(
                model,
                lambda m: m.conversation(prospect_id, limit=limit, offset=offset, order=order),
            )
        if detail is not None:
            item = detail.prospect
            planned = item.next_action_type and item.next_action_at
            return ConversationResponse(
                prospect_id=item.prospect_id,
                conversation_id=detail.conversation_id,
                origin="unknown",
                messages=_messages(
                    detail.messages, _deliveries(application, client.client_id, detail.messages)
                ),
                total_messages=detail.total_messages,
                limit=detail.limit,
                offset=detail.offset,
                order=detail.order.value,
                prospect=prospect_response(item),
                next_follow_up=(
                    FollowUpResponse(action_type=item.next_action_type, due_at=item.next_action_at)
                    if planned and item.status in {"new", "active"}
                    else None
                ),
                errors=[
                    ActionErrorResponse(
                        action_id=e.action_id,
                        action_type=e.action_type,
                        due_at=e.due_at,
                        attempts=e.attempts,
                        reason=e.reason,
                    )
                    for e in detail.errors
                ],
                control=_control(application, client.client_id, prospect_id),
            )
        found = run(
            client, response, lambda engine: engine.conversation_of(ProspectId(prospect_id))
        )
        if found is None:
            raise HTTPException(404, NOT_FOUND)
        everything = sorted(found.messages, key=lambda m: m.timestamp)
        if order is MessageOrder.DESC:
            everything.reverse()
        items = [
            MessageItem(
                message_id=m.message_id,
                direction=m.direction.value,
                channel=m.channel.value,
                text=m.text,
                timestamp=m.timestamp,
                external_id=m.external_id,
                author=(
                    "prospect"
                    if m.direction.value == "inbound"
                    else ("human" if m.metadata.get("author") == "human" else "agent")
                ),
            )
            for m in everything[offset : offset + limit]
        ]
        return ConversationResponse(
            prospect_id=found.prospect_id,
            conversation_id=found.conversation_id,
            origin=found.origin.value,
            messages=_messages(items, _deliveries(application, client.client_id, items)),
            total_messages=len(everything),
            limit=limit,
            offset=offset,
            order=order.value,
            control=_control(application, client.client_id, prospect_id),
        )

    return router


def _control(application: Application, client_id: str, prospect_id: str) -> ControlResponse | None:
    """Who is in charge of the conversation. A store that cannot be read only means it is not shown;
    the conversation is never refused for it."""
    controls = application.conversation_controls
    if controls is None:
        return None
    try:
        return control_response(controls(client_id).get(ProspectId(prospect_id)))
    except Exception:  # noqa: BLE001
        return None


def _messages(
    messages: Iterable[MessageItem], delivered: Mapping[str, str]
) -> list[MessageResponse]:
    return [
        MessageResponse(
            id=m.message_id,
            direction=m.direction,
            channel=m.channel,
            text=m.text,
            timestamp=m.timestamp,
            delivery=delivered.get(m.external_id or ""),
            author=m.author,
        )
        for m in messages
    ]


def _deliveries(
    application: Application, client_id: str, messages: Iterable[MessageItem]
) -> dict[str, str]:
    """WhatsApp's own report for the messages we sent, by the id WhatsApp gave them. A register that
    cannot be read only means no status is shown; the conversation is never refused for it."""
    service = application.whatsapp
    ids = [
        m.external_id
        for m in messages
        if m.external_id and m.direction == "outbound" and m.channel == "whatsapp"
    ]
    if service is None or not ids:
        return {}
    try:
        return {m: r.status for m, r in service.state.deliveries(client_id, ids).items()}
    except Exception:  # noqa: BLE001
        return {}
