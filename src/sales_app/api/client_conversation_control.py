"""Client API: taking a conversation over, writing to the prospect yourself, giving it back (spec 9.3).

- `POST /client/v1/conversations/{prospect_id}/take-over` (optional body `{reason}`): from now on the
  agent sends nothing on its own to this prospect: no reply, no follow-up. Its open actions are
  cancelled, the conversation shows as waiting for a person, the schedule is kept.
- `POST /client/v1/conversations/{prospect_id}/messages` (`{text, idempotency_key}`): a message you
  wrote, sent on the channel the prospect is reached on, **only** while you are in charge of the
  conversation and the channel is ready. It goes through the same channel as the agent's messages, so
  consent, a stop request, the daily send limits and WhatsApp's 24-hour window apply as they do for
  the agent; it is stored with the author `human`. The same key never sends twice (a repeat answers
  with the first outcome, `replayed: true`). A refusal answers with `{"code", "message"}` in `detail`;
  codes: `agent_not_loaded`, `channel_not_ready`, `not_in_human_mode`, `opted_out`, `no_contact`,
  `not_linked`, `no_channel`, `not_now` (the channel says wait: outside WhatsApp's window with no
  approved template, a limit reached), `send_failed`, `unconfirmed` (outcome unknown: look at the
  conversation before writing again), `dry_run`, `empty`, `unknown_prospect`.
- `POST /client/v1/conversations/{prospect_id}/release`: the agent takes the conversation back. A
  prospect who opted out stays so; the follow-up count starts over; if nothing is due ahead the next
  follow-up is planned after the client's own wait (see `followup-settings`).

Like every Client route, none takes a client id: the session decides it, the Engine is the client's
own, and a prospect of another client is a 404 with the same words as one that does not exist. Every
action is written in the central audit log (who, what, which conversation; never a message text).
Every answer is `Cache-Control: no-store`.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_agent import evaluate_readiness
from sales_app.api.client_followup import resume_after
from sales_app.api.client_signup import require_commercial
from sales_app.audit_log import OUTCOME_ERROR, OUTCOME_REJECTED, OUTCOME_SUCCESS
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.errors import ClientNotReadyError
from sales_core.contracts import ProspectId
from sales_core.engine import (
    ControlUnavailableError,
    Engine,
    ManualSendError,
)
from sales_core.ports import ConversationControl

NOT_FOUND = "no such prospect"
UNAVAILABLE = "conversations are temporarily unavailable"
KEY_PATTERN = r"^[A-Za-z0-9_.:-]{8,64}$"
MAX_TEXT = 4000
CHANNEL_CHECKS = ("whatsapp_number", "channel_ready")

_STATUS_OF_REFUSAL = {
    "empty": 422,
    "unknown_prospect": 404,
    "not_in_human_mode": 409,
    "opted_out": 409,
    "no_contact": 409,
    "not_linked": 409,
    "no_channel": 409,
    "not_now": 409,
    "unconfirmed": 409,
    "dry_run": 409,
    "control_unavailable": 409,
    "send_failed": 502,
}


class ControlResponse(BaseModel):
    mode: str  # agent | human
    awaiting_human: bool  # handed over by the agent, nobody took it yet
    taken_by: str | None
    taken_at: datetime | None
    reason: str | None
    released_at: datetime | None


def control_response(control: ConversationControl | None) -> ControlResponse:
    """No row at all is the agent, in charge since the start."""
    if control is None:
        return ControlResponse(
            mode="agent",
            awaiting_human=False,
            taken_by=None,
            taken_at=None,
            reason=None,
            released_at=None,
        )
    return ControlResponse(
        mode=control.mode.value,
        awaiting_human=control.awaiting_human,
        taken_by=control.taken_by,
        taken_at=control.taken_at,
        reason=control.reason,
        released_at=control.released_at,
    )


class TakeOverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=300)


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)
    ]
    idempotency_key: Annotated[str, StringConstraints(pattern=KEY_PATTERN)]


class SentMessageResponse(BaseModel):
    message_id: str
    channel: str
    sent_at: datetime | None
    replayed: bool


class ReleaseResponse(BaseModel):
    released: bool  # False: the agent was already in charge
    control: ControlResponse
    status: str | None  # the prospect's status afterwards
    next_follow_up_at: datetime | None  # when the agent comes back, null: nothing is scheduled


def create_client_conversation_control_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

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
        except ControlUnavailableError:
            raise refuse(
                409, "control_unavailable", "taking over conversations is not available"
            ) from None
        except LookupError:
            raise HTTPException(404, NOT_FOUND) from None
        except Exception:  # noqa: BLE001 - a store failure says nothing about its cause
            raise HTTPException(503, UNAVAILABLE) from None

    def audit(
        client: ClientProfile,
        action: str,
        prospect_id: str,
        outcome: str = OUTCOME_SUCCESS,
        **after: object,
    ) -> None:
        application.audit.record(
            actor=f"client:{client.client_id}",
            actor_role="client",
            action=action,
            target=f"conversation:{prospect_id}",
            client_id=client.client_id,
            outcome=outcome,
            after=after or None,
        )

    @router.post("/conversations/{prospect_id}/take-over", response_model=ControlResponse)
    def take_over(
        prospect_id: str,
        response: Response,
        body: TakeOverRequest | None = None,
        client: ClientProfile = Depends(get_current_client),
    ) -> ControlResponse:
        reason = (body.reason if body is not None else None) or None
        try:
            control = run(
                client,
                response,
                lambda engine: engine.take_over(
                    ProspectId(prospect_id), by=f"client:{client.client_id}", reason=reason
                ),
            )
        except HTTPException as problem:
            audit(client, "conversation.take_over", prospect_id, _outcome_of(problem))
            raise
        audit(client, "conversation.take_over", prospect_id, mode=control.mode.value)
        return control_response(control)

    @router.post("/conversations/{prospect_id}/release", response_model=ReleaseResponse)
    def release(
        prospect_id: str,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> ReleaseResponse:
        # Releasing hands the conversation back to the agent, which sells: not for a Free account.
        require_commercial(application, client.client_id)
        wait = resume_after(application, client.client_id)
        try:
            outcome, control = run(
                client,
                response,
                lambda engine: (
                    engine.release(
                        ProspectId(prospect_id),
                        by=f"client:{client.client_id}",
                        resume_after=wait,
                    ),
                    engine.control_of(ProspectId(prospect_id)),
                ),
            )
        except HTTPException as problem:
            audit(client, "conversation.release", prospect_id, _outcome_of(problem))
            raise
        audit(
            client,
            "conversation.release",
            prospect_id,
            released=outcome.released,
            status=outcome.status,
        )
        return ReleaseResponse(
            released=outcome.released,
            control=control_response(control),
            status=outcome.status,
            next_follow_up_at=outcome.next_follow_up_at,
        )

    @router.post(
        "/conversations/{prospect_id}/messages", response_model=SentMessageResponse, status_code=201
    )
    def send_message(
        prospect_id: str,
        body: SendMessageRequest,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> SentMessageResponse:
        response.headers["Cache-Control"] = "no-store"
        require_commercial(application, client.client_id)  # a Free account sends nothing
        actor = f"client:{client.client_id}"
        problem = channel_problem(application, client)
        if problem is not None:
            audit(
                client,
                "conversation.message",
                prospect_id,
                OUTCOME_REJECTED,
                code="channel_not_ready",
            )
            raise refuse(409, "channel_not_ready", problem)
        try:
            receipt = run(
                client,
                response,
                lambda engine: engine.send_manual(
                    ProspectId(prospect_id),
                    body.text,
                    idempotency_key=body.idempotency_key,
                    actor=actor,
                ),
            )
        except ManualSendError as refusal:
            audit(
                client,
                "conversation.message",
                prospect_id,
                OUTCOME_ERROR
                if refusal.code in ("send_failed", "unconfirmed")
                else OUTCOME_REJECTED,
                code=refusal.code,
            )
            raise refuse(
                _STATUS_OF_REFUSAL.get(refusal.code, 409), refusal.code, refusal.message
            ) from None
        except HTTPException as failure:
            audit(client, "conversation.message", prospect_id, _outcome_of(failure))
            raise
        audit(
            client,
            "conversation.message",
            prospect_id,
            channel=receipt.channel,
            replayed=receipt.replayed,
        )
        if receipt.replayed:
            response.status_code = 200
        return SentMessageResponse(
            message_id=receipt.message_id,
            channel=receipt.channel,
            sent_at=receipt.sent_at,
            replayed=receipt.replayed,
        )

    return router


def channel_problem(application: Application, client: ClientProfile) -> str | None:
    """Why the client's channel cannot send now, in words the client can read; None if it can."""
    for check in evaluate_readiness(application, client):
        if check.id in CHANNEL_CHECKS and not check.ok:
            return check.message
    return None


def _outcome_of(problem: HTTPException) -> str:
    return OUTCOME_ERROR if problem.status_code >= 500 else OUTCOME_REJECTED
