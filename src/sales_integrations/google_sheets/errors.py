"""Errors raised by the Google Sheets adapter."""

from collections.abc import Sequence


class SheetsError(RuntimeError):
    """Base class for every adapter-specific failure."""


class SheetsApiError(SheetsError):
    """The Google Sheets API refused or failed a request."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SheetLayoutError(SheetsError):
    """The spreadsheet does not have the tabs or columns the adapter needs."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


class SheetConflictError(SheetsError):
    """A cell the adapter was about to change was changed by someone else since it was read.

    Nothing is written when this is raised. The caller re-reads and decides again; this is what
    stops a human's approval, rejection or opt-out from being silently overwritten.
    """


class SheetDataError(SheetsError):
    """A row the adapter cannot safely interpret was needed for the operation (fail closed)."""
