"""The Google Sheets provider: prospects and Revansa's own state, kept in one spreadsheet.

It plays two roles, source and store, and can host the store beside the source (the same
spreadsheet is both, as it is today). This module is its only declaration: how to build it, the
settings it accepts, the credential it needs and what two clients must never share.
"""

from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from sales_app.config import ClientConfig
from sales_app.config.secrets import SHEETS_CREDENTIALS_FILE
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
    capabilities={"can_write_back": True, "can_host_store": True},
    unique_across_clients=(UniquenessRule("setting", "spreadsheet_id", "spreadsheet_id"),),
    build=build_repository,
)
