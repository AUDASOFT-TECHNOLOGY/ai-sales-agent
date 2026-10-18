"""The Google Sheets provider: prospects and Revansa's own state, kept in one spreadsheet.

It plays two roles, source and store, and can host the store beside the source (the same
spreadsheet is both, as it is today). This module is its only declaration: how to build it, the
settings it accepts, the credential it needs and what two clients must never share.
"""

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from sales_app.config import ClientConfig
from sales_app.config.schema import SourceInstance
from sales_app.config.secrets import SHEETS_CREDENTIALS_FILE, SHEETS_OAUTH_GRANT
from sales_app.sourcing import (
    ProspectChange,
    ProspectField,
    RowIssue,
    RowProblem,
    SheetsProspectSource,
    SourceAuthError,
    SourceConflictError,
    SourceDataError,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
)
from sales_app.wiring.providers.types import (
    CredentialDeclaration,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
    UniquenessRule,
)
from sales_integrations.google_sheets import (
    FieldWrite,
    GoogleSheetsApi,
    GoogleSheetsRepository,
    SheetFieldConflictError,
    SheetsApi,
    SheetsApiError,
    SheetsError,
    SheetTabs,
    extract_spreadsheet_id,
)
from sales_integrations.google_sheets.codecs import format_datetime
from sales_integrations.google_sheets.layout import PROSPECTS, header_for, normalize_header

KEY = "google_sheets"

# The tabs a `column_mapping` key (`<tab>.<field>`) may name, per role. The tab names are the Sheets
# layout's; a test keeps these lists equal to it.
SOURCE_TABS = ("prospects",)
STORE_TABS = ("actions", "messages", "log", "facts")

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _check_mapping(mapping: dict[str, str], tabs: tuple[str, ...]) -> dict[str, str]:
    problems = []
    for key, header in mapping.items():
        tab, _, name = key.partition(".")
        if tab not in tabs or not name:
            problems.append(
                f"column_mapping key {key!r} must be '<tab>.<field>' with a tab of {tabs}"
            )
        elif not str(header).strip():
            problems.append(f"column_mapping {key!r} has an empty header")
    if problems:
        raise ValueError("; ".join(problems))
    return mapping


WritebackColumn = Literal[
    "status",
    "last_contacted_at",
    "last_inbound_at",
    "follow_up_count",
    "next_action_type",
    "next_action_due_at",
]
# What each column of the prospects tab stands for when Revansa writes it back.
_WRITEBACK_OF: Mapping[str, ProspectField] = {
    "status": ProspectField.STATUS,
    "last_contacted_at": ProspectField.LAST_CONTACTED_AT,
    "last_inbound_at": ProspectField.LAST_INBOUND_AT,
    "follow_up_count": ProspectField.FOLLOW_UP_COUNT,
    "next_action_type": ProspectField.NEXT_ACTION,
    "next_action_due_at": ProspectField.NEXT_ACTION,
}


