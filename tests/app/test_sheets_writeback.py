"""The Google Sheets source when the operator allowed write-back: what it writes, and what it refuses.

Deterministic. The spreadsheet is `FakeSheetsApi`, an in-memory grid: it stands for a real Google
spreadsheet and proves nothing about a real Google call, credential or quota. The adapter, the
wiring reader and the source are the real ones.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from pydantic import ValidationError
from sheets_support import SID, TUNIS, FakeSheetsApi

from sales_app.sourcing import (
    ProspectChange,
    ProspectField,
    SourceAuthError,
    SourceConflictError,
    SourceDataError,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
    WriteBackReadable,
)
from sales_app.wiring.providers.google_sheets import (
    SheetsSourceSettings,
    build_prospect_source,
    writeback_of,
)
from sales_core.contracts import ProspectStatus, SalesActionType
from sales_integrations.google_sheets import SheetsApiError

CLIENT = "client-a"
TAB = "Prospects"
PHONE = "+216 99 888 777"
S = ProspectStatus
F = ProspectField
HEADERS = {
    "status": "Statut",
    "last_contacted_at": "Dernier contact",
    "last_inbound_at": "Derniere reponse",
    "follow_up_count": "Relances",
    "next_action_type": "Prochaine action",
    "next_action_due_at": "Echeance",
}
ALL_FIELDS = {F.STATUS, F.LAST_CONTACTED_AT, F.LAST_INBOUND_AT, F.FOLLOW_UP_COUNT, F.NEXT_ACTION}
T1 = datetime(2026, 3, 2, 10, 30, tzinfo=UTC)  # 11:30 in Tunis
T2 = datetime(2026, 3, 5, 8, 0, tzinfo=UTC)


def settings(columns=None, **extra):
    block = {"columns": dict(HEADERS if columns is None else columns), **extra}
    return {"spreadsheet_id": SID, "writeback": block}


def layout(api, headers=None):
    api.create_layout({f"prospects.{key}": header for key, header in (headers or HEADERS).items()})


def source_over(api, *, configured=None, source_id="sheet_a", client=CLIENT, lay=True):
    if lay:
        layout(api)
    return build_prospect_source(
        api,
        client_id=client,
        source_id=source_id,
        settings=configured if configured is not None else settings(),
        timezone=TUNIS,
    )


def add(api, pid, name="Ada Lovelace", phone=PHONE, **cells):
    return api.human_add_row(TAB, prospect_id=pid, display_name=name, phone=phone, **cells)


def change(pid, new, expected):
    return ProspectChange(prospect_id=pid, new=new, expected=expected)


def writes(api):
    return api.count("write")


@pytest.fixture
def api():
    return FakeSheetsApi()


# ---- off by default -----------------------------------------------------------------------------


def test_without_a_writeback_block_nothing_is_writable_and_nothing_is_ever_sent(api):
    source = source_over(api, configured={"spreadsheet_id": SID})
    add(api, "p1")
    capabilities = source.capabilities
    assert capabilities.writable == frozenset() and capabilities.mirrored == frozenset()
    assert not capabilities.consent_signal
    assert source.read_writeback_values() == {}
    before = len(api.calls)
    with pytest.raises(SourceReadOnlyError):
        source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert len(api.calls) == before and writes(api) == 0  # not even a read


def test_the_block_is_what_turns_it_on_and_it_declares_exactly_what_it_may_write(api):
    source = source_over(api)
    capabilities = source.capabilities
    assert capabilities.writable == ALL_FIELDS
    assert capabilities.mirrored == ALL_FIELDS - {F.STATUS}
    assert F.STATUS in capabilities.owned and not capabilities.mirrored & capabilities.owned
    assert capabilities.consent_signal and not capabilities.can_create
    assert isinstance(source, WriteBackReadable)


def test_a_source_that_writes_the_status_only_does_not_mirror_anything(api):
    source = source_over(api, configured=settings({"status": "Statut"}))
    assert source.capabilities.writable == {F.STATUS}
    assert source.capabilities.mirrored == frozenset()
    assert source.read_writeback_values() == {}


# ---- the settings -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"columns": {}},
        {"columns": {"phone": "Telephone"}},  # an identity column can never be written
        {"columns": {"stage": "Etape"}},
        {"columns": {"next_action_type": "Prochaine action"}},  # a next action is two columns
        {"columns": {"next_action_due_at": "Echeance"}},
        {"columns": {"last_contacted_at": "Dernier contact"}, "propagate_opt_out": True},
        {"columns": {"status": "Statut"}, "surprise": 1},
        {"columns": {"status": "  "}},
        {"columns": {"status": "Statut", "last_contacted_at": "statut"}},  # one column twice
        {"columns": {"status": "phone"}},  # the column of another field
    ],
)
def test_a_writeback_block_that_does_not_make_sense_is_refused(block):
    with pytest.raises(ValidationError):
        SheetsSourceSettings.model_validate({"spreadsheet_id": SID, "writeback": block})


def test_a_column_named_twice_in_different_ways_is_refused():
    both = {
        "spreadsheet_id": SID,
        "column_mapping": {"prospects.status": "Etat"},
        "writeback": {"columns": {"status": "Statut"}},
    }
    with pytest.raises(ValidationError):
        SheetsSourceSettings.model_validate(both)
    same = {**both, "column_mapping": {"prospects.status": " statut "}}
    assert SheetsSourceSettings.model_validate(same).effective_column_mapping == {
        "prospects.status": "Statut"
    }


def test_the_operator_can_read_back_what_is_allowed():
    assert writeback_of({"spreadsheet_id": SID}) == (frozenset(), False)
    fields, opt_out = writeback_of(settings(propagate_opt_out=True))
    assert fields == ALL_FIELDS and opt_out is True


# ---- what is written ----------------------------------------------------------------------------


def test_the_status_is_written_in_its_own_cell_and_nothing_else_is_touched(api):
    source = source_over(api)
    add(api, "p1", Statut="new", Relances="2")
    row2 = add(api, "p2", name="Grace", phone="+216 22 111 000", Statut="new")
    row_before = [list(r) for r in api.tabs[TAB]]
    source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert writes(api) == 1
    after = api.tabs[TAB]
    changed = [
        (r, c)
        for r, (new, old) in enumerate(zip(after, row_before, strict=True))
        for c, (a, b) in enumerate(zip(new, old, strict=True))
        if a != b
    ]
    assert changed == [(1, api.headers(TAB).index("Statut"))]
    assert api.cell(TAB, 2, "Statut") == "paused" and api.cell(TAB, row2, "Statut") == "new"
    assert (
        api.cell(TAB, 2, "Relances") == "2" and api.cell(TAB, 2, "display_name") == "Ada Lovelace"
    )


def test_every_field_is_written_in_its_column_in_the_clients_time_zone(api):
    source = source_over(api)
    row = add(api, "p1", Statut="active")
    source.update_prospects(
        [
            change(
                "p1",
                {
                    F.STATUS: S.CLOSED,
                    F.LAST_CONTACTED_AT: T1,
                    F.LAST_INBOUND_AT: T2,
                    F.FOLLOW_UP_COUNT: 3,
                    F.NEXT_ACTION: (SalesActionType.FOLLOW_UP, T2),
                },
                {
                    F.STATUS: S.ACTIVE,
                    F.LAST_CONTACTED_AT: None,
                    F.LAST_INBOUND_AT: None,
                    F.FOLLOW_UP_COUNT: 0,
                    F.NEXT_ACTION: (None, None),
                },
            )
        ]
    )
    assert writes(api) == 1  # one request for the whole row
    cell = lambda header: api.cell(TAB, row, header)
    assert cell("Statut") == "closed"
    assert cell("Dernier contact") == "2026-03-02T11:30:00+01:00"
    assert cell("Derniere reponse") == "2026-03-05T09:00:00+01:00"
    assert (cell("Relances"), cell("Prochaine action")) == ("3", "follow_up")
    assert cell("Echeance") == "2026-03-05T09:00:00+01:00"


def test_what_the_sheet_holds_is_read_back_as_typed_values_and_empty_cells_as_none(api):
    source = source_over(api)
    add(api, "p1")
    add(
        api,
        "p2",
        name="Grace",
        phone="+216 22 111 000",
        **{
            "Dernier contact": "2026-03-02 11:30",
            "Relances": 4,  # a number typed by a person stays a number
            "Prochaine action": "callback",
            "Echeance": "2026-03-06 09:00",
        },
    )
    held = source.read_writeback_values()
    assert held["p1"] == {
        F.LAST_CONTACTED_AT: None,
        F.LAST_INBOUND_AT: None,
        F.FOLLOW_UP_COUNT: 0,  # an empty count reads as 0, as it always has
        F.NEXT_ACTION: (None, None),
    }
    assert held["p2"][F.LAST_CONTACTED_AT] == T1
    assert held["p2"][F.FOLLOW_UP_COUNT] == 4
    assert held["p2"][F.NEXT_ACTION][0] is SalesActionType.CALLBACK
    assert held["p2"][F.NEXT_ACTION][1] == datetime(2026, 3, 6, 8, 0, tzinfo=UTC)


def test_a_value_already_in_the_cell_is_not_sent_again(api):
    source = source_over(api)
    add(api, "p1", Statut="paused", Relances="3")
    source.update_prospects(
        [
            change(
                "p1",
                {F.STATUS: S.PAUSED, F.FOLLOW_UP_COUNT: 3},
                {F.STATUS: S.PAUSED, F.FOLLOW_UP_COUNT: 3},
            )
        ]
    )
    assert writes(api) == 0


def test_a_next_action_is_cleared_with_empty_cells(api):
    source = source_over(api)
    row = add(
        api, "p1", **{"Prochaine action": "reminder", "Echeance": "2026-03-06T09:00:00+01:00"}
    )
    due = datetime(2026, 3, 6, 8, 0, tzinfo=UTC)
    source.update_prospects(
        [
            change(
                "p1",
                {F.NEXT_ACTION: (None, None)},
                {F.NEXT_ACTION: (SalesActionType.REMINDER, due)},
            )
        ]
    )
    assert api.cell(TAB, row, "Prochaine action") == "" and api.cell(TAB, row, "Echeance") == ""


# ---- what is refused ----------------------------------------------------------------------------


def test_a_field_the_operator_did_not_allow_is_refused_before_any_request(api):
    source = source_over(api, configured=settings({"status": "Statut"}))
    add(api, "p1")
    before = len(api.calls)
    for forbidden in (F.LAST_CONTACTED_AT, F.NEXT_ACTION, F.PHONE, F.DISPLAY_NAME, F.STAGE):
        with pytest.raises(SourceReadOnlyError):
            source.update_prospects([change("p1", {forbidden: None}, {forbidden: None})])
    with pytest.raises(SourceReadOnlyError):
        source.create_prospects([])
    assert len(api.calls) == before


def test_a_person_changing_a_cell_after_it_was_read_is_never_overwritten(api):
    source = source_over(api)
    row = add(api, "p1", Statut="new")
    api.human_set(TAB, row, "Statut", "closed")  # the person decided, after Revansa read "new"
    with pytest.raises(SourceConflictError) as caught:
        source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert caught.value.fields == ("status",) and caught.value.prospect_id == "p1"
    assert "closed" not in str(caught.value) and "paused" not in str(caught.value)
    assert api.cell(TAB, row, "Statut") == "closed" and writes(api) == 0


def test_one_stale_cell_stops_the_whole_call_and_nothing_is_written(api):
    source = source_over(api)
    add(api, "p1", Statut="new")
    row2 = add(api, "p2", name="Grace", phone="+216 22 111 000", Statut="new")
    api.human_set(TAB, row2, "Statut", "opted_out")
    with pytest.raises(SourceConflictError):
        source.update_prospects(
            [
                change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW}),
                change("p2", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW}),
            ]
        )
    assert writes(api) == 0 and api.cell(TAB, 2, "Statut") == "new"


def test_a_number_a_person_typed_is_compared_by_what_it_means_not_by_how_it_looks(api):
    source = source_over(api)
    row = add(api, "p1", Relances=2.0)  # the API hands back 2.0 for a typed 2
    source.update_prospects([change("p1", {F.FOLLOW_UP_COUNT: 3}, {F.FOLLOW_UP_COUNT: 2})])
    assert api.cell(TAB, row, "Relances") == "3"


def test_an_unknown_prospect_is_an_error_and_nothing_is_written(api):
    source = source_over(api)
    add(api, "p1")
    with pytest.raises(KeyError):
        source.update_prospects([change("nope", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert writes(api) == 0


def test_an_id_on_two_rows_is_never_written_and_never_guessed(api):
    source = source_over(api)
    add(api, "p1")
    add(api, "p1", name="Someone else", phone="+216 22 111 000")
    with pytest.raises(SourceDataError):
        source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert writes(api) == 0


def test_a_prospect_appearing_twice_in_one_call_is_refused(api):
    source = source_over(api)
    add(api, "p1")
    twice = change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})
    with pytest.raises(ValueError):
        source.update_prospects([twice, twice])
    assert writes(api) == 0


# ---- a column that is not there -------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["Statut", "Relances", "Echeance", "Dernier contact"])
def test_a_named_column_that_does_not_exist_makes_the_source_not_ready_and_writes_nothing(
    api, missing
):
    headers = {k: v for k, v in HEADERS.items()}
    layout(api, headers)
    api.tabs[TAB][0] = [h for h in api.tabs[TAB][0] if h != missing]
    source = source_over(api, lay=False)
    problems = source.verify()
    assert problems and all(not p.startswith("warning:") for p in problems)
    assert any(missing in p for p in problems)
    with pytest.raises(SourceDataError):
        source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    with pytest.raises(SourceDataError):
        source.read_writeback_values()
    assert writes(api) == 0


def test_a_column_that_is_there_twice_is_not_ready_either(api):
    layout(api)
    api.tabs[TAB][0].append("Relances")
    source = source_over(api, lay=False)
    assert any("more than once" in p for p in source.verify())
    with pytest.raises(SourceDataError):
        source.update_prospects([change("p1", {F.FOLLOW_UP_COUNT: 1}, {F.FOLLOW_UP_COUNT: None})])
    assert writes(api) == 0


def test_no_column_is_ever_created(api):
    source = source_over(api)
    headers = list(api.tabs[TAB][0])
    add(api, "p1")
    source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    source.verify()
    source.read_writeback_values()
    assert api.tabs[TAB][0] == headers and api.count("add_tabs") == 0 and api.count("append") == 0


def test_the_sheets_own_columns_and_their_order_are_the_clients_to_change(api):
    """Columns are found by their header: moving them around changes nothing."""
    layout(api)
    api.tabs[TAB][0].reverse()
    source = source_over(api, lay=False)
    row = add(api, "p1", Statut="new")
    source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert api.cell(TAB, row, "Statut") == "paused"


# ---- failures, as the application's errors --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [(500, SourceUnavailableError), (429, SourceRateLimitedError), (403, SourceAuthError)],
)
def test_a_failing_google_is_told_apart_and_leaks_nothing(api, status, expected):
    source = source_over(api)
    add(api, "p1", Statut="new")
    api.fail_next("write", SheetsApiError(f"boom {PHONE}", status=status))
    with pytest.raises(expected) as caught:
        source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert PHONE not in str(caught.value) and "boom" not in str(caught.value)
    assert api.cell(TAB, 2, "Statut") == "new"
    source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])  # and again
    assert api.cell(TAB, 2, "Statut") == "paused"


# ---- isolation ------------------------------------------------------------------------------------


def test_two_spreadsheets_with_the_same_ids_are_written_separately(api):
    other_api = FakeSheetsApi()
    mine = source_over(api, source_id="sheet_a")
    theirs = source_over(other_api, source_id="sheet_b", client="client-b")
    for each in (api, other_api):
        add(each, "p1", Statut="new")
    mine.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert api.cell(TAB, 2, "Statut") == "paused" and other_api.cell(TAB, 2, "Statut") == "new"
    assert writes(other_api) == 0
    with pytest.raises(SourceConflictError):  # the other one still expects what it read
        theirs.update_prospects([change("p1", {F.STATUS: S.CLOSED}, {F.STATUS: S.PAUSED})])


# ---- the limit that cannot be removed -------------------------------------------------------------


def test_a_person_editing_between_the_check_and_the_write_is_still_overwritten(api):
    """Google Sheets has no atomic compare-and-set. The check re-reads the row and the write is a
    separate request; an edit that lands between them is lost. This test records that limit so
    nobody takes the mechanism for an atomic one: it narrows the window, it cannot close it."""
    source = source_over(api)
    row = add(api, "p1", Statut="new")
    api.before("write", lambda: api.human_set(TAB, row, "Statut", "closed"))
    source.update_prospects([change("p1", {F.STATUS: S.PAUSED}, {F.STATUS: S.NEW})])
    assert api.cell(TAB, row, "Statut") == "paused"  # the person's "closed" is gone
    assert source.capabilities.writable and not _atomic(source)


def _atomic(source) -> bool:
    return source.capabilities.atomic_compare_and_set
