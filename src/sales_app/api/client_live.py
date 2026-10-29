"""Client API: what the client's agent is doing right now, and what it did a moment ago.

`GET /client/v1/live` takes no client id: the session decides it. It answers the latest steps, newest
first (`limit` 1..300, default 80), each with a kind (reading_prospects, planning, thinking,
answering, sending, receiving, checking_inbox, syncing, reporting), a state (running, done, failed,
abandoned), when it started and ended, and the person it is about when there is one. `now` repeats
the steps still running, so a screen needs no filtering. The screen asks every second or two; the
answer is never cached.

A step holds no message, address or credential: the exchange with a person is read from the
conversation (`/client/v1/conversations/{id}`), which the screen opens for the step it follows.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.agent_activity import DEFAULT_LIMIT, MAX_LIMIT, RUNNING, ActivityStep, settled
from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile

UNAVAILABLE = "the live view is temporarily unavailable"


class StepResponse(BaseModel):
    id: int
    kind: str
    state: str  # running | done | failed | abandoned
    started_at: datetime
    ended_at: datetime | None
    prospect_id: str | None
    name: str | None
    detail: str | None


class TodayResponse(BaseModel):
    sent: int | None  # messages the agent sent today; null: not known
    limit: int | None  # the most it may send today; null: no limit known


class LiveResponse(BaseModel):
    at: datetime  # the server's clock, so a screen can measure how long a step has been running
    today: TodayResponse | None = None
    now: list[StepResponse]
    recent: list[StepResponse]


def _step(step: ActivityStep) -> StepResponse:
    return StepResponse(
        id=step.seq,
        kind=step.kind,
        state=step.state,
        started_at=step.started_at,
        ended_at=step.ended_at,
        prospect_id=step.prospect_id,
        name=step.name,
        detail=step.detail,
    )


def live_response(
    steps: list[ActivityStep], at: datetime, today: TodayResponse | None = None
) -> LiveResponse:
    shown = [_step(settled(step, at)) for step in steps]
    return LiveResponse(at=at, today=today, now=[s for s in shown if s.state == RUNNING], recent=shown)


def create_client_live_router(application: Application) -> APIRouter:
    def today_of(client_id: str) -> TodayResponse | None:
        """Today's sends against the day's limit, for the quiet moments. Never fails the screen."""
        if application.quotas is None:
            return None
        try:
            status = application.quotas.status(client_id)
        except Exception:  # noqa: BLE001 - a figure that cannot be read is simply left out
            return None
        return TodayResponse(sent=status.daily_used, limit=status.daily_limit)

    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    @router.get("/live", response_model=LiveResponse)
    async def live(
        response: Response,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        client: ClientProfile = Depends(get_current_client),
    ) -> LiveResponse:
        response.headers["Cache-Control"] = "no-store"
        builders = application.builders
        if builders is None or builders.activity is None:
            raise HTTPException(503, UNAVAILABLE)

        def read() -> list[ActivityStep]:
            return builders.activity(client.client_id).latest(limit)  # type: ignore[misc]

        try:
            steps = await run_in_threadpool(read)
        except Exception:  # noqa: BLE001 - a store that fails says nothing of the cause
            raise HTTPException(503, UNAVAILABLE) from None
        return live_response(steps, datetime.now(UTC), await run_in_threadpool(today_of, client.client_id))

    return router
