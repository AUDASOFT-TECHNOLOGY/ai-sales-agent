"""An HTTP client for the contacts of one HubSpot account (a Private App token).

It is bound to one token, so to one client's account. It reads, and it has exactly one way to
write: `update_contact`, a `PATCH` of the properties of one contact, **bounded to the property
names given at construction** (`writable_properties`, empty by default, so a client built without
them cannot write at all) and never to an identity property (`NEVER_WRITTEN`), whatever it is
given. Nothing creates or deletes a contact, a property or anything else. The `POST` of the
contact search changes nothing.

It never returns a partial list: a page that fails fails the whole read. The token travels only in
the `Authorization` header; it is not in a URL, a `repr`, an error message or a log (nothing here
logs). A failure is a fixed sentence from `errors`, never what HubSpot or the HTTP library said.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from sales_integrations.hubspot.errors import (
    HubSpotAuthError,
    HubSpotDataError,
    HubSpotNotFoundError,
    HubSpotRateLimitedError,
    HubSpotUnavailableError,
)

BASE_URL = "https://api.hubapi.com"
CONTACTS_PATH = "/crm/v3/objects/contacts"
PROPERTIES_PATH = "/crm/v3/properties/contacts"
SEARCH_PATH = f"{CONTACTS_PATH}/search"
DEFAULT_TIMEOUT_SECONDS = 15.0
PAGE_SIZE = 100  # the most a listing page may hold
MAX_PAGES = 2000  # a runaway cursor is an error, not an endless read
MAX_RETRY_AFTER_SECONDS = 3600.0

_CONTACT_ID = re.compile(r"[0-9]{1,20}")
_PROPERTY_NAME = re.compile(r"[a-z][a-z0-9_]{0,99}")

# What a person is, and what says whether they may be contacted: never written, however the client
# was built.
NEVER_WRITTEN = frozenset(
    {
        "email",
        "firstname",
        "lastname",
        "phone",
        "mobilephone",
        "hubspot_owner_id",
        "hs_email_optout",
        "hs_object_id",
    }
)
_READ_MESSAGE = "HubSpot refused the access token, or it may not read contacts"
_WRITE_MESSAGE = "HubSpot refused the access token, or it may not change contacts"
_SCHEMA_MESSAGE = "HubSpot refused the access token, or it may not read the contact properties"

HttpClientFactory = Callable[[], httpx.Client]


@dataclass(frozen=True, slots=True, repr=False)
class HubSpotContact:
    """One contact as HubSpot holds it: its id and the properties that were asked for (text or
    None). It is personal data, so its `repr` shows nothing but the id."""

    id: str
    properties: Mapping[str, str | None]

    def __repr__(self) -> str:
        return f"HubSpotContact(id={self.id!r})"


@dataclass(frozen=True, slots=True)
class HubSpotProperty:
    """The definition of one contact property: what a write would have to respect. No value."""

    name: str
    type: str  # "string", "number", "datetime", "enumeration", "bool", "date"...
    options: tuple[str, ...]  # the values an enumeration accepts (internal values), else empty
    read_only: bool  # HubSpot computes it, or it cannot be set


class HubSpotClient:
    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        http: HttpClientFactory | None = None,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        writable_properties: Sequence[str] = (),
    ) -> None:
        if not access_token or not access_token.strip():
            raise ValueError("a HubSpot access token is required")
        if not 1 <= page_size <= PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {PAGE_SIZE}")
        self._headers = {"Authorization": f"Bearer {access_token.strip()}"}
        self._base_url = base_url.rstrip("/")
        self._http = http or (lambda: httpx.Client(timeout=timeout_seconds))
        self._page_size = page_size
        self._max_pages = max_pages
        writable = frozenset(writable_properties)
        if any(not _PROPERTY_NAME.fullmatch(name) for name in writable):
            raise ValueError("a writable property must be a HubSpot internal property name")
        if writable & NEVER_WRITTEN:
            raise ValueError("this property can never be written")
        self._writable = writable

    def __repr__(self) -> str:
        return "HubSpotClient()"

    # ---- reading -------------------------------------------------------------------------

    def list_contacts(self, properties: Sequence[str]) -> list[HubSpotContact]:
        """Every contact (archived ones are not returned), page after page. All of it or an error."""
        found: list[HubSpotContact] = []
        after: str | None = None
        for _ in range(self._max_pages):
            params: dict[str, Any] = {"limit": self._page_size, "properties": ",".join(properties)}
            if after is not None:
                params["after"] = after
            body = self._request("GET", CONTACTS_PATH, params=params)
            found.extend(_contacts(body.get("results")))
            following = _next_cursor(body)
            if following is None:
                return found
            if following == after:  # a cursor that does not move would never end
                raise HubSpotDataError("HubSpot's contact list did not end")
            after = following
        raise HubSpotDataError("HubSpot's contact list did not end")

    def get_contact(self, contact_id: str, properties: Sequence[str]) -> HubSpotContact | None:
        """The contact with this id, or None if there is none (or the id is not one of HubSpot's)."""
        if not _CONTACT_ID.fullmatch(contact_id):
            return None
        body = self._request(
            "GET",
            f"{CONTACTS_PATH}/{contact_id}",
            params={"properties": ",".join(properties)},
            missing_ok=True,
        )
        if body is None:
            return None
        found = _contacts([body])
        return found[0] if found else None

    def find_by_email(self, email: str, properties: Sequence[str]) -> list[HubSpotContact]:
        """The contacts whose e-mail address is this one (HubSpot compares them without case)."""
        body = self._request(
            "POST",
            SEARCH_PATH,
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "email", "operator": "EQ", "value": email.strip()}
                        ]
                    }
                ],
                "properties": list(properties),
                "limit": 100,
            },
        )
        return _contacts(body.get("results"))

    def get_property(self, name: str) -> HubSpotProperty | None:
        """The definition of a contact property, or None if there is no such property."""
        if not _PROPERTY_NAME.fullmatch(name):
            return None
        body = self._request(
            "GET", f"{PROPERTIES_PATH}/{name}", missing_ok=True, forbidden=_SCHEMA_MESSAGE
        )
        if body is None:
            return None
        kind, options = body.get("type"), body.get("options", [])
        if not isinstance(kind, str) or not isinstance(options, list):
            raise HubSpotDataError("HubSpot did not answer with the documented shape")
        values = tuple(
            o["value"]
            for o in options
            if isinstance(o, dict) and isinstance(o.get("value"), str) and not o.get("hidden")
        )
        meta = body.get("modificationMetadata")
        read_only = bool(body.get("calculated")) or (
            isinstance(meta, dict) and bool(meta.get("readOnlyValue"))
        )
        return HubSpotProperty(name, kind, values, read_only)

    # ---- writing: one contact's named properties, nothing else ------------------------------

    def update_contact(self, contact_id: str, values: Mapping[str, str]) -> None:
        """Set properties of one existing contact (text; the empty text clears one). Only the
        names given to the constructor are accepted; anything else raises `ValueError` before any
        request. Raises `HubSpotNotFoundError` for a contact that is not there."""
        if not _CONTACT_ID.fullmatch(contact_id):
            raise HubSpotNotFoundError("HubSpot has no such contact")
        if not values or not set(values) <= self._writable:
            raise ValueError("a property that was not declared writable cannot be written")
        if not all(isinstance(v, str) for v in values.values()):
            raise ValueError("a property is written as text")
        self._request(
            "PATCH",
            f"{CONTACTS_PATH}/{contact_id}",
            json={"properties": dict(values)},
            forbidden=_WRITE_MESSAGE,
            not_found=True,
        )

    def verify(self) -> None:
        """One contact id, read: succeeds only if the token is valid and may read contacts."""
        body = self._request(
            "GET", CONTACTS_PATH, params={"limit": 1, "properties": "hs_object_id"}
        )
        if not isinstance(body.get("results"), list):
            raise HubSpotDataError("HubSpot did not answer with a contact list")

    # ---- the one place a request is made ---------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        missing_ok: bool = False,
        forbidden: str = _READ_MESSAGE,
        not_found: bool = False,
    ) -> Any:
        try:
            with self._http() as client:
                response = client.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json,
                    headers=self._headers,
                )
        except httpx.HTTPError:  # a timeout, a refused connection...: never repeat what it said
            raise HubSpotUnavailableError("HubSpot could not be reached") from None
        status = response.status_code
        if status == 404 and missing_ok:
            return None
        if status == 404 and not_found:
            raise HubSpotNotFoundError("HubSpot has no such contact")
        if status in (401, 403):
            raise HubSpotAuthError(forbidden)
        if status == 429:
            raise HubSpotRateLimitedError(retry_after=_retry_after(response))
        if status >= 500:
            raise HubSpotUnavailableError("HubSpot answered with an error")
        if not response.is_success:
            raise HubSpotDataError("HubSpot refused the request")
        try:
            body = response.json()
        except ValueError:
            raise HubSpotDataError("HubSpot did not answer with JSON") from None
        if not isinstance(body, dict):
            raise HubSpotDataError("HubSpot did not answer with the documented shape")
        return body


