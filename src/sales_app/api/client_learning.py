"""Client API: what the agent learned from its own results, and the client's say over it.

`GET /client/v1/learning` takes no client id: the session decides it. It answers what holds today,
each point with a stable `code` (the screen words it), whether it is something to do or to avoid, how
many conversations stand behind it, and its state: `active` (the agent follows it), `watching` (not
enough yet, the agent does not follow it) or `off` (the client or the agent switched it off). The
`counts` say how many finished conversations were studied, so the screen can explain why nothing was
learned yet. A point is never a quotation: it holds no name, no message, no contact.

`POST /client/v1/learning/{note_id}/decision` takes `{"action": "off" | "confirm" | "on"}`:
`off` stops the agent following a point for good, `confirm` makes it follow it, `on` hands the choice
back to the agent. Nothing here is technical and nothing is required: the agent learns on its own.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.learning_service import LearningService
from sales_core.contracts import LearnedNote

UNAVAILABLE = "what the agent learned is temporarily unavailable"
ENOUGH_TO_SHOW = 5  # finished conversations studied before "nothing yet" stops being the answer


class NoteResponse(BaseModel):
    id: str
    code: str
    kind: Literal["do", "avoid"]
    state: Literal["active", "watching", "off"]
    evidence: Literal["low", "medium", "high"]
    cases: int
    against: int
    product: str | None = None
    confirmed: bool = False
    off_by: Literal["client", "auto"] | None = None
    reason: str = ""


class CountsResponse(BaseModel):
    studied: int  # finished conversations the agent studied
    achieved: int
    unfavourable: int
    needed: int  # studied conversations after which the agent starts to draw lessons


class LearningResponse(BaseModel):
    counts: CountsResponse
    notes: list[NoteResponse]


class DecisionRequest(BaseModel):
    action: Literal["off", "confirm", "on"]


def _note(note: LearnedNote) -> NoteResponse:
    return NoteResponse(
        id=note.note_id,
        code=note.code,
        kind=note.kind,
        state=note.state,
        evidence=note.evidence,
        cases=note.cases,
        against=note.against,
        product=note.product,
        confirmed=note.confirmed_by_client,
        off_by=note.off_by,
        reason=note.reason,
    )


def learning_response(service: LearningService) -> LearningResponse:
    counts = service.counts()
    order = {"active": 0, "watching": 1, "off": 2}
    notes = sorted(service.notes(), key=lambda n: (order[n.state], -n.cases, n.note_id))
    return LearningResponse(
        counts=CountsResponse(
            studied=counts["reviewed"],
            achieved=counts["achieved"],
            unfavourable=counts["unfavourable"],
            needed=ENOUGH_TO_SHOW,
        ),
        notes=[_note(n) for n in notes],
    )


def create_client_learning_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    def service_of(client_id: str) -> LearningService:
        builders = application.builders
        if builders is None or builders.learning is None:
            raise HTTPException(503, UNAVAILABLE)
        return LearningService(client_id, builders.learning(client_id))

    @router.get("/learning", response_model=LearningResponse)
    async def learning(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> LearningResponse:
        response.headers["Cache-Control"] = "no-store"
        try:
            return await run_in_threadpool(lambda: learning_response(service_of(client.client_id)))
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - a store that fails says nothing of the cause
            raise HTTPException(503, UNAVAILABLE) from None

    @router.post("/learning/{note_id}/decision", response_model=NoteResponse)
    async def decide(
        note_id: str,
        body: DecisionRequest,
        client: Annotated[ClientProfile, Depends(get_current_client)],
    ) -> NoteResponse:
        def apply() -> LearnedNote | None:
            return service_of(client.client_id).decide(note_id, body.action, datetime.now(UTC))

        try:
            note = await run_in_threadpool(apply)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            raise HTTPException(503, UNAVAILABLE) from None
        if note is None:
            raise HTTPException(404, "that point is not known")
        return _note(note)

    return router
