"""The HubSpot provider: prospects kept in a client's own HubSpot account (a source only, read-only).

It builds a `ProspectSource`, not a repository: HubSpot knows who the people are and nothing of what
Revansa does with them, so the runtime composes it with the client's operational store and the
consent registry (`sales_app.wiring.runtime`). It never writes to HubSpot.

One credential, the client's own: its Private App access token. It is a secret of the client's
scope, never shown again, and no two clients may hold the same one (that would be one HubSpot
account serving two clients). It is read here, to build the connector, and nowhere else that runs an
agent; it never reaches a log, an error or a response.

The operator decides what the CRM's words mean (`status_mapping`): there is no default, so a client
with no mapping does not run rather than run on a guess.
"""

import re
from collections.abc import Sequence
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from sales_app.config import ClientConfig
from sales_app.config.secrets import HUBSPOT_ACCESS_TOKEN
from sales_app.secret_store import ScopeKind
from sales_app.sourcing.contacts import normalize_phone
from sales_app.sourcing.crm_source import CrmProspectSource, CrmRecord
from sales_app.sourcing.errors import (
    SourceAuthError,
    SourceDataError,
    SourceError,
    SourceRateLimitedError,
    SourceUnavailableError,
)
from sales_app.sourcing.statuses import StatusMapping
from sales_app.wiring.providers.types import (
    CredentialDeclaration,
    Product,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
    UniquenessRule,
)
from sales_integrations.hubspot import (
    HubSpotAuthError,
    HubSpotClient,
    HubSpotContact,
    HubSpotDataError,
    HubSpotError,
    HubSpotRateLimitedError,
    HubSpotUnavailableError,
)

KEY = "hubspot"

# What HubSpot issues for a Private App: `pat-<region>-<uuid>` (for instance `pat-eu1-...`).
TOKEN_PATTERN = re.compile(
    r"^pat-[a-z]{2,4}\d{1,2}-[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"
)
TOKEN_RULE = "must be a HubSpot Private App access token (it looks like pat-eu1-...)"

PropertyName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,99}$")]
_NAME_PROPERTIES = ("firstname", "lastname")
_PHONE_PROPERTIES = ("mobilephone", "phone")  # a mobile first: the channel is a messaging one
_PHONE = re.compile(r"\+?[0-9]{6,15}")
_FALSE_WORDS = frozenset({"", "false", "0", "no"})


