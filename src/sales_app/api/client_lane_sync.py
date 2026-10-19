"""What the client says about the results Revansa copies back into a lane's source (write-back).

The models and the pure functions behind `GET/PUT /client/v1/lanes/{lane_id}/sync` (the routes live
with the lanes, `client_lanes`). Revansa keeps what it does for each prospect in its own database;
this is the client choosing *where, in its own spreadsheet or HubSpot, a copy is shown*:

- Google Sheets: a column of the prospects tab, named one by one, for each result copied;
- HubSpot: a contact property, named one by one, for each result copied, and the HubSpot words for
  the statuses (the lead status property and what each of its values means to Revansa).

Nothing is on by default and nothing is guessed: no block, no write. The client can only name
existing columns/properties; the rules (what is never overwritten, no column created, no identity
property written) are the providers' and are checked when the settings are saved. The deployment has
a master switch (`SALES_AGENT_WRITEBACK_ENABLED`): a block saved while it is off is kept and says so;
it writes nothing until the platform turns it on.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
PropertyName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,99}$")]
ColumnKey = Literal[
    "status",
    "last_contacted_at",
    "last_inbound_at",
    "follow_up_count",
    "next_action_type",
    "next_action_due_at",
]
PropertyKey = Literal[
    "last_contacted_at",
    "last_inbound_at",
    "follow_up_count",
    "next_action_type",
    "next_action_due_at",
    "summary",
]

SyncState = Literal["off", "waiting_platform", "configured"]

# What can be copied, in the words shown to the client.
RESULTS: tuple[tuple[str, str], ...] = (
    ("status", "Status (paused, closed, opted out)"),
    ("last_contacted_at", "Date of the last message sent"),
    ("last_inbound_at", "Date of the last reply"),
    ("follow_up_count", "Number of follow-ups"),
    ("next_action_type", "Next action"),
    ("next_action_due_at", "Next action due date"),
    ("summary", "Commercial summary"),
)


class SheetsSync(BaseModel):
    """The columns of the prospects tab Revansa may fill: one existing header per result."""

    model_config = ConfigDict(extra="forbid")

    columns: dict[ColumnKey, NonEmpty] = Field(min_length=1)
    propagate_opt_out: bool = False


class HubSpotSync(BaseModel):
    """The existing HubSpot contact properties Revansa may fill. `status` copies Revansa's status into
    the source's status property, in the HubSpot word the Admin mapped. What the lead statuses mean
    (`status_mapping`, `status_property`) is the Admin's, never the client's: no field for it here."""

    model_config = ConfigDict(extra="forbid")

    status: bool = False
    properties: dict[PropertyKey, PropertyName] = Field(default_factory=dict)
    propagate_opt_out: bool = False


class UpdateSyncRequest(BaseModel):
    """`enabled: false` removes the write-back (and nothing else). Only one provider's block."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None
    enabled: bool = True
    google_sheets: SheetsSync | None = None
    hubspot: HubSpotSync | None = None


class ResultOption(BaseModel):
    key: str
    label: str


class SyncResponse(BaseModel):
    version: int | None
    lane: str
    source: str  # the provider's key
    supported: bool  # False: this source cannot receive results
    state: SyncState
    message: str
    deployment_enabled: bool  # False: the platform has not turned write-back on
    results: list[ResultOption]
    google_sheets: SheetsSync | None = None
    hubspot: HubSpotSync | None = None


def sheets_block(sync: SheetsSync) -> dict[str, Any]:
    """The provider's `writeback` block for a spreadsheet."""
    block: dict[str, Any] = {"columns": dict(sync.columns)}
    if sync.propagate_opt_out:
        block["propagate_opt_out"] = True
    return block


def hubspot_changes(sync: HubSpotSync) -> dict[str, Any]:
    """The provider's source-settings keys a HubSpot sync sets (a key left out keeps its value)."""
    changes: dict[str, Any] = {}
    if sync.status or sync.properties:
        block: dict[str, Any] = {}
        if sync.status:
            block["status"] = True
        if sync.properties:
            block["properties"] = dict(sync.properties)
        if sync.propagate_opt_out:
            block["propagate_opt_out"] = True
        changes["writeback"] = block
    return changes


def sheets_view(settings: Mapping[str, Any]) -> SheetsSync | None:
    block = settings.get("writeback")
    if not isinstance(block, Mapping) or not isinstance(block.get("columns"), Mapping):
        return None
    return SheetsSync.model_validate(
        {"columns": dict(block["columns"]), "propagate_opt_out": bool(block.get("propagate_opt_out"))}
    )


def hubspot_view(settings: Mapping[str, Any]) -> HubSpotSync:
    """What the HubSpot source copies back now."""
    block = settings.get("writeback")
    block = block if isinstance(block, Mapping) else {}
    return HubSpotSync.model_validate(
        {
            "status": bool(block.get("status")),
            "properties": dict(block["properties"])
            if isinstance(block.get("properties"), Mapping)
            else {},
            "propagate_opt_out": bool(block.get("propagate_opt_out")),
        }
    )


def state_of(*, has_block: bool, deployment_enabled: bool) -> tuple[SyncState, str]:
    if not has_block:
        return "off", "Nothing is copied back: choose where the results should appear."
    if not deployment_enabled:
        return (
            "waiting_platform",
            "Your choices are saved. Revansa has not switched copying on yet; nothing is written "
            "until it does.",
        )
    return (
        "configured",
        "Revansa copies the results you chose. Test your connection to check they can be written.",
    )
