"""SalesRepository adapter for Google Sheets. See docs/sheet-schema.md for the spreadsheet layout."""

from sales_integrations.google_sheets.api import GoogleSheetsApi, SheetMeta, SheetsApi
from sales_integrations.google_sheets.errors import (
    SheetConflictError,
    SheetDataError,
    SheetFieldConflictError,
    SheetLayoutError,
    SheetsApiError,
    SheetsError,
)
from sales_integrations.google_sheets.layout import SheetTabs, extract_spreadsheet_id
from sales_integrations.google_sheets.repository import (
    FieldWrite,
    GoogleSheetsRepository,
    RowProblem,
)

__all__ = [
    "FieldWrite",
    "GoogleSheetsApi",
    "GoogleSheetsRepository",
    "RowProblem",
    "SheetConflictError",
    "SheetDataError",
    "SheetFieldConflictError",
    "SheetLayoutError",
    "SheetMeta",
    "SheetTabs",
    "SheetsApi",
    "SheetsApiError",
    "SheetsError",
    "extract_spreadsheet_id",
]
