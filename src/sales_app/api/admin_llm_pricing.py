"""Admin API for LLM prices and what a client's model calls cost.

The price list is global and belongs to Revansa: any admin may read it, only an owner changes it.
The usage a cost is computed from is one client's own register, so reading it goes through the same
client access rule as every other route that takes a `client_id` (a client the admin cannot reach is
a 404). Costs come from tokens that were really recorded and prices that are really configured; where
either is missing the cost is `null` (unknown), never 0.

Amounts travel as decimal strings, never floats.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import (
    ensure_client_access,
    make_current_admin_dependency,
    require_role,
)
from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.bootstrap import Application
from sales_app.llm_pricing import (
    InvalidPriceError,
    LLMPrice,
    LLMPricingStore,
    summarise_costs,
    totals_by_currency,
    validate_price,
)
from sales_app.sourcing.errors import StoreUnavailableError


class PriceBody(BaseModel):
    provider: str = Field(max_length=64)
    model: str = Field(max_length=128)
    input_per_million: Decimal = Field(description="Price of one million input tokens")
    output_per_million: Decimal = Field(description="Price of one million output tokens")
    currency: str = Field(max_length=3)


class PriceResponse(BaseModel):
    provider: str
    model: str
    input_per_million: str
    output_per_million: str
    currency: str
    updated_at: datetime
    updated_by: str


class PricesResponse(BaseModel):
    prices: list[PriceResponse]


class ModelCostResponse(BaseModel):
    provider: str
    model: str
    calls: int
    costed_calls: int
    unknown_calls: int  # calls whose cost cannot be told (no token count, or no price)
    input_tokens: int  # recorded tokens only
    output_tokens: int
    cost: str | None  # null: unknown, which is not 0
    currency: str | None


class UsageCostResponse(BaseModel):
    client_id: str
    models: list[ModelCostResponse]
    totals: dict[str, str]  # known costs per currency; a currency with no known cost is absent
    complete: bool  # every call is costed: false means the totals are a lower bound


def _price_response(price: LLMPrice) -> PriceResponse:
    return PriceResponse(
        provider=price.provider,
        model=price.model,
        input_per_million=format(price.input_per_million, "f"),
        output_per_million=format(price.output_per_million, "f"),
        currency=price.currency,
        updated_at=price.updated_at,
        updated_by=price.updated_by,
    )


def create_admin_llm_pricing_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def pricing() -> LLMPricingStore:
        if application.llm_pricing is None:
            raise HTTPException(503, "prices need the database (set SALES_AGENT_DATABASE_URL)")
        return application.llm_pricing

    def unavailable() -> HTTPException:
        return HTTPException(503, "the LLM price list is unavailable")

    @router.get("/settings/llm/pricing", response_model=PricesResponse)
    def list_prices(_admin: AdminUser = Depends(get_current_admin)) -> PricesResponse:
        try:
            prices = pricing().list_prices()
        except StoreUnavailableError:
            raise unavailable() from None
        return PricesResponse(prices=[_price_response(p) for p in prices])

    @router.put("/settings/llm/pricing", response_model=PriceResponse)
    async def set_price(
        body: PriceBody, admin: AdminUser = Depends(require_owner)
    ) -> PriceResponse:
        try:
            price = validate_price(
                body.provider,
                body.model,
                body.input_per_million,
                body.output_per_million,
                body.currency,
                updated_at=application.clock.now(),
                updated_by=admin.username,
            )
        except InvalidPriceError as error:
            raise HTTPException(422, str(error)) from None
        store = pricing()
        try:
            await run_in_threadpool(store.set_price, price)
        except StoreUnavailableError:
            raise unavailable() from None
        return _price_response(price)

    @router.delete("/settings/llm/pricing/{provider}/{model_id}", response_model=PricesResponse)
    async def delete_price(
        provider: str, model_id: str, _admin: AdminUser = Depends(require_owner)
    ) -> PricesResponse:
        store = pricing()
        try:
            if not await run_in_threadpool(store.delete_price, provider, model_id):
                raise HTTPException(404, "no such price")
            prices = await run_in_threadpool(store.list_prices)
        except StoreUnavailableError:
            raise unavailable() from None
        return PricesResponse(prices=[_price_response(p) for p in prices])

    @router.get("/clients/{client_id}/llm/usage", response_model=UsageCostResponse)
    def client_usage(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> UsageCostResponse:
        ensure_client_access(admin, client_id)
        if client_wiring_and_document(application, client_id) is None:
            raise HTTPException(404, "no such client")
        open_usage = application.builders.llm_usage if application.builders is not None else None
        if open_usage is None:
            raise HTTPException(503, "usage needs the database (set SALES_AGENT_DATABASE_URL)")
        try:
            records = open_usage(client_id).list_usage()
            # Without a price list every cost is unknown: the usage is still shown.
            prices = application.llm_pricing.list_prices() if application.llm_pricing else []
        except StoreUnavailableError:
            raise HTTPException(503, "the LLM usage register is unavailable") from None
        lines = summarise_costs(records, prices)
        return UsageCostResponse(
            client_id=client_id,
            models=[
                ModelCostResponse(
                    provider=line.provider,
                    model=line.model,
                    calls=line.calls,
                    costed_calls=line.costed_calls,
                    unknown_calls=line.unknown_calls,
                    input_tokens=line.input_tokens,
                    output_tokens=line.output_tokens,
                    cost=None if line.cost is None else format(line.cost, "f"),
                    currency=line.currency,
                )
                for line in lines
            ],
            totals={c: format(v, "f") for c, v in totals_by_currency(lines).items()},
            complete=all(line.unknown_calls == 0 for line in lines),
        )

    return router
