"""What a client keeps about a product beyond its settings: documents and links.

A resource belongs to one client and one product. It is either a *link* (a public web address the
client gave) or a *file* (uploaded and stored). Whether the agent can use it is a fact about what the
application really managed to read, never a promise:

- ``ready``        text was extracted and is searchable: the agent may draw on it;
- ``stored_only``  kept for the client, but nothing is read from it (a video, an image, a page that
                   is not text): the agent does not know its content;
- ``failed``       reading was attempted and did not work (an unreachable page, a damaged file).

``status_detail`` says why, as a fixed code the interface words for the client.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ResourceKind(StrEnum):
    LINK = "link"
    FILE = "file"


class ResourceStatus(StrEnum):
    READY = "ready"
    STORED_ONLY = "stored_only"
    FAILED = "failed"


# Fixed reasons a resource is not ready (the interface has a sentence for each).
DETAIL_VIDEO = "video"
DETAIL_IMAGE = "image"
DETAIL_PAGE_NOT_TEXT = "page_not_text"
DETAIL_SCANNED_PDF = "scanned_pdf"
DETAIL_EMPTY = "empty"
DETAIL_FETCH_FAILED = "fetch_failed"
DETAIL_UNSAFE_ADDRESS = "unsafe_address"
DETAIL_PARSE_FAILED = "parse_failed"

CATEGORIES = ("web", "pdf", "spreadsheet", "document", "image", "video", "testimonial", "other")
LINK_CATEGORIES = ("web", "video", "testimonial", "other")


@dataclass(frozen=True, slots=True, kw_only=True)
class Resource:
    id: str
    client_id: str
    product_id: str
    kind: ResourceKind
    category: str
    title: str
    url: str | None
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    status: ResourceStatus
    status_detail: str | None
    text_chars: int
    created_at: datetime
    created_by: str


@dataclass(frozen=True, slots=True)
class Extraction:
    """The outcome of reading a file or a page."""

    status: ResourceStatus
    text: str = ""
    detail: str | None = None

    @classmethod
    def ready(cls, text: str) -> "Extraction":
        return cls(ResourceStatus.READY, text)

    @classmethod
    def stored(cls, detail: str) -> "Extraction":
        return cls(ResourceStatus.STORED_ONLY, "", detail)

    @classmethod
    def failed(cls, detail: str) -> "Extraction":
        return cls(ResourceStatus.FAILED, "", detail)


@dataclass(frozen=True, slots=True)
class Chunk:
    """A searchable passage of one resource."""

    product_id: str
    resource_id: str
    title: str
    text: str
