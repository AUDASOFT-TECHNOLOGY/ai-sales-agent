"""Client API: what the agent can draw on to sell, and drafts of lessons found in it.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie. Every answer is `Cache-Control: no-store`.

- `GET /client/v1/training/material` one line per product of the caller's catalog: how many documents,
  links, testimonials and notes the agent can really read, how many it cannot (a video, a photo, a scan,
  an unreachable page) and why. Nothing is counted as read unless text was really extracted;
- `GET /client/v1/training/suggestions/{product}` draft lessons (frequent questions with their answer,
  testimonials) found in the readable documents of that product. The client reviews them: a suggestion
  becomes a lesson only when the client adds it.
"""

from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_resources import _product_ids
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.config_store import ConfigStoreError
from sales_app.resources.models import ResourceStatus
from sales_app.resources.service import ResourceService
from sales_app.resources.suggest import suggest
from sales_app.sourcing.errors import StoreUnavailableError

UNAVAILABLE = "product documents are temporarily unavailable"


class UnreadResource(BaseModel):
    id: str
    title: str
    category: str
    reason: str | None  # the fixed reason code the interface words (video, image, scanned_pdf, ...)


class ProductMaterial(BaseModel):
    product_id: str
    total: int
    readable: int
    readable_chars: int
    testimonials: int  # testimonials the agent can read
    by_category: dict[str, int]
    unread: list[UnreadResource]


class MaterialResponse(BaseModel):
    products: list[ProductMaterial]


class SuggestionResponse(BaseModel):
    kind: Literal["faq", "proof"]
    text: str
    when: str | None
    source: str
    attribution: str | None


class SuggestionsResponse(BaseModel):
    suggestions: list[SuggestionResponse]
    left_out: int


def create_client_training_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1/training", tags=["client"])

    def service() -> ResourceService:
        found = application.product_resources
        if found is None:
            raise HTTPException(503, "product documents are not available on this platform")
        return found

    def catalog_of(client: ClientProfile) -> set[str]:
        try:
            found = client_wiring_and_document(application, client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        return set() if found is None else _product_ids(found[1])

    @router.get("/material", response_model=MaterialResponse)
    def material(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> MaterialResponse:
        response.headers["Cache-Control"] = "no-store"
        docs = service()
        products = sorted(catalog_of(client))
        out: list[ProductMaterial] = []
        try:
            for product_id in products:
                found = docs.for_product(client.client_id, product_id)
                ready = [r for r in found if r.status is ResourceStatus.READY]
                out.append(
                    ProductMaterial(
                        product_id=product_id,
                        total=len(found),
                        readable=len(ready),
                        readable_chars=sum(r.text_chars for r in ready),
                        testimonials=sum(1 for r in ready if r.category == "testimonial"),
                        by_category=dict(Counter(r.category for r in found)),
                        unread=[
                            UnreadResource(
                                id=r.id, title=r.title, category=r.category, reason=r.status_detail
                            )
                            for r in found
                            if r.status is not ResourceStatus.READY
                        ],
                    )
                )
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        return MaterialResponse(products=out)

    @router.get("/suggestions/{product_id}", response_model=SuggestionsResponse)
    async def suggestions(
        product_id: str, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> SuggestionsResponse:
        response.headers["Cache-Control"] = "no-store"
        docs = service()
        if product_id not in catalog_of(client):
            raise HTTPException(404, "no such product")

        def work() -> SuggestionsResponse:
            try:
                resources = [
                    r
                    for r in docs.for_product(client.client_id, product_id)
                    if r.status is ResourceStatus.READY
                ]
                chunks = docs.chunks_of(client.client_id, product_id)
            except StoreUnavailableError:
                raise HTTPException(503, UNAVAILABLE) from None
            found = suggest(resources, chunks)
            return SuggestionsResponse(
                suggestions=[
                    SuggestionResponse(
                        kind=s.kind,  # type: ignore[arg-type]
                        text=s.text,
                        when=s.when,
                        source=s.source,
                        attribution=s.attribution,
                    )
                    for s in found.items
                ],
                left_out=found.left_out,
            )

        return await run_in_threadpool(work)

    return router
