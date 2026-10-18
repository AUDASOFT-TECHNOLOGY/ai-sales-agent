"""Adding, reading and removing the documents and links of a client's product.

This is the one place that decides what may be stored and what is said about it:

- limits: a file is at most `MAX_FILE_BYTES`, a product holds at most `MAX_PER_PRODUCT` resources and
  a client `MAX_PER_CLIENT`; only the types in `ACCEPTED_EXTENSIONS` are taken, and the bytes must be
  what the extension claims (a renamed file is refused, never read);
- links: ``https`` only, fetched through the safe fetcher (no private network); a known video site is
  stored without being fetched;
- reading: text is extracted when it really can be, and the resource says plainly when it could not
  (``stored_only`` / ``failed`` and a fixed reason), so nothing is presented as exploited when it is
  not;
- duplicates: the same file (same content) or the same address on one product is refused.

Errors are fixed sentences; they never repeat what the client sent.
"""

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from sales_app.resources.extract import (
    ACCEPTED_EXTENSIONS,
    FILE_TYPES,
    MAX_FILE_BYTES,
    chunk_text,
    extension_of,
    extract_file,
    extract_pdf,
    matches_type,
    read_html,
)
from sales_app.resources.fetch import (
    FetchFailedError,
    Page,
    UnsafeAddressError,
    fetch_page,
    is_video_host,
)
from sales_app.resources.models import (
    CATEGORIES,
    DETAIL_FETCH_FAILED,
    DETAIL_UNSAFE_ADDRESS,
    DETAIL_VIDEO,
    LINK_CATEGORIES,
    Extraction,
    Resource,
    ResourceKind,
    ResourceStatus,
)
from sales_app.resources.store import ResourceStore

MAX_PER_PRODUCT = 30
MAX_PER_CLIENT = 300
MAX_TITLE = 200
MAX_URL = 2048
_HTTPS = re.compile(r"^https://[^\s/$.?#][^\s]*$")


