"""The Google Sheets provider: prospects and Revansa's own state, kept in one spreadsheet.

It plays two roles, source and store, and can host the store beside the source (the same
spreadsheet is both, as it is today). This module is its only declaration: how to build it, the
settings it accepts, the credential it needs and what two clients must never share.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from sales_app.config import ClientConfig
from sales_app.config.secrets import SHEETS_CREDENTIALS_FILE
from sales_app.sourcing import (
    RowIssue,
    RowProblem,
    SheetsProspectSource,
    SourceAuthError,
    SourceDataError,
    SourceRateLimitedError,
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
    GoogleSheetsApi,
    GoogleSheetsRepository,
    SheetsApi,
    SheetsApiError,
    SheetsError,
    SheetTabs,
    extract_spreadsheet_id,
)

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


class SheetsSourceSettings(BaseModel):
    """Where the prospects are in the spreadsheet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spreadsheet_id: NonEmpty
    prospects_tab: NonEmpty = "Prospects"
    column_mapping: dict[str, str] = Field(default_factory=dict)

    @field_validator("column_mapping")
    @classmethod
    def _prospect_columns(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_mapping(value, SOURCE_TABS)


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


def build_repository(config: ClientConfig) -> GoogleSheetsRepository:
    section = config.spec.sheets
    return GoogleSheetsRepository(
        GoogleSheetsApi.from_service_account_file(config.secrets.get(SHEETS_CREDENTIALS_FILE)),
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

    def __init__(self, repository: GoogleSheetsRepository) -> None:
        self._repository = repository

    def __repr__(self) -> str:
        return "SheetsProspectReader()"

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
    """A read-only prospect source over one spreadsheet, from the operator's settings for it.

    `source_id` is the source's name in the client's configuration and the prefix of its prospects'
    ids; None keeps the ids as the sheet has them (a client's historical spreadsheet). The client's
    Google Sheets credential is the one deployment-wide file, as for every Sheets client today, so
    two sources of one client are two spreadsheets under the same credential.
    """
    section = SheetsSourceSettings.model_validate(dict(settings))
    repository = GoogleSheetsRepository(
        api,
        client_id=client_id,  # the Core's ClientId is a plain str at run time
        spreadsheet_id=extract_spreadsheet_id(section.spreadsheet_id),
        timezone=timezone,
        tabs=SheetTabs(prospects=section.prospects_tab),
        column_mapping=section.column_mapping,
        assign_missing_ids=assign_missing_ids,
    )
    return SheetsProspectSource(
        SheetsProspectReader(repository), client_id=client_id, namespace=source_id
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
        # The access model is going to change (OAuth per client); until then only the environment
        # supplies it, one service-account file for the deployment.
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
)
