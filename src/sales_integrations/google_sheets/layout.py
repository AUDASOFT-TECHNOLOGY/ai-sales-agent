"""The spreadsheet layout: tabs, columns, who owns each column, and reading a tab into a table.

Columns are found by their header text, so humans may reorder them, insert their own columns, and
rename headers (through `column_mapping`). Vendor-free: nothing here imports Google libraries.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sales_integrations.google_sheets.errors import SheetLayoutError


class Owner(StrEnum):
    CLIENT = "client"  # the client's team edits it; the agent only reads it
    SYSTEM = "system"  # written by the agent; humans must never edit it
    SHARED = "shared"  # both write it; conflicts are detected, never silently overwritten


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    field: str  # canonical name; also the default header text
    owner: Owner
    required: bool = True  # required columns must exist in the header row


@dataclass(frozen=True, slots=True)
class TabSpec:
    key: str  # "prospects", "actions", "messages", "log", "facts"
    default_title: str
    columns: tuple[ColumnSpec, ...]
    extra_columns: bool = False  # extra (client) columns are kept and exposed as attributes
    on_demand: bool = False  # a spreadsheet without this tab still works; it is created when needed

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(c.field for c in self.columns)


C, S, H = Owner.CLIENT, Owner.SYSTEM, Owner.SHARED

PROSPECTS = TabSpec(
    "prospects",
    "Prospects",
    (
        ColumnSpec("prospect_id", C),
        ColumnSpec("display_name", C),
        ColumnSpec("phone", C),
        ColumnSpec("email", C, required=False),
        ColumnSpec("preferred_channel", C, required=False),
        ColumnSpec("locale", C, required=False),
        ColumnSpec("status", H),
        ColumnSpec("stage", H),
        ColumnSpec("next_action_type", H),
        ColumnSpec("next_action_due_at", H),
        ColumnSpec("conversation_id", S, required=False),
        ColumnSpec("last_contacted_at", S),
        ColumnSpec("last_inbound_at", S),
        ColumnSpec("follow_up_count", S),
    ),
    extra_columns=True,
)

ACTIONS = TabSpec(
    "actions",
    "Actions",
    (
        ColumnSpec("action_id", S),
        ColumnSpec("prospect_id", S),
        ColumnSpec("action_type", S),
        ColumnSpec("due_at", S),
        ColumnSpec("priority", S),
        ColumnSpec("status", H),
        ColumnSpec("idempotency_key", S),
        ColumnSpec("attempts", S),
        ColumnSpec("last_error", S),
        ColumnSpec("draft_text", H),
        ColumnSpec("draft_language", H),
        ColumnSpec("draft_template_id", H),
        ColumnSpec("draft_template_params", H),
        ColumnSpec("next_stage", H),
        ColumnSpec("next_action_type", H),
        ColumnSpec("next_action_due_at", H),
    ),
    extra_columns=True,  # humans may add their own helper columns (for example a name lookup)
)

MESSAGES = TabSpec(
    "messages",
    "Messages",
    tuple(
        ColumnSpec(name, S)
        for name in (
            "message_id",
            "conversation_id",
            "prospect_id",
            "direction",
            "channel",
            "timestamp",
            "text",
            "external_id",
            "metadata",
        )
    ),
)

LOG = TabSpec(
    "log",
    "Log",
    tuple(
        ColumnSpec(name, S)
        for name in ("timestamp", "event_type", "actor", "prospect_id", "action_id", "details")
    ),
)

# Append-only observations about a prospect: key/value facts, one row per observation. Tabs of
# spreadsheets set up before facts existed lack it, hence `on_demand`.
FACTS = TabSpec(
    "facts",
    "Facts",
    tuple(
        ColumnSpec(name, S)
        for name in (
            "fact_id",
            "client_id",
            "prospect_id",
            "fact_key",
            "fact_value",
            "fact_status",
            "confidence",
            "evidence_source",
            "evidence_message_id",
            "evidence_excerpt",
            "evidence_observed_at",
            "evidence_recorded_by",
            "recorded_at",
            "updated_at",
        )
    ),
    on_demand=True,
)

TAB_SPECS: Mapping[str, TabSpec] = {s.key: s for s in (PROSPECTS, ACTIONS, MESSAGES, LOG, FACTS)}


@dataclass(frozen=True, slots=True)
class SheetTabs:
    """The tab titles in the client's spreadsheet."""

    prospects: str = PROSPECTS.default_title
    actions: str = ACTIONS.default_title
    messages: str = MESSAGES.default_title
    log: str = LOG.default_title
    facts: str = FACTS.default_title

    def title(self, key: str) -> str:
        return getattr(self, key)


