"""Client API: the messages the agent proposes and its client approves or rejects before they go out.

In `approval` mode every message the Brain writes waits for a person. A client whose prospects live
in a Google Sheet decides in the sheet's Actions tab; a client on any other source (HubSpot) has no
such tab, because its proposals are kept in the platform's own store, so this is where it decides.
The routes work the same for both: they go through the client's running Engine, which owns the
rules (what may be approved, the audit trail, the single writer) and never through a store directly.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, so no id sent by the browser can reach another client's proposals. Every answer is
`Cache-Control: no-store`.

- `GET /client/v1/approvals`: the proposals waiting, oldest first (prospect name, text, language).
- `POST /client/v1/approvals/{action_id}/approve` and `/reject`: decide one. Approving only releases
  the message: it is sent by the next dispatch, after the safety checks every send passes (a prospect
  who opted out meanwhile still receives nothing). 404 if nothing by that id is waiting (unknown,
  already decided, or another client's); 409 if the person can no longer be contacted.
"""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_signup import require_commercial
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.errors import ClientNotReadyError
from sales_core.engine import Engine, ProposalNotApprovableError, ProposalNotFoundError

NOT_FOUND = "no proposal is waiting under this id"
UNAVAILABLE = "proposals are temporarily unavailable"


class ProposalResponse(BaseModel):
    id: str
    prospect_name: str | None
    text: str
    language: str | None
    due_at: datetime


class ProposalsResponse(BaseModel):
    proposals: list[ProposalResponse]


class DecisionResponse(BaseModel):
    id: str
    decision: str  # "approved" | "rejected"


def create_client_approvals_router(application: Application) -> APIRouter:
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
        except (ProposalNotFoundError, ProposalNotApprovableError):
            raise
        except Exception:  # noqa: BLE001 - a store failure says nothing about its cause
            raise HTTPException(503, UNAVAILABLE) from None

    @router.get("/approvals", response_model=ProposalsResponse)
    def list_proposals(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ProposalsResponse:
        found = run(client, response, Engine.pending_proposals)
        return ProposalsResponse(
            proposals=[
                ProposalResponse(
                    id=p.action_id,
                    prospect_name=p.prospect_name,
                    text=p.text,
                    language=p.language,
                    due_at=p.due_at,
                )
                for p in found
            ]
        )

    def decide(client: ClientProfile, response: Response, action_id: str, *, approve: bool):
        if approve:  # an approval sends the message: not for a Free account
            require_commercial(application, client.client_id)
        actor = f"client:{client.client_id}"
        decision = "approved" if approve else "rejected"

        def work(engine: Engine) -> None:
            if approve:
                engine.approve_proposal(action_id, actor=actor)
            else:
                engine.reject_proposal(action_id, actor=actor)

        try:
            run(client, response, work)
        except ProposalNotFoundError:
            raise HTTPException(404, NOT_FOUND) from None
        except ProposalNotApprovableError as error:
            raise HTTPException(409, str(error)) from None
        return DecisionResponse(id=action_id, decision=decision)

    @router.post("/approvals/{action_id}/approve", response_model=DecisionResponse)
    def approve(
        action_id: str, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> DecisionResponse:
        return decide(client, response, action_id, approve=True)

    @router.post("/approvals/{action_id}/reject", response_model=DecisionResponse)
    def reject(
        action_id: str, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> DecisionResponse:
        return decide(client, response, action_id, approve=False)

    return router
