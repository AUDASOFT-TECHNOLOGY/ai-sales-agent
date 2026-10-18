"""Client API: the payments a buyer says they made, and the client's own confirmation of them.

Revansa is not a payment gateway. When a buyer says they paid or sends a receipt, nothing is
confirmed: the agent only records what was said. A payment counts as paid when a person at the client
has checked their own account and confirms it here, with the amount, the currency and a reference.
That is the only door (`sales_core.payments.PaymentConfirmationService`); the buyer, the agent and a
message can never confirm a payment.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, so no id sent by the browser can reach another client's payments. Every answer is
`Cache-Control: no-store`.

- `GET /client/v1/payments`: the payments to check (a claim or a receipt, newest first) and those
  already confirmed. Only people who wrote back can have said they paid, so only they are looked at
  (the most recent ones, a bounded number).
- `POST /client/v1/payments/{prospect_id}/confirm` `{amount, currency, reference}`: records the
  confirmation by the logged-in client. Idempotent: confirming the same payment again changes nothing.
  404 if the prospect is unknown; 409 if the same reference was confirmed with other terms, or the
  agent is not loaded; 422 for terms that are not valid.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StringConstraints

from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.errors import ClientNotReadyError
from sales_core.contracts import (
    CONFIRM_PAYMENTS,
    ConfirmingActor,
    PaymentCase,
    PaymentConfirmation,
    PaymentConfirmationStatus,
)
from sales_core.engine import Engine
from sales_core.payments import PaymentDesk, PaymentsNotSupportedError

UNAVAILABLE = "payments are temporarily unavailable"
MAX_DIGITS = 12

Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Amount = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]


class PaymentCaseResponse(BaseModel):
    prospect_id: str
    prospect_name: str | None
    progress: str  # claimed | proof_received | paid
    signalled_at: datetime | None
    confirmed_at: datetime | None
    confirmed_by: str | None
    amount: str | None
    currency: str | None
    reference: str | None


class PaymentsResponse(BaseModel):
    cases: list[PaymentCaseResponse]


class ConfirmRequest(BaseModel):
    """`extra="forbid"`: nothing but the terms the person checked."""

    model_config = ConfigDict(extra="forbid")

    amount: Amount
    currency: Currency
    reference: Reference


def _response(case: PaymentCase) -> PaymentCaseResponse:
    return PaymentCaseResponse(
        prospect_id=case.prospect_id,
        prospect_name=case.prospect_name,
        progress=case.progress.value,
        signalled_at=case.signalled_at,
        confirmed_at=case.confirmed_at,
        confirmed_by=case.confirmed_by,
        amount=case.amount,
        currency=case.currency,
        reference=case.reference,
    )


def _amount(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise HTTPException(422, "the amount is not a number") from None
    if not value.is_finite() or value <= 0 or len(value.as_tuple().digits) > MAX_DIGITS:
        raise HTTPException(422, "the amount must be a number above zero")
    return value


def create_client_payments_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    def desk[T](engine: Engine, client_id: str, work: Callable[[PaymentDesk], T]) -> T:
        """The person's desk over this client's own records, under the Engine's single writer."""
        return engine.exclusive(
            lambda repository, facts, writer, clock: work(
                PaymentDesk(
                    client_id=client_id,
                    repository=repository,
                    facts=facts,
                    facts_writer=writer,
                    clock=clock,
                )
            )
        )

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
        except PaymentsNotSupportedError:
            raise HTTPException(409, "payments cannot be tracked for your account") from None
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - a store failure says nothing about its cause
            raise HTTPException(503, UNAVAILABLE) from None

    @router.get("/payments", response_model=PaymentsResponse)
    def list_payments(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> PaymentsResponse:
        cases = run(
            client, response, lambda engine: desk(engine, client.client_id, PaymentDesk.cases)
        )
        return PaymentsResponse(cases=[_response(c) for c in cases])

    @router.post("/payments/{prospect_id}/confirm", response_model=PaymentCaseResponse)
    def confirm_payment(
        prospect_id: str,
        body: ConfirmRequest,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> PaymentCaseResponse:
        amount = _amount(body.amount)
        # The same payment confirmed twice is one confirmation: the key is its own terms.
        key = hashlib.sha256(
            f"{prospect_id}|{body.reference}|{amount}|{body.currency}".encode()
        ).hexdigest()[:40]
        try:
            confirmation = PaymentConfirmation(
                client_id=client.client_id,
                prospect_id=prospect_id,
                confirmation_id=f"web-{key}",
                actor=ConfirmingActor(
                    actor_id=f"client:{client.client_id}",
                    display_name=client.login,
                    permissions=frozenset({CONFIRM_PAYMENTS}),
                ),
                confirmed_at=application.clock.now(),
                payment_reference=body.reference,
                amount=amount,
                currency=body.currency,
            )
        except ValueError:
            raise HTTPException(422, "these payment terms are not valid") from None

        def work(engine: Engine) -> PaymentCase | None:
            receipt = desk(engine, client.client_id, lambda d: d.confirm(confirmation))
            if receipt.status is PaymentConfirmationStatus.REJECTED:
                if receipt.reason == "unknown_prospect":
                    raise HTTPException(404, "no such prospect")
                raise HTTPException(409, "this payment cannot be confirmed with these terms")
            return desk(engine, client.client_id, lambda d: d.case_of(prospect_id))

        case = run(client, response, work)
        if case is None:
            raise HTTPException(503, UNAVAILABLE)
        return _response(case)

    return router
