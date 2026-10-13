"""The pure Google Sheets source: people read from a spreadsheet, nothing else, nothing written.

Deterministic. The spreadsheet is `FakeSheetsApi`, an in-memory grid: it stands for a real Google
spreadsheet and proves nothing about a real Google call, credential or quota. The adapter, the
wiring reader and the source are the real ones.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from sheets_support import SID, TUNIS, FakeSheetsApi

from sales_app.sourcing import (
    ProblemCode,
    ProspectChange,
    ProspectField,
    ProspectSource,
    SheetsProspectSource,
    SourceAuthError,
    SourceDataError,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
)
from sales_app.wiring.providers.google_sheets import SheetsSourceSettings, build_prospect_source
from sales_core.contracts import ProspectStatus
from sales_integrations.google_sheets import SheetsApiError

CLIENT = "client-a"
PHONE = "+216 99 888 777"
OTHER = "+216 22 111 000"
EMAIL = "leak.me@example.com"
TAB = "Prospects"


def add(api, pid, name="Ada Lovelace", phone=PHONE, **cells):
    return api.human_add_row(TAB, prospect_id=pid, display_name=name, phone=phone, **cells)


def source_over(api, *, source_id="sheet_a", layout=True, **kwargs) -> SheetsProspectSource:
    if layout:
        api.create_layout()
    return build_prospect_source(
        api,
        client_id=CLIENT,
        source_id=source_id,
        settings={"spreadsheet_id": SID},
        timezone=TUNIS,
        **kwargs,
    )


@pytest.fixture
def api():
    return FakeSheetsApi()


# ---- the contract ----------------------------------------------------------------------------


def test_it_is_a_prospect_source_owning_people_and_the_sheets_status(api):
    source = source_over(api)
    assert isinstance(source, ProspectSource)
    capabilities = source.capabilities
    assert ProspectField.STATUS in capabilities.owned
    for revansas in (
        ProspectField.STAGE,
        ProspectField.NEXT_ACTION,
        ProspectField.LAST_CONTACTED_AT,
        ProspectField.LAST_INBOUND_AT,
        ProspectField.FOLLOW_UP_COUNT,
        ProspectField.CONVERSATION_ID,
        ProspectField.TELEGRAM_CHAT_ID,
        ProspectField.LANE_ID,
    ):
        assert revansas not in capabilities.owned
    assert capabilities.writable == frozenset()
    assert capabilities.id_namespace == "sheet_a"
    assert source.client_id == CLIENT


def test_a_namespace_must_look_like_a_provider_key(api):
    with pytest.raises(ValueError):
        source_over(api, source_id="Sheet A!")


def test_it_reads_the_people_of_the_sheet_by_their_native_ids(api):
    source = source_over(api, source_id=None)
    add(api, "p1", status="active")
    add(api, "weird id/7", name="Bob", phone=OTHER)
    listed = {p.prospect_id: p for p in source.list_prospects()}
    assert set(listed) == {"p1", "weird id/7"}  # a historical id is never validated or rewritten
    assert listed["p1"].display_name == "Ada Lovelace"
    assert listed["p1"].contact.phone == PHONE
    assert source.get_prospect("p1") == listed["p1"]
    assert source.get_prospect("nobody") is None


def test_it_finds_a_person_by_phone_or_email_and_not_by_a_telegram_chat(api):
    source = source_over(api)
    add(api, "p1", email=EMAIL, telegram_chat_id="777")
    assert source.find_prospect_by_contact(phone="21699888777").prospect_id == "p1"
    assert source.find_prospect_by_contact(email=EMAIL.upper()).prospect_id == "p1"
    assert source.find_prospect_by_contact(phone="+216 00 000 000") is None
    assert source.find_prospect_by_contact() is None


# ---- people only: Revansa's own columns are not read ----------------------------------------


def test_what_revansa_keeps_in_its_own_columns_is_ignored_and_neutral(api):
    source = source_over(api, source_id=None)
    add(
        api,
        "p1",
        status="closed",
        stage="negotiation",
        conversation_id="conv-9",
        telegram_chat_id="777",
        last_contacted_at="2026-01-10 09:00",
        follow_up_count=3,
        next_action_type="follow_up",
        next_action_due_at="2026-01-20 09:00",
    )
    (person,) = source.list_prospects()
    assert person.status is ProspectStatus.CLOSED  # a human's word in the sheet binds
    assert person.stage is None and person.conversation_id is None
    assert person.contact.telegram_chat_id is None and person.lane_id is None
    assert person.schedule.last_contacted_at is None
    assert person.schedule.follow_up_count == 0
    assert person.schedule.next_action_type is None and person.schedule.next_action_due_at is None
    assert person.contact.phone == PHONE


def test_the_extra_columns_of_the_client_are_kept_as_attributes(api):
    api.create_layout()
    api.tabs[TAB][0].append("Company")
    source = source_over(api, layout=False)
    add(api, "p1", Company="Acme")
    assert source.list_prospects()[0].attributes == {"Company": "Acme"}


# ---- read-only -------------------------------------------------------------------------------


def test_every_write_is_refused_without_a_single_request(api):
    source = source_over(api)
    add(api, "p1")
    source.list_prospects()
    api.calls.clear()
    person = source.list_prospects()[0]
    api.calls.clear()
    with pytest.raises(SourceReadOnlyError):
        source.create_prospects([person])
    with pytest.raises(SourceReadOnlyError):
        source.update_prospects(
            [
                ProspectChange(
                    prospect_id="p1",
                    new={ProspectField.STATUS: ProspectStatus.PAUSED},
                    expected={ProspectField.STATUS: ProspectStatus.NEW},
                )
            ]
        )
    assert api.calls == []


def test_reading_never_writes_to_the_sheet_even_for_a_row_with_no_id(api):
    source = source_over(api)
    add(api, "p1")
    api.human_add_row(TAB, display_name="No Id", phone=OTHER)
    before = [list(row) for row in api.tabs[TAB]]
    ids = [p.prospect_id for p in source.list_prospects()]
    source.get_prospect("p1")
    source.find_prospect_by_contact(phone=OTHER)
    assert ids == ["p1"]  # the row with no id is not read, and is not given one
    assert api.count("write") == 0 and api.count("append") == 0 and api.count("add_tabs") == 0
    assert api.tabs[TAB] == before


def test_an_operator_may_ask_for_missing_ids_to_be_given(api):
    source = source_over(api, assign_missing_ids=True)
    api.human_add_row(TAB, display_name="No Id", phone=OTHER)
    assert len(source.list_prospects()) == 1
    assert api.count("write") == 1


def test_only_the_prospects_tab_is_read(api):
    source = source_over(api)
    add(api, "p1")
    del api.tabs["Actions"], api.tabs["Messages"], api.tabs["Log"]  # a sheet with people only
    api.calls.clear()
    assert [p.prospect_id for p in source.list_prospects()] == ["p1"]
    assert api.count("read") == 1


# ---- namespaced ids and the fail-closed rules ------------------------------------------------


def test_an_id_outside_the_alphabet_is_left_out_and_reported_when_the_source_has_a_namespace(api):
    source = source_over(api)
    add(api, "ok-1")
    add(api, "bad:id", name="Bob", phone=OTHER)
    assert [p.prospect_id for p in source.list_prospects()] == ["ok-1"]
    assert source.get_prospect("bad:id") is None
    problems = source.problems()
    assert [p.code for p in problems] == [ProblemCode.INVALID_NATIVE_ID]
    assert all(p.native_id is None for p in problems)


def test_a_repeated_id_and_a_repeated_phone_are_left_out_and_described_without_data(api):
    source = source_over(api)
    add(api, "dup", name="Ada")
    add(api, "dup", name="Bea", phone=OTHER)
    add(api, "p3", name="Dina", phone="+216 55 444 333")
    add(api, "p2", name="Cleo", phone="+216 55 444 333")  # the phone of the row above
    assert [p.prospect_id for p in source.list_prospects()] == ["p3"]
    codes = {p.code for p in source.problems()}
    assert codes == {ProblemCode.DUPLICATE_ID, ProblemCode.DUPLICATE_CONTACT}
    for problem in source.problems():
        text = f"{problem!r} {problem.detail}"
        assert not any(
            word in text for word in ("'dup'", "'p2'", "'p3'", "Ada", "Bea", "216", EMAIL)
        )


def test_a_row_that_cannot_be_interpreted_is_left_out_and_reported(api):
    source = source_over(api)
    add(api, "p1", status="not-a-status")
    add(api, "p2", name="Bob", phone=OTHER)
    assert [p.prospect_id for p in source.list_prospects()] == ["p2"]
    assert [p.code for p in source.problems()] == [ProblemCode.UNREADABLE_RECORD]


def test_a_sheet_that_holds_no_refusal_signal_for_left_out_rows_reports_none(api):
    source = source_over(api)
    add(api, "p1", status="not-a-status")
    source.list_prospects()
    assert source.refused_contacts() == ()


def test_the_repr_leaks_nothing(api):
    source = source_over(api)
    add(api, "p1")
    source.list_prospects()
    assert "Ada" not in repr(source) and PHONE not in repr(source)
    assert "Ada" not in repr(source._reader) and PHONE not in repr(source._reader)


# ---- errors are translated -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, SourceAuthError),
        (401, SourceAuthError),
        (429, SourceRateLimitedError),
        (500, SourceUnavailableError),
        (None, SourceUnavailableError),
    ],
)
def test_a_failure_of_the_api_becomes_a_source_error_with_a_fixed_sentence(api, status, expected):
    source = source_over(api)
    add(api, "p1")
    api.fail_next("read", SheetsApiError(f"secret {PHONE} detail", status=status))
    with pytest.raises(expected) as raised:
        source.list_prospects()
    assert PHONE not in str(raised.value) and "secret" not in str(raised.value)


def test_a_missing_prospects_tab_is_a_data_error_never_an_empty_list(api):
    source = source_over(api)
    api.tabs.pop(TAB)
    api.fail_next("read", SheetsApiError("no tab", status=400))
    with pytest.raises(SourceUnavailableError):
        source.list_prospects()
    source = source_over(api, layout=False)
    with pytest.raises((SourceDataError, SourceUnavailableError)):
        source.list_prospects()


# ---- verification looks at the prospects tab only --------------------------------------------


def test_verify_ignores_the_tabs_of_a_store_the_spreadsheet_may_not_have(api):
    source = source_over(api)
    for tab in ("Actions", "Messages", "Log", "Facts"):
        api.tabs.pop(tab, None)
    assert source.verify() == []


def test_verify_says_when_the_prospects_tab_is_missing_or_misses_a_column(api):
    source = source_over(api)
    api.tabs[TAB][0].remove("phone")
    assert any("phone" in issue for issue in source.verify())
    api.tabs.pop(TAB)
    assert source.verify()


def test_settings_reject_a_mapping_for_a_tab_that_is_not_the_prospects(api):
    with pytest.raises(ValueError):
        SheetsSourceSettings(spreadsheet_id=SID, column_mapping={"actions.status": "State"})
