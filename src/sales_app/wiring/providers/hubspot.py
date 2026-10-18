"""The HubSpot provider: prospects kept in a client's own HubSpot account (a source only).

It builds a `ProspectSource`, not a repository: HubSpot knows who the people are and nothing of what
Revansa does with them, so the runtime composes it with the client's operational store and the
consent registry (`sales_app.wiring.runtime`). It is read-only unless the operator gave the source a
`writeback` block (see `HubSpotWritebackSettings`): then, and only then, and only if the deployment
turned write-back on, Revansa copies a few of its own results into properties the operator named
(`docs/writeback-hubspot.md`).

One credential, the client's own: its Private App access token. It is a secret of the client's
scope, never shown again, and no two clients may hold the same one (that would be one HubSpot
account serving two clients). It is read here, to build the connector, and nowhere else that runs an
agent; it never reaches a log, an error or a response.

The operator decides what the CRM's words mean (`status_mapping`): there is no default, so a client
with no mapping does not run rather than run on a guess.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from sales_app.config import ClientConfig
from sales_app.config.schema import SourceInstance
from sales_app.config.secrets import HUBSPOT_ACCESS_TOKEN
from sales_app.secret_store import ScopeKind
from sales_app.sourcing.contacts import normalize_phone
from sales_app.sourcing.crm_source import CrmProspectSource, CrmRecord
from sales_app.sourcing.errors import (
    SourceAuthError,
    SourceConflictError,
    SourceDataError,
    SourceError,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
)
from sales_app.sourcing.fields import ProspectField
from sales_app.sourcing.ports import ProspectChange
from sales_app.sourcing.statuses import StatusMapping, StatusWords, resolve_source_status
from sales_app.wiring.providers.types import (
    CredentialDeclaration,
    Product,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
    UniquenessRule,
)
from sales_integrations.hubspot import (
    NEVER_WRITTEN,
    HubSpotAuthError,
    HubSpotClient,
    HubSpotContact,
    HubSpotDataError,
    HubSpotError,
    HubSpotNotFoundError,
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


WritebackProperty = Literal[
    "last_contacted_at",
    "last_inbound_at",
    "follow_up_count",
    "next_action_type",
    "next_action_due_at",
]
_FIELD_OF: Mapping[str, ProspectField] = {
    "last_contacted_at": ProspectField.LAST_CONTACTED_AT,
    "last_inbound_at": ProspectField.LAST_INBOUND_AT,
    "follow_up_count": ProspectField.FOLLOW_UP_COUNT,
    "next_action_type": ProspectField.NEXT_ACTION,
    "next_action_due_at": ProspectField.NEXT_ACTION,
}
# The kind of HubSpot property each written value needs (checked against HubSpot before any write).
_PROPERTY_TYPE: Mapping[str, str] = {
    "last_contacted_at": "datetime",
    "last_inbound_at": "datetime",
    "follow_up_count": "number",
    "next_action_type": "string",
    "next_action_due_at": "datetime",
}


class HubSpotWritebackSettings(BaseModel):
    """What the operator allows Revansa to write back into this HubSpot account, and where.

    Off unless this block exists. Every value written names a contact property **here**, which must
    already exist in HubSpot with the right type: nothing is created and no name is guessed. The
    status goes to `status_property`, with the CRM word that is the inverse of `status_mapping`
    (one word per status, or the source is refused). The next action is two properties (its type
    and its due date) and is named whole or not at all. A source whose property is missing, of the
    wrong type or not settable is not ready and writes nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: bool = False
    properties: dict[WritebackProperty, PropertyName] = Field(default_factory=dict)
    propagate_opt_out: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> "HubSpotWritebackSettings":
        named = set(self.properties)
        if not self.status and not named:
            raise ValueError("a writeback block must write at least one thing")
        if ("next_action_type" in named) != ("next_action_due_at" in named):
            raise ValueError(
                "next_action_type and next_action_due_at are written together: name both or neither"
            )
        if self.propagate_opt_out and not self.status:
            raise ValueError("propagating an opt-out needs the status to be written")
        if len(set(self.properties.values())) != len(self.properties):
            raise ValueError("two values are given the same HubSpot property")
        clash = set(self.properties.values()) & NEVER_WRITTEN
        if clash:
            raise ValueError("a property that says who a person is can never be written")
        return self

    @property
    def fields(self) -> frozenset[ProspectField]:
        found = {_FIELD_OF[key] for key in self.properties}
        return frozenset(found | ({ProspectField.STATUS} if self.status else set()))


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
    # Off unless present. The operator's decision, never the client's.
    writeback: HubSpotWritebackSettings | None = None

    @field_validator("status_mapping")
    @classmethod
    def _understood(cls, value: dict[str, str]) -> dict[str, str]:
        StatusMapping.from_config(value)  # raises a ValueError that words what is wrong
        return value

    @model_validator(mode="after")
    def _writeback_fits(self) -> "HubSpotSourceSettings":
        section = self.writeback
        if section is None:
            return self
        taken = {self.status_property, self.do_not_contact_property}
        if self.locale_property:
            taken.add(self.locale_property)
        if set(section.properties.values()) & taken:
            raise ValueError(
                "writeback.properties cannot name the status, do-not-contact or language property"
            )
        if section.status:
            StatusWords.from_config(
                self.status_mapping, propagate_opt_out=section.propagate_opt_out
            )
        return self