class SheetsWritebackSettings(BaseModel):
    """What the operator allows Revansa to write back into this spreadsheet, and where.

    Off unless this block exists. Every field written needs a column **named here**, which must
    already exist in the prospects tab: nothing is created, nothing is guessed from a default name,
    and a source whose column is missing or named twice is not ready and writes nothing. The next
    action is two columns (its type and its due date) and is named whole or not at all.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: dict[WritebackColumn, NonEmpty] = Field(min_length=1)
    propagate_opt_out: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> "SheetsWritebackSettings":
        named = set(self.columns)
        if ("next_action_type" in named) != ("next_action_due_at" in named):
            raise ValueError(
                "next_action_type and next_action_due_at are written together: name both or neither"
            )
        if self.propagate_opt_out and "status" not in named:
            raise ValueError("propagating an opt-out needs the status column")
        return self

    @property
    def fields(self) -> frozenset[ProspectField]:
        return frozenset(_WRITEBACK_OF[key] for key in self.columns)


class SheetsSourceSettings(BaseModel):
    """Where the prospects are in the spreadsheet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spreadsheet_id: NonEmpty
    prospects_tab: NonEmpty = "Prospects"
    column_mapping: dict[str, str] = Field(default_factory=dict)
    writeback: SheetsWritebackSettings | None = None  # off by default

    @field_validator("column_mapping")
    @classmethod
    def _prospect_columns(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_mapping(value, SOURCE_TABS)

    @property
    def effective_column_mapping(self) -> dict[str, str]:
        """The one mapping the adapter reads and writes with: `column_mapping`, plus the columns
        the write-back names (a header is named once, so it cannot be read from one column and
        written to another)."""
        mapping = dict(self.column_mapping)
        if self.writeback is not None:
            mapping.update({f"prospects.{k}": h for k, h in self.writeback.columns.items()})
        return mapping

    @model_validator(mode="after")
    def _writeback_columns_agree(self) -> "SheetsSourceSettings":
        if self.writeback is None:
            return self
        for key, header in self.writeback.columns.items():
            named = self.column_mapping.get(f"prospects.{key}")
            if named is not None and normalize_header(named) != normalize_header(header):
                raise ValueError(
                    "writeback.columns and column_mapping name different columns for one field"
                )
        mapping = self.effective_column_mapping
        headers = [
            normalize_header(header_for(PROSPECTS, c.field, mapping)) for c in PROSPECTS.columns
        ]
        if len(set(headers)) != len(headers):
            raise ValueError("two fields of the prospects tab are given the same column")
        return self


def writeback_of(settings: Mapping[str, Any]) -> tuple[frozenset[ProspectField], bool]:
    """The fields the operator allows to be written back to this spreadsheet, and whether an opt-out
    is propagated. Nothing, for settings without a write-back block. Raises `ValueError` (pydantic's)
    for settings that are not acceptable."""
    section = SheetsSourceSettings.model_validate(dict(settings)).writeback
    return (frozenset(), False) if section is None else (section.fields, section.propagate_opt_out)


class SheetsStoreSettings(BaseModel):
    """Where Revansa's own state is in the spreadsheet (the source's, unless said otherwise)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actions_tab: NonEmpty = "Actions"
    messages_tab: NonEmpty = "Messages"
    log_tab: NonEmpty = "Log"
    column_mapping: dict[str, str] = Field(default_factory=dict)

    @field_validator("column_mapping")
    @classmethod
    def _store_columns(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_mapping(value, STORE_TABS)


def spreadsheet_id_of(text: str) -> str:
    """The id inside a spreadsheet URL, or the bare id itself. ValueError, without echoing `text`,
    when it is neither."""
    try:
        return extract_spreadsheet_id(text)
    except ValueError:
        raise ValueError("is neither a Google Sheets URL nor a spreadsheet id") from None


def service_account_email(credentials_file: str | None) -> str | None:
    """The address a client must share its spreadsheet with: the `client_email` of the platform's
    service-account file. It is an identifier, not a secret; None when the file cannot be read."""
    if not credentials_file:
        return None
    try:
        email = json.loads(Path(credentials_file).read_text(encoding="utf-8")).get("client_email")
    except (OSError, ValueError, AttributeError):
        return None
    return email if isinstance(email, str) and "@" in email else None


def open_probe(credentials_file: str, *, timeout_seconds: float) -> GoogleSheetsApi:
    """The Sheets API for a connection check: one try, a bounded wait."""
    return GoogleSheetsApi.from_service_account_file(
        credentials_file, timeout_seconds=timeout_seconds, max_attempts=1
    )


def open_oauth_api(
    source: Any, *, timeout_seconds: float | None = None, max_attempts: int = 5
) -> GoogleSheetsApi:
    """Sheets over a client's own Google authorisation (`source` gives fresh access tokens)."""
    return GoogleSheetsApi.from_token_source(
        source, timeout_seconds=timeout_seconds, max_attempts=max_attempts
    )


def api_for(config: ClientConfig) -> GoogleSheetsApi:
    """The client's own Google authorisation when it holds one (the way a client connects), else the
    deployment's service-account file (the older way, kept for deployments that still use it)."""
    if SHEETS_OAUTH_GRANT in config.secrets:
        from sales_app import sheets_wiring  # not at import: it needs the whole application

        service = sheets_wiring.installed()
        if service is None:
            raise SourceAuthError("Google Sheets access is not available")
        return service.api(config.client_id)
    return GoogleSheetsApi.from_service_account_file(config.secrets.get(SHEETS_CREDENTIALS_FILE))


def build_repository(config: ClientConfig) -> GoogleSheetsRepository:
    section = config.spec.sheets
    return GoogleSheetsRepository(
        api_for(config),
        client_id=config.client_id,  # the Core's ClientId is a plain str at run time
        spreadsheet_id=extract_spreadsheet_id(section.spreadsheet_id),
        timezone=ZoneInfo(config.spec.timezone),
        tabs=SheetTabs(
            prospects=section.prospects_tab,
            actions=section.actions_tab,
            messages=section.messages_tab,
            log=section.log_tab,
        ),
        column_mapping=section.column_mapping,
    )


class SheetsProspectReader:
    """The prospects tab of one spreadsheet, read through the Sheets adapter, as a `SheetsReader`.

    It reads nothing but that tab (the tabs of a store, if the spreadsheet has any, are not its
    business), and turns every failure of the adapter into a `SourceError` with a fixed sentence.
    The adapter is built never to write: it gives no id to a row that has none (that is a write),
    unless the operator asked for it with `assign_missing_ids`.
    """

    def __init__(
        self,
        repository: GoogleSheetsRepository,
        *,
        zone: ZoneInfo | None = None,
        writable: Collection[ProspectField] = (),
    ) -> None:
        self._repository = repository
        self._writable = frozenset(writable)
        self._zone = zone
        if self._writable and zone is None:
            raise ValueError("writing to a sheet needs the client's time zone")

    def __repr__(self) -> str:
        return "SheetsProspectReader()"

    # ---- write-back: only when the operator turned it on ----------------------------------

    def read_writeback_values(
        self, fields: Collection[ProspectField]
    ) -> Mapping[str, Mapping[ProspectField, Any]]:
        mirrored = [f for f in fields if f is not ProspectField.STATUS and f in self._writable]
        held = _translated(self._repository.read_mirrored)
        return {
            native_id: {f: values[f.value] for f in mirrored} for native_id, values in held.items()
        }

    def write_fields(self, changes: Sequence[ProspectChange]) -> None:
        if not all(set(change.new) <= self._writable for change in changes):
            raise SourceReadOnlyError("a field of this change cannot be written")
        writes = [
            FieldWrite(c.prospect_id, self._cells(c.new), self._cells(c.expected)) for c in changes
        ]
        _translated(lambda: self._repository.write_fields(writes))

    def _cells(self, values: Mapping[ProspectField, Any]) -> dict[str, str]:
        """Typed values as the text of the columns they go to; an empty value is an empty cell."""
        assert self._zone is not None
        cells: dict[str, str] = {}
        for field, value in values.items():
            if field is ProspectField.NEXT_ACTION:
                action, due = value
                cells["next_action_type"] = "" if action is None else str(action)
                cells["next_action_due_at"] = format_datetime(due, self._zone)
            elif field is ProspectField.FOLLOW_UP_COUNT:
                cells[field.value] = "" if value is None else str(int(value))
            elif field is ProspectField.STATUS:
                cells[field.value] = str(value)
            else:
                cells[field.value] = format_datetime(value, self._zone)
        return cells

    def list_prospects(self) -> Sequence[Any]:
        return _translated(self._repository.list_prospects)

    def get_prospect(self, native_id: str) -> Any | None:
        return _translated(lambda: self._repository.get_prospect(native_id))  # type: ignore[arg-type]

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Any | None:
        return _translated(
            lambda: self._repository.find_prospect_by_contact(phone=phone, email=email)
        )

    def row_problems(self) -> Sequence[RowProblem]:
        return [
            RowProblem(problem.row, _issue_of(problem.message))
            for problem in self._repository.problems
            if problem.tab == self._repository.prospects_title
        ]

    def verify(self) -> list[str]:
        return _translated(lambda: self._repository.verify(("prospects",)))


def _issue_of(message: str) -> RowIssue:
    """What the adapter said about a row, as a kind. The sentence itself is never passed on: it may
    quote an id or a phone number."""
    if message.startswith("duplicate prospect_id"):
        return RowIssue.DUPLICATE_ID
    if message.startswith("same phone number"):
        return RowIssue.DUPLICATE_PHONE
    return RowIssue.UNREADABLE


def _translated[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except SheetFieldConflictError as error:
        raise SourceConflictError(error.prospect_id, error.fields) from None
    except SheetsApiError as error:
        if error.status in (401, 403):
            raise SourceAuthError("Google Sheets refused the credentials or the access") from None
        if error.status == 429:
            raise SourceRateLimitedError("the Google Sheets rate limit was reached") from None
        raise SourceUnavailableError("Google Sheets could not be reached or failed") from None
    except SheetsError:
        raise SourceDataError("the spreadsheet is not laid out as Revansa expects") from None


def build_prospect_source(
    api: SheetsApi,
    *,
    client_id: str,
    source_id: str | None,
    settings: Mapping[str, Any],
    timezone: ZoneInfo,
    assign_missing_ids: bool = False,
) -> SheetsProspectSource:
    """A read-only prospect source over one spreadsheet, from the operator's settings for it. It
    writes only if those settings carry a `writeback` block (see `SheetsWritebackSettings`).

    `source_id` is the source's name in the client's configuration and the prefix of its prospects'
    ids; None keeps the ids as the sheet has them (a client's historical spreadsheet). The client's
    Google Sheets credential is the one deployment-wide file, as for every Sheets client today, so
    two sources of one client are two spreadsheets under the same credential.
    """
    section = SheetsSourceSettings.model_validate(dict(settings))
    writable = section.writeback.fields if section.writeback is not None else frozenset()
    repository = GoogleSheetsRepository(
        api,
        client_id=client_id,  # the Core's ClientId is a plain str at run time
        spreadsheet_id=extract_spreadsheet_id(section.spreadsheet_id),
        timezone=timezone,
        tabs=SheetTabs(prospects=section.prospects_tab),
        column_mapping=section.effective_column_mapping,
        assign_missing_ids=assign_missing_ids,
    )
    return SheetsProspectSource(
        SheetsProspectReader(repository, zone=timezone, writable=writable),
        client_id=client_id,
        namespace=source_id,
        writable=writable,
    )


def build_source_instance(config: ClientConfig, instance: SourceInstance) -> SheetsProspectSource:
    """One spreadsheet among a client's several, read-only: its prospects are `<instance id>:<id>`.
    Every instance reads under the deployment's one Google credential."""
    return build_prospect_source(
        api_for(config),
        client_id=config.client_id,
        source_id=instance.id,
        settings=instance.settings,
        timezone=ZoneInfo(config.spec.timezone),
    )


DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.SOURCE,
    label="Google Sheets",
    roles=frozenset({ProviderKind.SOURCE, ProviderKind.STORE}),
    can_host_store=True,
    settings_models={
        ProviderKind.SOURCE: SheetsSourceSettings,
        ProviderKind.STORE: SheetsStoreSettings,
    },
    credentials=(
        # The client's own Google authorisation (OAuth, stored in its scope) is how a client
        # connects; a deployment may still supply one service-account file from the environment.
        CredentialDeclaration(
            key=SHEETS_CREDENTIALS_FILE,
            label="Google Sheets credentials file",
            scope=None,
            required_when=Requirement.ALWAYS,
        ),
    ),
    connection_keys=(SHEETS_CREDENTIALS_FILE,),
    capabilities={"can_write_back": True, "can_host_store": True},
    unique_across_clients=(UniquenessRule("setting", "spreadsheet_id", "spreadsheet_id"),),
    build=build_repository,
    build_instance=build_source_instance,
)
