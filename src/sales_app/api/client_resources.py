"""Client API: the documents and links a client attaches to its products.

Like the other client routes, none takes a client id: who is asking comes only from the session
cookie, so no id sent by the browser can reach another client's resources. A product id in a path is
checked against the caller's own catalog; a resource id is looked up through the caller. Every answer
is `Cache-Control: no-store`.

- `GET    /client/v1/products/{product}/resources`            what is attached, what the agent can
  really read of each, and the limits;
- `POST   /client/v1/products/{product}/resources/link`       `{title, url, category}`: a public
  https page. It is opened by the platform through a fetcher that refuses private networks;
- `POST   /client/v1/products/{product}/resources/file`       the file as the raw request body
  (`X-File-Name`, `X-Resource-Title`, both URL-encoded): type, size and content are checked;
- `POST   /client/v1/products/{product}/resources/{id}/retry` reads a link again;
- `DELETE /client/v1/products/{product}/resources/{id}`;
- `GET    /client/v1/products/{product}/resources/{id}/download` the stored file, never rendered
  inline (`attachment`, `nosniff`).

What the agent can use is what was really extracted (`status: ready`). A video, an image or a page
without text is kept and says it is not read; nothing is ever presented as exploited when it is not.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StringConstraints

from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.config_store import ConfigStoreError
from sales_app.resources.models import Resource
from sales_app.resources.service import ResourceError, ResourceService
from sales_app.sourcing.errors import StoreUnavailableError

UNAVAILABLE = "product documents are temporarily unavailable"


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    category: Literal["web", "video", "testimonial", "other"] = "web"


class ResourceResponse(BaseModel):
    id: str
    product_id: str
    kind: str
    category: str
    title: str
    url: str | None
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    status: str  # ready | stored_only | failed
    status_detail: str | None
    text_chars: int
    created_at: datetime


class LimitsResponse(BaseModel):
    max_file_bytes: int
    max_per_product: int
    accepted_extensions: list[str]


class ResourcesResponse(BaseModel):
    resources: list[ResourceResponse]
    limits: LimitsResponse


class DeletedResponse(BaseModel):
    deleted: str


def _response(resource: Resource) -> ResourceResponse:
    return ResourceResponse(
        id=resource.id,
        product_id=resource.product_id,
        kind=resource.kind.value,
        category=resource.category,
        title=resource.title,
        url=resource.url,
        filename=resource.filename,
        mime_type=resource.mime_type,
        size_bytes=resource.size_bytes,
        status=resource.status.value,
        status_detail=resource.status_detail,
        text_chars=resource.text_chars,
        created_at=resource.created_at,
    )


def _product_ids(document: Mapping[str, Any]) -> set[str]:
    brain = document.get("brain")
    settings = brain.get("settings") if isinstance(brain, Mapping) else None
    catalog = settings.get("catalog") if isinstance(settings, Mapping) else None
    products = catalog.get("products") if isinstance(catalog, Mapping) else None
    if not isinstance(products, list | tuple):
        return set()
    return {
        str(p["id"]) for p in products if isinstance(p, Mapping) and isinstance(p.get("id"), str)
    }


def _header_text(request: Request, name: str, default: str = "") -> str:
    raw = request.headers.get(name)
    return unquote(raw)[:300] if raw else default


def create_client_resources_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1/products", tags=["client"])

    def service() -> ResourceService:
        found = application.product_resources
        if found is None:
            raise HTTPException(503, "product documents are not available on this platform")
        return found

    def own_product(client: ClientProfile, product_id: str) -> None:
        try:
            found = client_wiring_and_document(application, client.client_id)
        except ConfigStoreError:
            raise HTTPException(503, "settings are temporarily unavailable") from None
        if found is None or product_id not in _product_ids(found[1]):
            raise HTTPException(404, "no such product")

    def guarded(work):
        try:
            return work()
        except ResourceError as error:
            raise HTTPException(error.status, error.message) from None
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None

    @router.get("/{product_id}/resources", response_model=ResourcesResponse)
    def list_resources(
        product_id: str, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ResourcesResponse:
        response.headers["Cache-Control"] = "no-store"
        docs = service()
        own_product(client, product_id)
        found = guarded(lambda: docs.for_product(client.client_id, product_id))
        limits = docs.limits
        return ResourcesResponse(
            resources=[_response(r) for r in found],
            limits=LimitsResponse(
                max_file_bytes=limits.max_file_bytes,
                max_per_product=limits.max_per_product,
                accepted_extensions=list(limits.accepted_extensions),
            ),
        )

    @router.post("/{product_id}/resources/link", response_model=ResourceResponse, status_code=201)
    async def add_link(
        product_id: str,
        body: LinkRequest,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> ResourceResponse:
        response.headers["Cache-Control"] = "no-store"
        docs = service()
        own_product(client, product_id)
        added = await run_in_threadpool(
            lambda: guarded(
                lambda: docs.add_link(
                    client.client_id,
                    product_id,
                    title=body.title,
                    url=body.url,
                    category=body.category,
                    by=f"client:{client.client_id}",
                )
            )
        )
        return _response(added)

    @router.post("/{product_id}/resources/file", response_model=ResourceResponse, status_code=201)
    async def add_file(
        product_id: str,
        request: Request,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> ResourceResponse:
        response.headers["Cache-Control"] = "no-store"
        docs = service()
        own_product(client, product_id)
        limit = docs.limits.max_file_bytes
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "the file is too large")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                raise HTTPException(413, "the file is too large")
        filename = _header_text(request, "x-file-name")
        if not filename:
            raise HTTPException(422, "the file has no name")
        added = await run_in_threadpool(
            lambda: guarded(
                lambda: docs.add_file(
                    client.client_id,
                    product_id,
                    filename=filename,
                    title=_header_text(request, "x-resource-title", filename),
                    content=bytes(body),
                    by=f"client:{client.client_id}",
                )
            )
        )
        return _response(added)

    @router.post("/{product_id}/resources/{resource_id}/retry", response_model=ResourceResponse)
    async def retry(
        product_id: str,
        resource_id: str,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> ResourceResponse:
        response.headers["Cache-Control"] = "no-store"
        docs = service()
        own_product(client, product_id)
        updated = await run_in_threadpool(
            lambda: guarded(lambda: docs.retry(client.client_id, product_id, resource_id))
        )
        return _response(updated)

    @router.delete("/{product_id}/resources/{resource_id}", response_model=DeletedResponse)
    def delete(
        product_id: str,
        resource_id: str,
        response: Response,
        client: ClientProfile = Depends(get_current_client),
    ) -> DeletedResponse:
        response.headers["Cache-Control"] = "no-store"
        docs = service()
        # A product that left the catalog can still have its documents removed.
        guarded(lambda: docs.delete(client.client_id, product_id, resource_id))
        return DeletedResponse(deleted=resource_id)

    @router.get("/{product_id}/resources/{resource_id}/download")
    def download(
        product_id: str, resource_id: str, client: ClientProfile = Depends(get_current_client)
    ) -> Response:
        docs = service()
        resource, content = guarded(
            lambda: docs.download(client.client_id, product_id, resource_id)
        )
        name = resource.filename or "document"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    return router