class ResourceError(Exception):
    """A refusal, with a fixed sentence and the HTTP status that suits it."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True, slots=True)
class Limits:
    max_file_bytes: int = MAX_FILE_BYTES
    max_per_product: int = MAX_PER_PRODUCT
    accepted_extensions: tuple[str, ...] = ACCEPTED_EXTENSIONS


def _title(text: str, fallback: str) -> str:
    cleaned = " ".join("".join(c for c in text if c.isprintable() or c == " ").split())[:MAX_TITLE]
    return cleaned or fallback[:MAX_TITLE]


def _read_page(page: Page) -> Extraction:
    return (
        extract_pdf(page.body)
        if page.content_type == "application/pdf"
        else read_html(page.body, page.full_content_type)
    )


class ResourceService:
    def __init__(
        self,
        store: ResourceStore,
        *,
        clock: Callable[[], datetime],
        fetch: Callable[[str], Page] = fetch_page,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
        limits: Limits | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._fetch = fetch
        self._new_id = new_id
        self.limits = limits or Limits()

    def __repr__(self) -> str:
        return "ResourceService()"

    # ---- reading what is kept ------------------------------------------------------------------

    def for_product(self, client_id: str, product_id: str) -> list[Resource]:
        return self._store.for_product(client_id, product_id)

    def download(self, client_id: str, product_id: str, resource_id: str) -> tuple[Resource, bytes]:
        resource = self._owned(client_id, product_id, resource_id)
        content = self._store.content(client_id, resource_id)
        if resource.kind is not ResourceKind.FILE or content is None:
            raise ResourceError(404, "no such file")
        return resource, content

    def delete(self, client_id: str, product_id: str, resource_id: str) -> None:
        self._owned(client_id, product_id, resource_id)
        self._store.delete(client_id, resource_id)

    # ---- adding ----------------------------------------------------------------------------------

    def add_link(
        self, client_id: str, product_id: str, *, title: str, url: str, category: str, by: str
    ) -> Resource:
        address = url.strip()
        if len(address) > MAX_URL or not _HTTPS.match(address):
            raise ResourceError(422, "the link must be a web address starting with https://")
        if category not in LINK_CATEGORIES:
            raise ResourceError(422, "this kind of link is not accepted")
        self._room_for_one(client_id, product_id)
        normalized = address.rstrip("/")
        if any(
            (r.url or "").rstrip("/") == normalized
            for r in self._store.for_product(client_id, product_id)
        ):
            raise ResourceError(409, "this link is already in the list")
        resource = Resource(
            id=self._new_id(),
            client_id=client_id,
            product_id=product_id,
            kind=ResourceKind.LINK,
            category=category,
            title=_title(title, urlsplit(address).hostname or "link"),
            url=address,
            filename=None,
            mime_type=None,
            size_bytes=None,
            sha256=None,
            status=ResourceStatus.STORED_ONLY,
            status_detail=None,
            text_chars=0,
            created_at=self._clock(),
            created_by=by,
        )
        extraction = self._read_link(address)
        chunks = chunk_text(extraction.text) if extraction.status is ResourceStatus.READY else []
        resource = self._with_reading(resource, extraction, chunks)
        self._store.add(resource, content=None, chunks=chunks)
        return resource

    def add_file(
        self,
        client_id: str,
        product_id: str,
        *,
        filename: str,
        title: str,
        content: bytes,
        by: str,
    ) -> Resource:
        extension = extension_of(filename)
        if extension not in self.limits.accepted_extensions:
            raise ResourceError(415, "this type of file is not accepted")
        if not content:
            raise ResourceError(422, "the file is empty")
        if len(content) > self.limits.max_file_bytes:
            raise ResourceError(413, "the file is too large")
        if not matches_type(extension, content):
            raise ResourceError(422, "the content of the file does not match its type")
        self._room_for_one(client_id, product_id)
        digest = hashlib.sha256(content).hexdigest()
        if any(r.sha256 == digest for r in self._store.for_product(client_id, product_id)):
            raise ResourceError(409, "this file is already in the list")
        extraction = extract_file(extension, content)
        chunks = chunk_text(extraction.text) if extraction.status is ResourceStatus.READY else []
        category = FILE_TYPES[extension]
        assert category in CATEGORIES
        resource = Resource(
            id=self._new_id(),
            client_id=client_id,
            product_id=product_id,
            kind=ResourceKind.FILE,
            category=category,
            title=_title(title, filename),
            url=None,
            filename=_title(filename, "file"),
            mime_type=None,
            size_bytes=len(content),
            sha256=digest,
            status=ResourceStatus.STORED_ONLY,
            status_detail=None,
            text_chars=0,
            created_at=self._clock(),
            created_by=by,
        )
        resource = self._with_reading(resource, extraction, chunks)
        self._store.add(resource, content=content, chunks=chunks)
        return resource

    def retry(self, client_id: str, product_id: str, resource_id: str) -> Resource:
        """Reads a link again (a page that was down). Files are read once, when they are added."""
        resource = self._owned(client_id, product_id, resource_id)
        if resource.kind is not ResourceKind.LINK or resource.url is None:
            raise ResourceError(422, "only a link can be read again")
        extraction = self._read_link(resource.url)
        chunks = chunk_text(extraction.text) if extraction.status is ResourceStatus.READY else []
        updated = self._store.update_reading(
            client_id,
            resource_id,
            status=extraction.status,
            status_detail=extraction.detail,
            chunks=chunks,
        )
        if updated is None:
            raise ResourceError(404, "no such resource")
        return updated

    # ---- helpers ---------------------------------------------------------------------------------

    def _owned(self, client_id: str, product_id: str, resource_id: str) -> Resource:
        resource = self._store.get(client_id, resource_id)
        if resource is None or resource.product_id != product_id:
            raise ResourceError(404, "no such resource")
        return resource

    def _room_for_one(self, client_id: str, product_id: str) -> None:
        if self._store.count(client_id, product_id) >= self.limits.max_per_product:
            raise ResourceError(422, "this product already has the maximum number of documents")
        if self._store.count(client_id) >= MAX_PER_CLIENT:
            raise ResourceError(422, "your account already has the maximum number of documents")

    def _read_link(self, url: str) -> Extraction:
        if is_video_host(url):
            return Extraction.stored(DETAIL_VIDEO)
        try:
            return _read_page(self._fetch(url))
        except UnsafeAddressError:
            return Extraction.failed(DETAIL_UNSAFE_ADDRESS)
        except FetchFailedError:
            return Extraction.failed(DETAIL_FETCH_FAILED)

    @staticmethod
    def _with_reading(resource: Resource, extraction: Extraction, chunks: list[str]) -> Resource:
        return replace(
            resource,
            status=extraction.status,
            status_detail=extraction.detail,
            text_chars=sum(len(c) for c in chunks),
        )