def _retry_after(response: httpx.Response) -> float | None:
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return seconds if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS else None


def _next_cursor(body: Mapping[str, Any]) -> str | None:
    paging = body.get("paging")
    if paging is None:
        return None
    if not isinstance(paging, dict):
        raise HubSpotDataError("HubSpot did not answer with the documented shape")
    follow = paging.get("next")
    if follow is None:
        return None
    cursor = follow.get("after") if isinstance(follow, dict) else None
    if not isinstance(cursor, str) or not cursor:
        raise HubSpotDataError("HubSpot did not answer with the documented shape")
    return cursor


def _contacts(items: Any) -> list[HubSpotContact]:
    """The contacts of a `results` list. One malformed item fails the whole read: it is never
    guessed at or skipped."""
    if not isinstance(items, list):
        raise HubSpotDataError("HubSpot did not answer with the documented shape")
    found = []
    for item in items:
        if not isinstance(item, dict):
            raise HubSpotDataError("HubSpot did not answer with the documented shape")
        identifier = item.get("id")
        raw = item.get("properties")
        if isinstance(identifier, int) and not isinstance(identifier, bool):
            identifier = str(identifier)
        if not isinstance(identifier, str) or not identifier or not isinstance(raw, dict):
            raise HubSpotDataError("HubSpot did not answer with the documented shape")
        found.append(
            HubSpotContact(
                identifier,
                {
                    k: v if isinstance(v, str) else None
                    for k, v in raw.items()
                    if isinstance(k, str)
                },
            )
        )
    return found