def validate_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    """Keys are `<tab>.<field>` (for example `prospects.phone`); values are header texts."""
    problems: list[str] = []
    for key, header in mapping.items():
        tab, _, name = key.partition(".")
        spec = TAB_SPECS.get(tab)
        if spec is None or name not in spec.fields:
            problems.append(f"column_mapping key {key!r} is not a known '<tab>.<field>'")
        elif not str(header).strip():
            problems.append(f"column_mapping {key!r} has an empty header")
    if problems:
        raise ValueError("; ".join(problems))
    return {k: str(v).strip() for k, v in mapping.items()}


def header_for(spec: TabSpec, field_name: str, mapping: Mapping[str, str]) -> str:
    return mapping.get(f"{spec.key}.{field_name}", field_name)


def normalize_header(text: object) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


@dataclass(slots=True)
class Row:
    number: int  # 1-based row number in the sheet
    cells: list[Any]


@dataclass(slots=True)
class Table:
    """One tab, parsed: header positions plus the non-blank data rows."""

    spec: TabSpec
    title: str
    headers: list[str]
    index: dict[str, int]  # canonical field -> column position (0-based)
    extras: dict[str, int]  # header text of a non-canonical column -> position
    rows: list[Row] = field(default_factory=list)
    next_row: int = 2  # first row number after the last used one

    def cell(self, row: Row, field_name: str) -> Any:
        position = self.index.get(field_name)
        if position is None or position >= len(row.cells):
            return None
        return row.cells[position]

    def position(self, key: str) -> int | None:
        """Column position of an encoded key: a canonical field, or `@<header>` for an extra."""
        if key.startswith("@"):
            return self.extras.get(key[1:])
        return self.index.get(key)


def parse_table(
    spec: TabSpec,
    title: str,
    values: Sequence[Sequence[Any]],
    mapping: Mapping[str, str],
    *,
    with_rows: bool = True,
) -> Table:
    """Interpret the raw values of a tab. Raises `SheetLayoutError` listing every problem."""
    if not values or not any(str(c).strip() for c in values[0]):
        raise SheetLayoutError([f"tab {title!r} has no header row (row 1 is empty)"])
    headers = [str(c).strip() if c is not None else "" for c in values[0]]
    positions: dict[str, list[int]] = {}
    for position, header in enumerate(headers):
        if header:
            positions.setdefault(normalize_header(header), []).append(position)

    problems: list[str] = []
    index: dict[str, int] = {}
    for column in spec.columns:
        wanted = header_for(spec, column.field, mapping)
        found = positions.get(normalize_header(wanted), [])
        if len(found) > 1:
            problems.append(f"tab {title!r}: header {wanted!r} appears more than once")
        elif found:
            index[column.field] = found[0]
        elif column.required:
            problems.append(f"tab {title!r}: required column {wanted!r} is missing")
    if problems:
        raise SheetLayoutError(problems)

    known = set(index.values())
    extras = (
        {h: i for i, h in enumerate(headers) if h and i not in known} if spec.extra_columns else {}
    )
    table = Table(spec, title, headers, index, extras, next_row=len(values) + 1)
    if with_rows:
        for offset, cells in enumerate(values[1:], start=2):
            if any(str(c).strip() for c in cells if c is not None):
                table.rows.append(Row(offset, list(cells)))
    return table


# ---- A1 notation -----------------------------------------------------------------------------


def column_letter(position: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    number = position + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def quote_tab(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def cell_ref(title: str, position: int, row: int) -> str:
    return f"{quote_tab(title)}!{column_letter(position)}{row}"


def row_ref(title: str, width: int, row: int) -> str:
    return f"{quote_tab(title)}!A{row}:{column_letter(max(width, 1) - 1)}{row}"


_SPREADSHEET_URL = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def extract_spreadsheet_id(text: str) -> str:
    """Accept a spreadsheet URL or a bare id and return the id."""
    text = text.strip()
    found = _SPREADSHEET_URL.search(text)
    if found:
        return found.group(1)
    if _BARE_ID.match(text):
        return text
    raise ValueError(f"{text!r} is neither a Google Sheets URL nor a spreadsheet id")