class HubSpotReader:
    """The `CrmReader` of one HubSpot account: HubSpot's contacts as `CrmRecord`s, and HubSpot's
    failures as the application's `SourceError`s (fixed sentences, never what HubSpot said)."""

    def __init__(self, client: HubSpotClient, settings: HubSpotSourceSettings) -> None:
        self._client = client
        self._settings = settings
        self._writeback = settings.writeback
        self._mapping = StatusMapping.from_config(settings.status_mapping)
        self._words = (
            StatusWords.from_config(
                settings.status_mapping, propagate_opt_out=self._writeback.propagate_opt_out
            )
            if self._writeback is not None and self._writeback.status
            else StatusWords({})
        )
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
        return [] if self._writeback is None else self._verify_writeback()

    # ---- write-back: only when the operator turned it on ------------------------------------

    def _verify_writeback(self) -> list[str]:
        """The properties Revansa would write exist in HubSpot, accept what it would write, and can
        be set. Nothing is written, and a problem here keeps the source from being written."""
        assert self._writeback is not None
        problems: list[str] = []
        checks: list[tuple[str, str, str, tuple[str, ...]]] = [
            (key, name, _PROPERTY_TYPE[key], ())
            for key, name in sorted(self._writeback.properties.items())
        ]
        if self._writeback.status:
            words = self._words.all_words()
            checks.append(("status", self._settings.status_property, "enumeration", words))
        for label, name, kind, words in checks:
            try:
                found = _translated(lambda n=name: self._client.get_property(n))
            except SourceAuthError:
                return [
                    (
                        "write-back: HubSpot did not let the Private App read the contact "
                        "property definitions (it needs the crm.schemas.contacts.read scope)"
                    )
                ]
            except SourceError as error:
                return [f"write-back: {error}"]
            if found is None:
                problems.append(f"write-back: the HubSpot property for {label} does not exist")
            elif found.read_only:
                problems.append(f"write-back: the HubSpot property for {label} cannot be set")
            elif label == "status":
                if found.type not in ("enumeration", "string"):
                    problems.append("write-back: the HubSpot status property is not a choice list")
                elif found.type == "enumeration" and not set(words) <= set(found.options):
                    problems.append(
                        "write-back: the HubSpot status property does not offer every value "
                        "Revansa would write"
                    )
            elif found.type != kind:
                problems.append(f"write-back: the HubSpot property for {label} must be {kind}")
        return problems

    def _names(self, fields: Collection[ProspectField]) -> tuple[str, ...]:
        assert self._writeback is not None
        names = [
            name for key, name in self._writeback.properties.items() if _FIELD_OF[key] in fields
        ]
        if ProspectField.STATUS in fields:
            names += [
                self._settings.status_property,
                self._settings.do_not_contact_property,
            ]
        return tuple(dict.fromkeys(names))

    def read_writeback_values(
        self, fields: Collection[ProspectField]
    ) -> Mapping[str, Mapping[ProspectField, Any]]:
        if self._writeback is None:
            raise SourceReadOnlyError("write-back is not configured")
        allowed = self._writeback.fields - {ProspectField.STATUS}
        mirrored = [f for f in fields if f in allowed]
        if not mirrored:
            return {}
        contacts = _translated(lambda: self._client.list_contacts(self._names(mirrored)))
        found: dict[str, Mapping[ProspectField, Any]] = {}
        for contact in contacts:
            try:
                found[contact.id] = self._held(contact, mirrored)
            except ValueError:  # a value that cannot be read: the contact is left alone
                continue
        return found

    def write_fields(self, changes: Sequence[ProspectChange]) -> None:
        if self._writeback is None:
            raise SourceReadOnlyError("write-back is not configured")
        writable = self._writeback.fields
        if not all(set(change.new) <= writable for change in changes):
            raise SourceReadOnlyError("a field of this change cannot be written")
        # Every change is checked against what HubSpot holds *now* before the first one is sent.
        prepared: list[tuple[str, dict[str, str]]] = []
        for change in changes:
            fields = set(change.expected)
            contact = _translated(
                lambda c=change: self._client.get_contact(c.prospect_id, self._names(set(c.new)))
            )
            if contact is None:
                raise KeyError("unknown prospect")
            try:
                current = self._held(contact, fields)
            except ValueError:
                raise SourceConflictError(
                    change.prospect_id, tuple(sorted(f.value for f in fields))
                ) from None
            moved = tuple(
                sorted(
                    f.value
                    for f in fields
                    if _comparable(current[f]) != _comparable(change.expected[f])
                )
            )
            if moved:
                raise SourceConflictError(change.prospect_id, moved)
            prepared.append((change.prospect_id, self._texts(change.new)))
        for contact_id, texts in prepared:
            _translated(lambda i=contact_id, t=texts: self._client.update_contact(i, t))

    def _held(
        self, contact: HubSpotContact, fields: Collection[ProspectField]
    ) -> dict[ProspectField, Any]:
        """What this contact holds now in the properties of these fields, as typed values.
        Raises `ValueError` for a value that cannot be read (never guessed at)."""
        assert self._writeback is not None
        values = contact.properties
        names = self._writeback.properties
        held: dict[ProspectField, Any] = {}
        for each in fields:
            match each:
                case ProspectField.STATUS:
                    resolution = resolve_source_status(
                        values.get(self._settings.status_property),
                        self._mapping,
                        do_not_contact=_refuses(values.get(self._settings.do_not_contact_property)),
                    )
                    if resolution.status is None:
                        raise ValueError("the status cannot be read")
                    held[each] = resolution.status
                case ProspectField.LAST_CONTACTED_AT:
                    held[each] = _moment(values.get(names["last_contacted_at"]))
                case ProspectField.LAST_INBOUND_AT:
                    held[each] = _moment(values.get(names["last_inbound_at"]))
                case ProspectField.FOLLOW_UP_COUNT:
                    held[each] = _count(values.get(names["follow_up_count"]))
                case ProspectField.NEXT_ACTION:
                    held[each] = (
                        _text(values.get(names["next_action_type"])),
                        _moment(values.get(names["next_action_due_at"])),
                    )
        return held

    def _texts(self, new: Mapping[ProspectField, Any]) -> dict[str, str]:
        """Typed values as the text of the properties they go to; an empty value clears one."""
        assert self._writeback is not None
        names = self._writeback.properties
        texts: dict[str, str] = {}
        for each, value in new.items():
            match each:
                case ProspectField.STATUS:
                    word = self._words.word(value)
                    if word is None:
                        raise SourceReadOnlyError("this status cannot be written")
                    texts[self._settings.status_property] = word
                case ProspectField.LAST_CONTACTED_AT:
                    texts[names["last_contacted_at"]] = _moment_text(value)
                case ProspectField.LAST_INBOUND_AT:
                    texts[names["last_inbound_at"]] = _moment_text(value)
                case ProspectField.FOLLOW_UP_COUNT:
                    texts[names["follow_up_count"]] = "" if value is None else str(int(value))
                case ProspectField.NEXT_ACTION:
                    action, due = value
                    texts[names["next_action_type"]] = "" if action is None else str(action)
                    texts[names["next_action_due_at"]] = _moment_text(due)
                case _:
                    raise SourceReadOnlyError("a field of this change cannot be written")
        return texts

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
    except HubSpotNotFoundError:
        raise KeyError("unknown prospect") from None
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


