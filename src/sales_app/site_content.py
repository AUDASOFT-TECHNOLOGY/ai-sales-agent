"""What the Admin writes for visitors and for clients to read: the public Contact, Privacy Policy and
Terms of Service pages, and the bank details a client pays an offline plan into.

One text per (page, language): a title and a body of plain paragraphs (never HTML: it is shown as text).
A page with no text is not published, so the landing page links only to what exists. The bank details
are not public: only a signed-in client reads them.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

PUBLIC_PAGES = ("contact", "privacy", "terms")
BANK = "bank"
KEYS = (*PUBLIC_PAGES, BANK)
LOCALES = ("en", "fr", "ar")
TITLE_MAX = 120
BODY_MAX = 20_000


@dataclass(frozen=True, slots=True)
class SitePage:
    key: str
    locale: str
    title: str
    body: str
    updated_at: datetime
    updated_by: str


class SitePageStore(Protocol):
    def get(self, key: str, locale: str) -> SitePage | None: ...

    def list_all(self) -> list[SitePage]: ...

    def save(self, page: SitePage) -> None:
        """Replaces the text of that page in that language."""

    def remove(self, key: str, locale: str) -> None: ...


class InMemorySitePageStore:
    def __init__(self) -> None:
        self._pages: dict[tuple[str, str], SitePage] = {}
        self._lock = threading.Lock()

    def get(self, key: str, locale: str) -> SitePage | None:
        with self._lock:
            return self._pages.get((key, locale))

    def list_all(self) -> list[SitePage]:
        with self._lock:
            return sorted(self._pages.values(), key=lambda p: (p.key, p.locale))

    def save(self, page: SitePage) -> None:
        with self._lock:
            self._pages[(page.key, page.locale)] = page

    def remove(self, key: str, locale: str) -> None:
        with self._lock:
            self._pages.pop((key, locale), None)


def read_page(store: SitePageStore, key: str, locale: str) -> SitePage | None:
    """The page in the asked language, else in English, else in any language it exists in."""
    found = store.get(key, locale) or store.get(key, "en")
    if found is not None:
        return found
    return next((p for p in store.list_all() if p.key == key), None)
