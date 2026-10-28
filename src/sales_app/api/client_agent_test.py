"""Client API: testing the agent on a person of the client's choice, before it runs on its own.

- `POST /client/v1/agent-test` (`{contact, name?, circuit_id?}`): the agent writes to this person now,
  on the channel of the circuit (the default one when none is named), as it would to any prospect:
  same offer, same rules, same safety checks, same daily limit. The contact is an email address for
  an email circuit and a phone number for the others. The answer says what happened:
  `state` is `sent` (the first message is out), `waiting` (nothing was sent yet: the day's limit is
  reached, or on Telegram the person has to open the conversation first) or `not_sent` (safety or
  the channel refused; `detail` says why). A refusal answers with `{"code", "message"}` in `detail`;
  codes: `agent_not_loaded`, `channel_not_ready`, `dry_run`, `unknown_circuit`, `no_channel`,
  `invalid_contact`, `contact_in_use` (this contact is already a prospect: the test is on its
  conversation).
- `GET /client/v1/agent-test`: the tests so far, newest first.
- `GET /client/v1/agent-test/{prospect_id}`: one test and its conversation, oldest message first.

A test is a prospect like any other: it counts in the conversations, the prospects and the results,
and in the daily limit. It is recognised by its id (`inbound-test-...`), so a later filter can leave it
out. Like every Client route, none takes a client id: the session decides it, and a prospect of
another client (or one that is not a test) is a 404 with the same words as one that does not exist.
Every launch is written in the central audit log (who, which circuit; never a contact nor a text).
Every answer is `Cache-Control: no-store`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_conversation_control import channel_problem
from sales_app.audit_log import OUTCOME_ERROR, OUTCOME_REJECTED, OUTCOME_SUCCESS
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.errors import ClientNotReadyError
from sales_core.contracts import ActionStatus, Direction, LaneId, Prospect, ProspectId
from sales_core.engine import Engine, ManualSendError
from sales_core.engine.engine import TEST_ID_PREFIX

NOT_FOUND = "no such test"
UNAVAILABLE = "tests are temporarily unavailable"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_STATUS_OF_REFUSAL = {
    "dry_run": 409,
    "unknown_lane": 404,
    "no_channel": 409,
    "invalid_contact": 422,
    "contact_in_use": 409,
}


class StartTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    name: str | None = Field(default=None, max_length=120)
    circuit_id: str | None = Field(default=None, max_length=64)


class TestMessage(BaseModel):
    direction: str  # outbound: the agent; inbound: the person
    text: str
    sent_at: datetime


class TestSummary(BaseModel):
    prospect_id: str
    name: str
    contact: str
    channel: str | None
    circuit_id: str | None
    status: str
    messages: int
    replies: int
    last_message_at: datetime | None


class TestListResponse(BaseModel):
    tests: list[TestSummary]


class TestConversationResponse(BaseModel):
    test: TestSummary
    messages: list[TestMessage]


class StartTestResponse(BaseModel):
    prospect_id: str
    state: str  # sent | waiting | not_sent
    detail: str | None


def create_client_agent_test_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1/agent-test", tags=["client"])

    def refuse(status: int, code: str, message: str) -> HTTPException:
        return HTTPException(status, {"code": code, "message": message})

    def run[T](client: ClientProfile, response: Response, work: Callable[[Engine], T]) -> T:
        response.headers["Cache-Control"] = "no-store"
        runtime = application.registry.get(client.client_id)
        if runtime is None:
            raise refuse(409, "agent_not_loaded", "your agent is not loaded by the platform yet")
        try:
            with runtime.lease() as engine:
                return work(engine)
        except ClientNotReadyError:
            raise refuse(
                409, "agent_not_loaded", "your agent is not ready: see your readiness checks"
            ) from None
        except (HTTPException, ManualSendError):
            raise
        except LookupError:
            raise HTTPException(404, NOT_FOUND) from None
        except Exception:  # noqa: BLE001 - a store failure says nothing about its cause
            raise HTTPException(503, UNAVAILABLE) from None

    def audit(client: ClientProfile, outcome: str = OUTCOME_SUCCESS, **after: object) -> None:
        application.audit.record(
            actor=f"client:{client.client_id}",
            actor_role="client",
            action="agent_test.start",
            target="agent_test",
            client_id=client.client_id,
            outcome=outcome,
            after=after or None,
        )

    @router.post("", response_model=StartTestResponse, status_code=201)
    def start(
        body: StartTestRequest,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> StartTestResponse:
        response.headers["Cache-Control"] = "no-store"
        problem = channel_problem(application, client)
        if problem is not None:
            audit(client, OUTCOME_REJECTED, code="channel_not_ready")
            raise refuse(409, "channel_not_ready", problem)
        lane = LaneId(body.circuit_id) if body.circuit_id else None
        try:
            prospect, report = run(
                client,
                response,
                lambda engine: engine.start_test(
                    contact=body.contact,
                    name=body.name or "",
                    lane_id=lane,
                    actor=f"client:{client.client_id}",
                ),
            )
        except ManualSendError as refusal:
            audit(client, OUTCOME_REJECTED, code=refusal.code, circuit=body.circuit_id)
            code = "unknown_circuit" if refusal.code == "unknown_lane" else refusal.code
            raise refuse(_STATUS_OF_REFUSAL.get(refusal.code, 409), code, refusal.message) from None
        except HTTPException as failure:
            audit(client, OUTCOME_ERROR if failure.status_code >= 500 else OUTCOME_REJECTED)
            raise
        state, detail = _state_of(report)
        audit(client, circuit=prospect.lane_id, state=state)
        return StartTestResponse(prospect_id=prospect.prospect_id, state=state, detail=detail)

    @router.get("", response_model=TestListResponse)
    def list_tests(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> TestListResponse:
        def work(engine: Engine) -> TestListResponse:
            found = [
                _summary(engine, p)
                for p in engine.list_prospects()
                if p.prospect_id.startswith(TEST_ID_PREFIX)
            ]
            found.sort(key=lambda s: s.last_message_at or _EPOCH, reverse=True)
            return TestListResponse(tests=found)

        return run(client, response, work)

    @router.get("/{prospect_id}", response_model=TestConversationResponse)
    def one_test(
        prospect_id: str,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> TestConversationResponse:
        def work(engine: Engine) -> TestConversationResponse:
            if not prospect_id.startswith(TEST_ID_PREFIX):
                raise LookupError(prospect_id)
            history = engine.conversation_of(ProspectId(prospect_id))
            prospect = next(
                (p for p in engine.list_prospects() if p.prospect_id == prospect_id), None
            )
            if history is None or prospect is None:
                raise LookupError(prospect_id)
            return TestConversationResponse(
                test=_summary(engine, prospect),
                messages=[
                    TestMessage(direction=m.direction.value, text=m.text, sent_at=m.timestamp)
                    for m in history.messages
                ],
            )

        return run(client, response, work)

    return router


def _state_of(report) -> tuple[str, str | None]:
    """What the launch did, from the Engine's report of the one action it may have planned."""
    if report.errors:
        return "not_sent", report.errors[0]
    if not report.outcomes:
        return "waiting", None
    last = report.outcomes[-1]
    if last.status is ActionStatus.SENT:
        return "sent", None
    if last.status in (ActionStatus.CANCELLED, ActionStatus.FAILED):
        return "not_sent", last.detail
    return "waiting", last.detail


def _summary(engine: Engine, prospect: Prospect) -> TestSummary:
    history = engine.conversation_of(prospect.prospect_id)
    messages = history.messages if history is not None else ()
    contact = prospect.contact
    return TestSummary(
        prospect_id=prospect.prospect_id,
        name=prospect.display_name,
        contact=contact.email or contact.phone or contact.telegram_chat_id or "",
        channel=contact.preferred_channel.value if contact.preferred_channel else None,
        circuit_id=prospect.lane_id,
        status=prospect.status.value,
        messages=len(messages),
        replies=sum(1 for m in messages if m.direction is Direction.INBOUND),
        last_message_at=max((m.timestamp for m in messages), default=None),
    )