def _moment(value: str | None) -> datetime | None:
    """A HubSpot date-time (ISO 8601, or milliseconds since the epoch) as an aware moment, to the
    second; None for an empty value. Raises `ValueError` for what is neither."""
    text = _text(value)
    if text is None:
        return None
    try:
        if re.fullmatch(r"[0-9]{10,16}", text):
            moment = datetime.fromtimestamp(int(text) / 1000, UTC)
        else:
            moment = datetime.fromisoformat(text)
    except (ValueError, OverflowError, OSError):
        raise ValueError("not a date-time") from None
    return moment.replace(tzinfo=moment.tzinfo or UTC).astimezone(UTC).replace(microsecond=0)


def _count(value: str | None) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        raise ValueError("not a number") from None
    if not number.is_integer() or number < 0:
        raise ValueError("not a count")
    return int(number)


def _moment_text(value: datetime | None) -> str:
    return "" if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _comparable(value: Any) -> Any:
    """A value in the form two reads of the same cell agree on (moments to the second, an empty
    action type as nothing)."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(microsecond=0)
    if isinstance(value, tuple):
        return tuple(_comparable(part) for part in value)
    if isinstance(value, str):
        return value.strip() or None
    return value


def _refuses(value: str | None) -> bool:
    """Fail closed: anything but an empty value or a plain "false" is a refusal."""
    return value is not None and value.strip().lower() not in _FALSE_WORDS


def writeback_of(settings: Mapping[str, Any]) -> tuple[frozenset[ProspectField], bool]:
    """The fields the operator allows to be written back to this HubSpot source, and whether an
    opt-out is propagated. Nothing, for settings without a write-back block. Raises `ValueError`
    (pydantic's) for settings that are not acceptable, such as a status mapping that cannot be
    inverted."""
    section = HubSpotSourceSettings.model_validate(dict(settings)).writeback
    return (frozenset(), False) if section is None else (section.fields, section.propagate_opt_out)


def _build(
    config: ClientConfig, raw_settings: Mapping[str, Any], namespace: str, *, writeback: bool
) -> CrmProspectSource:
    try:
        settings = HubSpotSourceSettings.model_validate(dict(raw_settings))
    except ValidationError as error:
        problems = "; ".join(
            ".".join(str(part) for part in problem["loc"]) + f": {problem['msg']}"
            for problem in error.errors()
        )
        raise ValueError(f"source.settings: {problems}") from None
    if HUBSPOT_ACCESS_TOKEN not in config.secrets:
        raise ValueError("the HubSpot access token is not configured")
    section = settings.writeback if writeback else None
    if section is not None:
        names = list(section.properties.values())
        if section.status:
            names.append(settings.status_property)
        client = HubSpotClient(config.secrets.get(HUBSPOT_ACCESS_TOKEN), writable_properties=names)
    else:
        settings = settings.model_copy(update={"writeback": None})  # a read-only source
        client = HubSpotClient(config.secrets.get(HUBSPOT_ACCESS_TOKEN))
    return CrmProspectSource(
        HubSpotReader(client, settings),
        client_id=config.client_id,
        mapping=StatusMapping.from_config(settings.status_mapping),
        namespace=namespace,
        writable=section.fields if section is not None else (),
    )


def build_source(config: ClientConfig) -> CrmProspectSource:
    """This client's HubSpot source, from its own token and its operator's settings. It is always
    read-only: write-back is wired for sources of lanes only. Raises
    `ValueError` (a fixed sentence, never a value) when either is missing or not acceptable."""
    return _build(config, config.spec.source_settings, KEY, writeback=False)


def build_source_instance(config: ClientConfig, instance: SourceInstance) -> CrmProspectSource:
    """One HubSpot source among a client's several: its prospects are `<instance id>:<native id>`."""
    return _build(config, instance.settings, instance.id, writeback=True)


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
    capabilities={"can_write_back": True, "can_host_store": False},
    unique_across_clients=(UniquenessRule("credential", HUBSPOT_ACCESS_TOKEN, "HubSpot token"),),
    build=build_source,
    product=Product.PROSPECT_SOURCE,
    build_instance=build_source_instance,
)
