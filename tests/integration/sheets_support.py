"""An in-memory Google Sheets stand-in for adapter tests, plus helpers that play the human user.

`FakeSheetsApi` implements the adapter's `SheetsApi` interface over plain grids. Values written
through it are stored exactly as given (like the real API with RAW input); values a test "types"
with `human_*` helpers keep their Python type (a typed number stays a number), as in a real sheet.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from sales_core.contracts import ClientId
from sales_integrations.google_sheets import (
    GoogleSheetsRepository,
    SheetMeta,
    SheetsApiError,
    SheetTabs,
)
from sales_integrations.google_sheets.layout import TAB_SPECS, column_letter

SID = "sheet-1"
TUNIS = ZoneInfo("Africa/Tunis")

_CELL = re.compile(r"^'((?:[^']|'')+)'(?:!([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?)?$")
_ROWS = re.compile(r"^'((?:[^']|'')+)'!(\d+):(\d+)$")


def _letters_to_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _blank(value: Any) -> bool:
    return value is None or value == ""


@dataclass
class FakeSheetsApi:
    timezone: str = "Africa/Tunis"
    tabs: dict[str, list[list[Any]]] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    failures: dict[str, list[Exception]] = field(default_factory=dict)
    hooks: dict[str, list[Callable[[], None]]] = field(
        default_factory=dict
    )  # run just before a call applies

    # ---- SheetsApi ------------------------------------------------------------------------

    def read(self, spreadsheet_id: str, ranges: Sequence[str]) -> list[list[list[Any]]]:
        self._enter("read", list(ranges))
        return [self._read_range(r) for r in ranges]

    def write(self, spreadsheet_id: str, updates: Sequence[tuple[str, list[list[str]]]]) -> None:
        self._enter("write", [r for r, _ in updates])
        self._run_hooks("write")
        for range_, values in updates:
            match = _CELL.match(range_)
            assert match and match.group(2), f"bad write range {range_}"
            title = match.group(1).replace("''", "'")
            grid = self.tabs[title]
            column, row = _letters_to_index(match.group(2)), int(match.group(3))
            for r_offset, values_row in enumerate(values):
                for c_offset, value in enumerate(values_row):
                    self._set(grid, row + r_offset, column + c_offset, value)

    def append(self, spreadsheet_id: str, range_: str, rows: list[list[str]]) -> None:
        self._enter("append", range_)
        self._run_hooks("append")
        match = _CELL.match(range_)
        assert match
        grid = self.tabs[match.group(1).replace("''", "'")]
        while grid and all(_blank(c) for c in grid[-1]):
            grid.pop()
        grid.extend([list(r) for r in rows])

    def metadata(self, spreadsheet_id: str) -> SheetMeta:
        self._enter("metadata", None)
        return SheetMeta(tuple(self.tabs), self.timezone)

    def add_tabs(self, spreadsheet_id: str, titles: Sequence[str]) -> None:
        self._enter("add_tabs", list(titles))
        for title in titles:
            self.tabs[title] = []

    # ---- failure injection and call accounting ---------------------------------------

    def fail_next(self, method: str, error: Exception | None = None, times: int = 1) -> None:
        failure = error or SheetsApiError("injected failure", status=500)
        self.failures.setdefault(method, []).extend([failure] * times)

    def before(self, method: str, action: Callable[[], None]) -> None:
        """Run `action` (a person doing something) once, just before the next `method` call applies."""
        self.hooks.setdefault(method, []).append(action)

    def _run_hooks(self, method: str) -> None:
        for action in self.hooks.pop(method, []):
            action()

    def count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    def _enter(self, method: str, detail: Any) -> None:
        queue = self.failures.get(method)
        if queue:
            raise queue.pop(0)
        self.calls.append((method, detail))

    # ---- grid internals ------------------------------------------------------------------

    @staticmethod
    def _set(grid: list[list[Any]], row: int, column: int, value: Any) -> None:
        while len(grid) < row:
            grid.append([])
        cells = grid[row - 1]
        while len(cells) <= column:
            cells.append("")
        cells[column] = value

    def _read_range(self, range_: str) -> list[list[Any]]:
        rows = _ROWS.match(range_)
        if rows:
            title = rows.group(1).replace("''", "'")
            grid = self._tab(title)
            first, last = int(rows.group(2)), int(rows.group(3))
            return self._trim(grid[first - 1 : last])
        match = _CELL.match(range_)
        assert match, f"unsupported read range {range_}"
        return self._trim(self._tab(match.group(1).replace("''", "'")))

    def _tab(self, title: str) -> list[list[Any]]:
        if title not in self.tabs:
            raise SheetsApiError(f"Unable to parse range: {title}", status=400)
        return self.tabs[title]

    @staticmethod
    def _trim(grid: list[list[Any]]) -> list[list[Any]]:
        trimmed = []
        for cells in grid:
            cells = list(cells)
            while cells and _blank(cells[-1]):
                cells.pop()
            trimmed.append(cells)
        while trimmed and not trimmed[-1]:
            trimmed.pop()
        return trimmed

    # ---- helpers that play the human user --------------------------------------------

    def create_layout(
        self,
        mapping: dict[str, str] | None = None,
        tabs: SheetTabs | None = None,
        prospect_headers: Sequence[str] | None = None,
    ) -> None:
        """Tabs with the canonical header rows (optionally renamed through `mapping`)."""
        mapping = mapping or {}
        tabs = tabs or SheetTabs()
        for key, spec in TAB_SPECS.items():
            headers = [mapping.get(f"{key}.{c.field}", c.field) for c in spec.columns]
            if key == "prospects" and prospect_headers is not None:
                headers = list(prospect_headers)
            self.tabs[tabs.title(key)] = [headers]

    def headers(self, tab: str) -> list[str]:
        return [str(h) for h in self.tabs[tab][0]]

    def human_add_row(self, tab: str, **cells: Any) -> int:
        """Append a row typed by a person, addressed by header text. Returns its row number."""
        headers = self.headers(tab)
        row = [""] * len(headers)
        for header, value in cells.items():
            row[headers.index(header)] = value
        grid = self.tabs[tab]
        while len(grid) > 1 and all(_blank(c) for c in grid[-1]):
            grid.pop()
        grid.append(row)
        return len(grid)

    def human_set(self, tab: str, row: int, header: str, value: Any) -> None:
        self._set(self.tabs[tab], row, self.headers(tab).index(header), value)

    def cell(self, tab: str, row: int, header: str) -> Any:
        position = self.headers(tab).index(header)
        cells = self.tabs[tab][row - 1]
        return cells[position] if position < len(cells) else ""

    def rows(self, tab: str) -> list[dict[str, Any]]:
        headers = self.headers(tab)
        result = []
        for cells in self.tabs[tab][1:]:
            if any(not _blank(c) for c in cells):
                result.append(
                    {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(headers)}
                )
        return result

    def find_row(self, tab: str, header: str, value: str) -> int:
        position = self.headers(tab).index(header)
        for number, cells in enumerate(self.tabs[tab][1:], start=2):
            if position < len(cells) and str(cells[position]) == value:
                return number
        raise LookupError(f"no {header}={value!r} in {tab}")


def make_repository(
    api: FakeSheetsApi | None = None,
    *,
    client_id: str = "client-a",
    layout: bool = True,
    prospect_headers: Sequence[str] | None = None,
    **kwargs: Any,
) -> tuple[GoogleSheetsRepository, FakeSheetsApi]:
    api = api or FakeSheetsApi()
    mapping = kwargs.get("column_mapping")
    tabs = kwargs.get("tabs")
    if layout:
        api.create_layout(mapping, tabs, prospect_headers)
    repo = GoogleSheetsRepository(
        api,
        client_id=ClientId(client_id),
        spreadsheet_id=SID,
        timezone=TUNIS,
        **kwargs,
    )
    return repo, api


def column_of(header_index: int) -> str:
    return column_letter(header_index)


CallHook = Callable[[], None]
