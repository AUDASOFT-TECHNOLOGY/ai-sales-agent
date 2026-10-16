"""The connection test of a Google Sheets source, when the operator chose columns for write-back:
a spreadsheet that does not have them is not a source that can be written to.

The spreadsheet is `FakeSheetsApi`; nothing here proves a real Google call works.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from sheets_support import SID, FakeSheetsApi

from sales_app.api.client_source import _writeback_headers
from sales_app.connection_tests import check_google_sheets

TAB = "Prospects"
HEADERS = ("Statut", "Dernier contact", "Relances")


def sheet(*headers):
    api = FakeSheetsApi()
    api.tabs[TAB] = [list(headers) or ["prospect_id", *HEADERS]]
    return api


def check(api, headers=HEADERS):
    return check_google_sheets(
        api=api, spreadsheet_id=SID, prospects_tab=TAB, writeback_headers=headers
    )


def test_without_columns_to_check_the_test_is_exactly_what_it_was_and_reads_no_cell():
    api = sheet()
    result = check_google_sheets(api=api, spreadsheet_id=SID, prospects_tab=TAB)
    assert result.ok and api.count("read") == 0 and api.count("metadata") == 1


def test_a_spreadsheet_that_has_every_chosen_column_once_passes_and_only_its_header_is_read():
    api = sheet()
    api.human_add_row(TAB, prospect_id="p1", Statut="a name nobody should see")
    result = check(api)
    assert result.ok
    assert api.count("read") == 1 and api.calls[-1][1][0].endswith("!1:1")
    assert "nobody" not in repr(result)


def test_headers_are_compared_as_the_adapter_does_ignoring_case_and_spacing():
    assert check(sheet("prospect_id", " statut ", "DERNIER   contact", "relances")).ok


def test_a_chosen_column_that_is_not_there_makes_the_test_fail_and_names_it():
    result = check(sheet("prospect_id", "Statut", "Relances"))
    assert not result.ok and "columns chosen for write-back" in result.message
    assert result.details == {"columns": "Dernier contact"}


def test_a_chosen_column_that_is_there_twice_fails_too():
    result = check(sheet("prospect_id", "Statut", "Dernier contact", "Relances", "relances"))
    assert not result.ok and "more than once" in result.message
    assert result.details == {"columns": "Relances"}


def test_changing_the_spreadsheet_for_one_without_the_columns_fails_the_same_settings():
    old = sheet()
    new = sheet("prospect_id", "Name", "Phone")  # another workbook, other headers
    assert check(old).ok and not check(new).ok


def test_an_unreachable_or_refused_spreadsheet_is_a_failure_with_a_fixed_sentence():
    from sales_integrations.google_sheets import SheetsApiError

    api = sheet()
    api.fail_next("read", SheetsApiError("boom +216 99 100 001", status=403))
    result = check(api)
    assert not result.ok and "+216" not in repr(result) and "boom" not in repr(result)


def test_a_probe_that_cannot_read_cells_is_not_taken_for_a_pass():
    class MetadataOnly:
        def metadata(self, spreadsheet_id):
            return type("Meta", (), {"titles": (TAB,)})()

    result = check_google_sheets(
        api=MetadataOnly(), spreadsheet_id=SID, prospects_tab=TAB, writeback_headers=HEADERS
    )
    assert not result.ok and "cannot be checked" in result.message


def test_the_columns_come_from_the_operators_block_and_nowhere_else():
    block = {"writeback": {"columns": {"status": "Statut", "follow_up_count": "Relances"}}}
    assert _writeback_headers(block) == ("Statut", "Relances")
    assert _writeback_headers({}) == () and _writeback_headers({"writeback": None}) == ()
    assert _writeback_headers({"writeback": {"columns": "nope"}}) == ()