class HubSpotSourceSettings(BaseModel):
    """What the operator decides about one client's HubSpot account. No secret is among them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # The contact property that holds the CRM's status, and what each of its values means to
    # Revansa (`new`, `paused`, `closed` or `opted_out`). A value that is not listed is not
    # understood, and a contact carrying it is left out.
    status_property: PropertyName = "hs_lead_status"
    status_mapping: dict[str, str] = Field(min_length=1)
    # A contact whose value here is anything but false/empty must never be contacted.
    do_not_contact_property: PropertyName = "hs_email_optout"
    locale_property: PropertyName | None = None

    @field_validator("status_mapping")
    @classmethod
    def _understood(cls, value: dict[str, str]) -> dict[str, str]:
        StatusMapping.from_config(value)  # raises a ValueError that words what is wrong
        return value


class HubSpotReader:
    """The `CrmReader` of one HubSpot account: HubSpot's contacts as `CrmRecord`s, and HubSpot's
    failures as the application's `SourceError`s (fixed sentences, never what HubSpot said)."""

    def __init__(self, client: HubSpotClient, settings: HubSpotSourceSettings) -> None:
        self._client = client
        self._settings = settings
        self._properties: tuple[str, ...] = tuple(
            dict.fromkeys(
                (
                    *_NAME_PROPERTIES,
                    "email",
                    *_PHONE_PROPERTIES,
                    settings.status_property,
                    settings.do_not_contact_property,
                    *((settings.locale_property,) if settings.locale_property else ()),
                )
            )
        )

    def __repr__(self) -> str:
        return "HubSpotReader()"

    def list_records(self) -> Sequence[CrmRecord]:
        contacts = _translated(lambda: self._client.list_contacts(self._properties))
        return [self._record(c) for c in contacts]

    def get_record(self, native_id: str) -> CrmRecord | None:
        contact = _translated(lambda: self._client.get_contact(native_id, self._properties))
        return None if contact is None else self._record(contact)

    def find_records(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Sequence[CrmRecord]:
        found: dict[str, CrmRecord] = {}
        if email and email.strip():
            for contact in _translated(lambda: self._client.find_by_email(email, self._properties)):
                found.setdefault(contact.id, self._record(contact))
        digits = normalize_phone(phone)
        if digits and not found:
            # HubSpot cannot search a number whatever way it was typed ("+216 99 888 777",
            # "(216) 99-888-777"), so the comparison is made here, on every contact.
            for record in self.list_records():
                if normalize_phone(record.phone) == digits:
                    found.setdefault(record.native_id, record)
        return list(found.values())

    def verify(self) -> list[str]:
        try:
            _translated(self._client.verify)
        except SourceError as error:
            return [str(error)]
        return []

    def _record(self, contact: HubSpotContact) -> CrmRecord:
        values = contact.properties
        name = " ".join(
            part for part in (_text(values.get(p)) for p in _NAME_PROPERTIES) if part is not None
        )
        locale = self._settings.locale_property
        return CrmRecord(
            native_id=contact.id,
            name=name or None,
            phone=next(
                (n for n in (_phone(values.get(p)) for p in _PHONE_PROPERTIES) if n is not None),
                None,
            ),
            email=_text(values.get("email")),
            status=values.get(self._settings.status_property),
            do_not_contact=_refuses(values.get(self._settings.do_not_contact_property)),
            locale=_text(values.get(locale)) if locale else None,
        )


def _translated[T](call) -> T:
    try:
        return call()
    except HubSpotAuthError:
        raise SourceAuthError(
            "HubSpot refused the access token, or it may not read contacts"
        ) from None
    except HubSpotRateLimitedError as error:
        raise SourceRateLimitedError(
            "HubSpot's rate limit was reached", retry_after=error.retry_after
        ) from None
    except HubSpotUnavailableError:
        raise SourceUnavailableError(
            "HubSpot could not be reached or answered with an error"
        ) from None
    except HubSpotDataError:
        raise SourceDataError("HubSpot did not answer as its API documents") from None
    except HubSpotError:
        raise SourceUnavailableError("HubSpot failed") from None


def _text(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def _phone(value: str | None) -> str | None:
    """A number the Core may use: digits and an optional leading `+`, or None (a free-text value
    such as "call after 5" is not a number and is never guessed at)."""
    text = _text(value)
    if text is None:
        return None
    compact = re.sub(r"[\s().-]", "", text)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    return compact if _PHONE.fullmatch(compact) else None


def _refuses(value: str | None) -> bool:
    """Fail closed: anything but an empty value or a plain "false" is a refusal."""
    return value is not None and value.strip().lower() not in _FALSE_WORDS


def build_source(config: ClientConfig) -> CrmProspectSource:
    """This client's HubSpot source, from its own token and its operator's settings. Raises
    `ValueError` (a fixed sentence, never a value) when either is missing or not acceptable."""
    try:
        settings = HubSpotSourceSettings.model_validate(dict(config.spec.source_settings))
    except ValidationError as error:
        problems = "; ".join(
            ".".join(str(part) for part in problem["loc"]) + f": {problem['msg']}"
            for problem in error.errors()
        )
        raise ValueError(f"source.settings: {problems}") from None
    if HUBSPOT_ACCESS_TOKEN not in config.secrets:
        raise ValueError("the HubSpot access token is not configured")
    return CrmProspectSource(
        HubSpotReader(HubSpotClient(config.secrets.get(HUBSPOT_ACCESS_TOKEN)), settings),
        client_id=config.client_id,
        mapping=StatusMapping.from_config(settings.status_mapping),
        namespace=KEY,
    )


DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.SOURCE,
    label="HubSpot",
    roles=frozenset({ProviderKind.SOURCE}),
    settings_models={ProviderKind.SOURCE: HubSpotSourceSettings},
    credentials=(
        CredentialDeclaration(
            key=HUBSPOT_ACCESS_TOKEN,
            label="HubSpot access token",
            scope=ScopeKind.CLIENT,
            required_when=Requirement.ALWAYS,
            unique=True,
            pattern=TOKEN_PATTERN,
            rule=TOKEN_RULE,
        ),
    ),
    connection_keys=(HUBSPOT_ACCESS_TOKEN,),
    capabilities={"can_write_back": False, "can_host_store": False},
    unique_across_clients=(UniquenessRule("credential", HUBSPOT_ACCESS_TOKEN, "HubSpot token"),),
    build=build_source,
    product=Product.PROSPECT_SOURCE,
)
