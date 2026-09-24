"""Behaviour specific to the Google Sheets adapter: layout, human edits, conflicts, caching."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from factories import CLIENT, NOW, PROSPECT, make_action, make_message
from sheets_support import FakeSheetsApi, make_repository

from sales_core.contracts import (
    ActionId,
    ActionPriority,
    ActionStatus,
    AuditEvent,
    Channel,
    MessageDraft,
    ProspectId,
    ProspectStatus,
    SalesActionType,
)
from sales_integrations.google_sheets import (
    SheetConflictError,
    SheetDataError,
    SheetLayoutError,
    SheetsApiError,
    SheetTabs,
    extract_spreadsheet_id,
)

PID = ProspectId("p1")


def add_prospect(api, pid="p1", **cells):
    values = {"prospect_id": pid, "display_name": "Alex", "phone": "+15550000001", "status": "new"}
    return api.human_add_row("Prospects", **(values | cells))


def approved_action(repo, api, **kw):
    """One PROPOSED action in the sheet, as the Engine would leave it."""
    action = make_action(status=ActionStatus.PROPOSED, draft=MessageDraft(text="Hello there"), **kw)
    repo.save_actions([action])
    return action


class TestLayout:
    def test_columns_are_found_by_header_so_humans_may_reorder_and_add_their_own(self):
        api = FakeSheetsApi()
        headers = ["Notes", "phone", "prospect_id", "Budget", "display_name", "status", "stage",
                   "next_action_type", "next_action_due_at", "last_contacted_at",
                   "last_inbound_at", "follow_up_count"]  # fmt: skip
        repo, api = make_repository(api, prospect_headers=headers)
        api.human_add_row("Prospects", prospect_id="p1", display_name="Alex", phone="+1555",
                          Notes="VIP", Budget="10k")  # fmt: skip
        (prospect,) = repo.list_prospects()
        assert prospect.display_name == "Alex" and prospect.status is ProspectStatus.NEW
        assert dict(prospect.attributes) == {"Notes": "VIP", "Budget": "10k"}

    def test_extra_columns_survive_updates_untouched(self):
        headers = ["Notes", "prospect_id", "display_name", "phone", "status", "stage",
                   "next_action_type", "next_action_due_at", "last_contacted_at",
                   "last_inbound_at", "follow_up_count"]  # fmt: skip
        repo, api = make_repository(prospect_headers=headers)
        api.human_add_row(
            "Prospects", prospect_id="p1", display_name="A", phone="+1", Notes="keep me"
        )
        current = repo.get_prospect(PID)
        api.human_set("Prospects", 2, "Notes", "edited meanwhile")  # a human edits another cell
        repo.update_prospects([replace(current, stage="warm")])
        assert api.cell("Prospects", 2, "Notes") == "edited meanwhile"
        assert api.cell("Prospects", 2, "stage") == "warm"

    def test_header_matching_ignores_case_and_spacing(self):
        api = FakeSheetsApi()
        headers = ["Prospect_ID", " Display  Name ", "PHONE", "Status", "Stage", "next_action_type",
                   "next_action_due_at", "last_contacted_at", "last_inbound_at", "follow_up_count"]  # fmt: skip
        mapping = {"prospects.display_name": "display name"}
        repo, api = make_repository(api, prospect_headers=headers, column_mapping=mapping)
        api.human_add_row(
            "Prospects", **{"Prospect_ID": "p1", " Display  Name ": "Alex", "PHONE": "+1"}
        )
        assert repo.list_prospects()[0].display_name == "Alex"

    def test_column_mapping_lets_a_client_keep_its_own_headers(self):
        mapping = {
            "prospects.phone": "Téléphone",
            "prospects.display_name": "Nom",
            "actions.status": "État",
        }
        repo, api = make_repository(column_mapping=mapping)
        api.human_add_row(
            "Prospects", prospect_id="p1", Nom="Alex", Téléphone="+1555", status="new"
        )
        assert repo.list_prospects()[0].contact.phone == "+1555"
        repo.save_actions([make_action()])
        assert api.rows("Actions")[0]["État"] == "planned"

    def test_custom_tab_titles(self):
        tabs = SheetTabs(prospects="Leads", actions="À faire", messages="Chat", log="Journal")
        repo, api = make_repository(tabs=tabs)
        add = api.human_add_row(
            "Leads", prospect_id="p1", display_name="A", phone="+1", status="new"
        )
        assert add == 2 and len(repo.list_prospects()) == 1
        repo.append_audit_events(
            [AuditEvent(timestamp=NOW, client_id=CLIENT, event_type="a.b", actor="core")]
        )
        assert len(api.rows("Journal")) == 1

    def test_missing_required_columns_are_all_reported_at_once(self):
        repo, _api = make_repository(
            prospect_headers=["prospect_id", "display_name", "phone", "status"]
        )
        with pytest.raises(SheetLayoutError) as raised:
            repo.list_prospects()
        text = str(raised.value)
        for column in ("stage", "next_action_type", "last_contacted_at", "follow_up_count"):
            assert f"'{column}' is missing" in text

    def test_optional_columns_may_be_absent(self):
        headers = ["prospect_id", "display_name", "phone", "status", "stage", "next_action_type",
                   "next_action_due_at", "last_contacted_at", "last_inbound_at", "follow_up_count"]  # fmt: skip
        repo, api = make_repository(prospect_headers=headers)  # no email, locale, conversation_id
        add_prospect(api)
        assert repo.list_prospects()[0].contact.email is None

    def test_an_empty_header_row_and_duplicate_headers_are_layout_errors(self):
        repo, api = make_repository()
        api.tabs["Prospects"] = []
        with pytest.raises(SheetLayoutError, match="no header row"):
            repo.list_prospects()
        repo, api = make_repository(prospect_headers=["prospect_id", "prospect_id", "display_name", "phone", "status",
                                                      "stage", "next_action_type", "next_action_due_at",
                                                      "last_contacted_at", "last_inbound_at", "follow_up_count"])  # fmt: skip
        with pytest.raises(SheetLayoutError, match="more than once"):
            repo.list_prospects()

    def test_a_missing_tab_is_an_api_error_that_says_what_to_check(self):
        repo, api = make_repository()
        del api.tabs["Prospects"]
        with pytest.raises(SheetsApiError):
            repo.list_prospects()

    @pytest.mark.parametrize(
        "mapping", [{"prospects.nope": "X"}, {"phone": "Tel"}, {"prospects.phone": " "}]
    )
    def test_column_mapping_keys_are_validated(self, mapping):
        with pytest.raises(ValueError, match="column_mapping"):
            make_repository(column_mapping=mapping)

    def test_spreadsheet_id_from_a_url_or_bare(self):
        url = "https://docs.google.com/spreadsheets/d/1AbC-dEf_9/edit?gid=0#gid=0"
        assert extract_spreadsheet_id(url) == "1AbC-dEf_9"
        assert extract_spreadsheet_id("  1AbC-dEf_9 ") == "1AbC-dEf_9"
        with pytest.raises(ValueError, match="neither"):
            extract_spreadsheet_id("not a sheet!")


class TestReadingWhatHumansType:
    def test_values_are_read_forgivingly(self):
        repo, api = make_repository()
        add_prospect(api, "p1", status=" Active ", phone=21612345678, stage="Warm",
                     next_action_type="Follow_Up", next_action_due_at="2026-01-20 09:00",
                     follow_up_count=2.0, preferred_channel="WhatsApp", locale="fr")  # fmt: skip
        (p,) = repo.list_prospects()
        assert p.status is ProspectStatus.ACTIVE and p.contact.phone == "21612345678"
        assert p.schedule.next_action_type is SalesActionType.FOLLOW_UP
        assert p.schedule.next_action_due_at == datetime(
            2026, 1, 20, 8, 0, tzinfo=UTC
        )  # 09:00 in Tunis
        assert p.schedule.follow_up_count == 2 and p.contact.preferred_channel is Channel.WHATSAPP
        assert p.stage == "Warm" and p.locale == "fr"

    def test_a_blank_status_means_new(self):
        repo, api = make_repository()
        add_prospect(api, status="")
        assert repo.list_prospects()[0].status is ProspectStatus.NEW

    @pytest.mark.parametrize(
        "typed",
        [
            "2026-01-20T09:00:00+01:00",
            "2026-01-20 09:00",
            "2026-01-20T08:00:00Z",
            46042.375,  # how Sheets stores a date typed as a date: 20 Jan 2026, 09:00
        ],
    )
    def test_dates_are_accepted_in_every_form_a_human_or_sheets_produces(self, typed):
        repo, api = make_repository()
        add_prospect(api, next_action_type="callback", next_action_due_at=typed)
        assert repo.list_prospects()[0].schedule.next_action_due_at == datetime(
            2026, 1, 20, 8, 0, tzinfo=UTC
        )

    def test_date_only_means_midnight_local_time(self):
        repo, api = make_repository()
        add_prospect(api, next_action_type="callback", next_action_due_at="2026-01-20")
        due = repo.list_prospects()[0].schedule.next_action_due_at
        assert due == datetime(2026, 1, 19, 23, 0, tzinfo=UTC)

    def test_blank_rows_and_rows_with_only_client_columns_are_skipped(self):
        headers = ["Notes", "prospect_id", "display_name", "phone", "status", "stage", "next_action_type",
                   "next_action_due_at", "last_contacted_at", "last_inbound_at", "follow_up_count"]  # fmt: skip
        repo, api = make_repository(prospect_headers=headers)
        api.human_add_row("Prospects", Notes="just a note")
        api.tabs["Prospects"].append([])
        api.human_add_row("Prospects", prospect_id="p1", display_name="A", phone="+1")
        assert [p.prospect_id for p in repo.list_prospects()] == ["p1"]


class TestProspectIds:
    def test_rows_without_an_id_get_a_stable_generated_one(self):
        repo, api = make_repository(id_factory=iter(["p-aaa", "p-bbb"]).__next__)
        api.human_add_row("Prospects", display_name="Alex", phone="+1555", status="new")
        first = repo.list_prospects()
        assert [p.prospect_id for p in first] == ["p-aaa"]
        assert api.cell("Prospects", 2, "prospect_id") == "p-aaa"  # written back to the sheet
        assert [p.prospect_id for p in repo.list_prospects()] == ["p-aaa"]  # and stable

    def test_only_rows_with_something_to_identify_them_get_an_id(self):
        repo, api = make_repository()
        api.human_add_row("Prospects", status="new")
        assert repo.list_prospects() == [] and api.cell("Prospects", 2, "prospect_id") == ""

    def test_backfill_can_be_switched_off(self):
        repo, api = make_repository(assign_missing_ids=False)
        api.human_add_row("Prospects", display_name="Alex", phone="+1")
        assert repo.list_prospects() == [] and api.count("write") == 0


class TestBadRowsFailClosed:
    def test_unreadable_rows_are_excluded_and_reported_never_guessed(self, caplog):
        repo, api = make_repository()
        add_prospect(api, "ok")
        add_prospect(api, "bad-status", status="maybe")
        add_prospect(api, "bad-date", next_action_type="follow_up", next_action_due_at="soon")
        add_prospect(api, "half", next_action_type="follow_up")  # a type without a date
        with caplog.at_level(logging.WARNING):
            assert [p.prospect_id for p in repo.list_prospects()] == ["ok"]
        problems = {p.row: p.message for p in repo.problems}
        assert set(problems) == {3, 4, 5}
        assert (
            "status" in problems[3]
            and "next_action_due_at" in problems[4]
            and "together" in problems[5]
        )
        assert "excluded" in caplog.text

    def test_problems_are_logged_once_not_on_every_read(self, caplog):
        repo, api = make_repository()
        add_prospect(api, "bad", status="maybe")
        with caplog.at_level(logging.WARNING):
            repo.list_prospects()
            repo.list_prospects()
        assert caplog.text.count("excluded") == 1

    def test_duplicate_ids_exclude_every_row_that_shares_them(self):
        repo, api = make_repository()
        add_prospect(api, "dup", display_name="A")
        add_prospect(api, "dup", display_name="B")
        add_prospect(api, "fine")
        assert [p.prospect_id for p in repo.list_prospects()] == ["fine"]
        assert repo.get_prospect(ProspectId("dup")) is None  # so the Engine will not contact either
        with pytest.raises(SheetDataError, match="several rows"):
            repo.update_prospects(
                [replace(repo.list_prospects()[0], prospect_id=ProspectId("dup"))]
            )

    def test_an_unreadable_action_row_is_excluded_from_lists_but_its_key_stays_reserved(self):
        repo, api = make_repository()
        repo.save_actions([make_action(idempotency_key="k1")])
        api.human_set("Actions", 2, "status", "aprove")  # a typo
        assert repo.list_actions() == []
        assert {p.row for p in repo.problems} == {2}
        with pytest.raises(ValueError, match="already in use"):
            repo.save_actions([make_action(action_id=ActionId("a-2"), idempotency_key="k1")])
        with pytest.raises(SheetDataError, match="k1"):
            repo.find_action_by_idempotency_key("k1")  # never "not found" for a key that exists

    def test_duplicate_action_ids_are_excluded(self):
        repo, api = make_repository()
        repo.save_actions([make_action()])
        api.human_add_row("Actions", **api.rows("Actions")[0])
        assert repo.list_actions() == []

    def test_a_row_of_another_type_of_data_never_leaks_a_partial_object(self):
        repo, api = make_repository()
        repo.save_actions([make_action()])
        api.human_set("Actions", 2, "due_at", "whenever")
        assert repo.list_actions() == [] and "due_at" in repo.problems[0].message


class TestWritingValues:
    def test_everything_is_stored_as_text_so_nothing_is_evaluated(self):
        repo, api = make_repository()
        repo.append_messages([make_message(text='=HYPERLINK("http://evil")')])
        repo.save_actions([make_action(draft=MessageDraft(text="=1+1"))])
        assert api.rows("Messages")[0]["text"] == '=HYPERLINK("http://evil")'
        assert api.rows("Actions")[0]["draft_text"] == "=1+1"
        assert all(isinstance(v, str) for row in api.rows("Actions") for v in row.values())

    def test_dates_are_written_in_the_clients_local_time_with_an_offset(self):
        repo, api = make_repository()
        repo.save_actions([make_action(due_at=NOW)])
        assert api.rows("Actions")[0]["due_at"] == "2026-01-15T10:00:00+01:00"

    def test_a_draft_and_its_template_round_trip(self):
        repo, api = make_repository()
        draft = MessageDraft(
            text="Bonjour", language="fr", template_id="welcome", template_params={"1": "Alex"}
        )
        repo.save_actions([make_action(draft=draft, priority=ActionPriority.URGENT)])
        (action,) = repo.list_actions()
        assert action.draft == draft and action.priority is ActionPriority.URGENT
        row = api.rows("Actions")[0]
        assert row["priority"] == "urgent" and row["draft_template_params"] == '{"1": "Alex"}'

    def test_a_next_action_travels_with_the_action(self):
        repo, _api = make_repository()
        due = NOW + timedelta(days=3)
        repo.save_actions(
            [
                make_action(
                    next_stage="warm",
                    next_action_type=SalesActionType.CALLBACK,
                    next_action_due_at=due,
                )
            ]
        )
        (action,) = repo.list_actions()
        assert (action.next_stage, action.next_action_type, action.next_action_due_at) == (
            "warm",
            SalesActionType.CALLBACK,
            due,
        )

    def test_too_long_values_are_refused_before_anything_is_written(self):
        repo, api = make_repository()
        with pytest.raises(ValueError, match="at most"):
            repo.save_actions([make_action(draft=MessageDraft(text="x" * 60_000))])
        assert api.count("write") == 0 and api.rows("Actions") == []

    def test_new_rows_follow_the_sheets_own_column_order(self):
        repo, api = make_repository()
        api.tabs["Actions"] = [
            list(reversed(api.headers("Actions")))
        ]  # a human reordered the columns
        repo.save_actions([make_action(idempotency_key="k9")])
        assert api.rows("Actions")[0]["idempotency_key"] == "k9"
        repo.append_audit_events(
            [
                AuditEvent(
                    timestamp=NOW,
                    client_id=CLIENT,
                    event_type="a.b",
                    actor="core",
                    details={"k": "v"},
                )
            ]
        )
        assert api.rows("Log")[0]["details"] == '{"k": "v"}'

    def test_only_changed_cells_are_written(self):
        repo, api = make_repository()
        add_prospect(api)
        current = repo.get_prospect(PID)
        api.calls.clear()
        repo.update_prospects([replace(current, stage="warm")])
        (write,) = [detail for name, detail in api.calls if name == "write"]
        assert write == ["'Prospects'!H2"]  # the stage cell, nothing else

    def test_an_unchanged_update_writes_nothing(self):
        repo, api = make_repository()
        add_prospect(api)
        current = repo.get_prospect(PID)
        api.calls.clear()
        repo.update_prospects([current])
        assert api.count("write") == 0

    def test_engine_state_columns_round_trip(self):
        repo, api = make_repository()
        add_prospect(api)
        current = repo.get_prospect(PID)
        due = NOW + timedelta(days=5)
        schedule = replace(current.schedule, next_action_type=SalesActionType.REMINDER, next_action_due_at=due,
                           last_contacted_at=NOW, last_inbound_at=NOW, follow_up_count=3)  # fmt: skip
        repo.update_prospects(
            [replace(current, status=ProspectStatus.ACTIVE, stage="s", schedule=schedule)]
        )
        again = repo.get_prospect(PID)
        assert again.schedule == schedule and again.status is ProspectStatus.ACTIVE


class TestHumanEditsAreNeverOverwritten:
    def test_a_rejection_during_dispatch_wins_and_nothing_is_sent(self):
        repo, api = make_repository()
        approved = approved_action(repo, api)
        api.human_set("Actions", 2, "status", "approved")
        (seen,) = repo.list_actions({ActionStatus.APPROVED})
        api.human_set("Actions", 2, "status", "rejected")  # the human changes their mind just now
        with pytest.raises(SheetConflictError, match="status"):
            repo.save_actions([replace(seen, status=ActionStatus.SENDING)])
        assert api.cell("Actions", 2, "status") == "rejected"
        assert approved.action_id == seen.action_id

    def test_recording_a_failure_does_not_touch_the_status_a_human_set(self):
        repo, api = make_repository()
        approved_action(repo, api)
        (seen,) = repo.list_actions()
        api.human_set("Actions", 2, "status", "rejected")
        repo.save_actions([replace(seen, attempts=1, last_error="model down")])  # status untouched
        assert api.cell("Actions", 2, "status") == "rejected"
        assert api.cell("Actions", 2, "last_error") == "model down"

    def test_an_opt_out_typed_during_a_run_is_not_reverted_by_the_run(self):
        repo, api = make_repository()
        add_prospect(api)
        seen = repo.get_prospect(PID)
        api.human_set("Prospects", 2, "status", "opted_out")
        with pytest.raises(SheetConflictError):
            repo.update_prospects([replace(seen, status=ProspectStatus.ACTIVE)])
        assert api.cell("Prospects", 2, "status") == "opted_out"

    def test_unrelated_changes_go_through_and_keep_the_humans_edit(self):
        repo, api = make_repository()
        add_prospect(api)
        seen = repo.get_prospect(PID)
        api.human_set("Prospects", 2, "status", "opted_out")
        schedule = replace(seen.schedule, last_contacted_at=NOW, follow_up_count=1)
        repo.update_prospects([replace(seen, schedule=schedule)])  # the run's send bookkeeping
        assert api.cell("Prospects", 2, "status") == "opted_out"
        assert api.cell("Prospects", 2, "follow_up_count") == "1"

    def test_a_conflict_writes_nothing_at_all(self):
        repo, api = make_repository()
        add_prospect(api, "p1")
        add_prospect(api, "p2", phone="+15550000002")
        one, two = repo.list_prospects()
        api.human_set("Prospects", 3, "status", "paused")
        api.calls.clear()
        with pytest.raises(SheetConflictError):
            repo.update_prospects(
                [replace(one, stage="a"), replace(two, status=ProspectStatus.ACTIVE)]
            )
        assert api.count("write") == 0 and api.cell("Prospects", 2, "stage") == ""

    def test_a_human_who_already_made_the_same_change_is_not_a_conflict(self):
        repo, api = make_repository()
        add_prospect(api)
        seen = repo.get_prospect(PID)
        api.human_set("Prospects", 2, "status", "active")
        repo.update_prospects([replace(seen, status=ProspectStatus.ACTIVE)])
        assert api.cell("Prospects", 2, "status") == "active"

    def test_rows_are_found_by_id_even_after_a_human_sorts_the_sheet(self):
        repo, api = make_repository()
        add_prospect(api, "p1")
        add_prospect(api, "p2", phone="+15550000002")
        seen = repo.get_prospect(PID)
        api.tabs["Prospects"][1:] = list(reversed(api.tabs["Prospects"][1:]))  # sorted
        repo.update_prospects([replace(seen, stage="warm")])
        assert (
            api.cell("Prospects", api.find_row("Prospects", "prospect_id", "p1"), "stage") == "warm"
        )
        assert api.cell("Prospects", api.find_row("Prospects", "prospect_id", "p2"), "stage") == ""

    def test_a_human_edited_draft_is_what_the_next_read_returns(self):
        repo, api = make_repository()
        approved_action(repo, api)
        api.human_set("Actions", 2, "draft_text", "Edited by a person")
        api.human_set("Actions", 2, "status", "APPROVED")
        (action,) = repo.list_actions()
        assert action.status is ActionStatus.APPROVED and action.draft.text == "Edited by a person"

    def test_editing_the_draft_never_discards_the_engines_other_fields(self):
        repo, api = make_repository()
        due = NOW + timedelta(days=4)
        repo.save_actions(
            [
                make_action(
                    draft=MessageDraft(text="Hi"),
                    next_stage="s",
                    next_action_type=SalesActionType.FOLLOW_UP,
                    next_action_due_at=due,
                )
            ]
        )
        (seen,) = repo.list_actions()
        api.human_set("Actions", 2, "next_stage", "human-stage")
        repo.save_actions([replace(seen, status=ActionStatus.SENDING)])
        assert api.cell("Actions", 2, "next_stage") == "human-stage"
        assert api.cell("Actions", 2, "status") == "sending"


class TestCallBudget:
    def test_updates_cost_one_read_and_one_write(self):
        repo, api = make_repository()
        add_prospect(api)
        seen = repo.get_prospect(PID)
        api.calls.clear()
        repo.update_prospects([replace(seen, stage="x")])
        assert [name for name, _ in api.calls] == ["read", "write"]

    def test_saving_new_and_existing_actions_is_a_read_an_append_and_a_write(self):
        repo, api = make_repository()
        repo.save_actions([make_action()])
        (seen,) = repo.list_actions()
        api.calls.clear()
        repo.save_actions(
            [
                replace(seen, status=ActionStatus.APPROVED),
                make_action(action_id=ActionId("a-2"), idempotency_key="k2"),
            ]
        )
        assert [name for name, _ in api.calls] == ["read", "append", "write"]

    def test_a_whole_plan_is_one_read_and_one_append(self):
        repo, api = make_repository()
        api.calls.clear()
        repo.save_actions(
            [make_action(action_id=ActionId(f"a-{i}"), idempotency_key=f"k{i}") for i in range(20)]
        )
        assert [name for name, _ in api.calls] == ["read", "append"]
        assert len(api.rows("Actions")) == 20


class TestMessageHistoryIsAlwaysRead:
    """History is never served from memory: the sheet is the only source of truth."""

    def test_every_read_goes_to_the_sheet_so_new_rows_show_up_at_once(self):
        repo, api = make_repository()
        assert repo.get_conversation(PROSPECT).messages == ()
        api.human_add_row(
            "Messages",
            message_id="m-9",
            conversation_id="c-1",
            prospect_id="p-1",
            direction="inbound",
            channel="whatsapp",
            timestamp="2026-01-15T10:00:00+01:00",
            text="typed by a person",
        )
        assert [m.text for m in repo.get_conversation(PROSPECT).messages] == ["typed by a person"]

    def test_appending_checks_for_duplicates_against_the_current_sheet(self):
        repo, api = make_repository()
        api.human_add_row(
            "Messages",
            message_id="m-1",
            conversation_id="c-1",
            prospect_id="p-1",
            direction="inbound",
            channel="whatsapp",
            timestamp="2026-01-15T10:00:00+01:00",
            text="already there",
        )
        with pytest.raises(ValueError, match="already exists"):
            repo.append_messages([make_message(message_id="m-1")])

    def test_a_retry_after_an_append_of_unknown_outcome_cannot_duplicate_rows(self):
        repo, api = make_repository()
        original = api.append

        def applied_then_timed_out(sid, range_, rows):
            original(sid, range_, rows)
            raise TimeoutError("the reply never arrived")

        api.append = applied_then_timed_out
        with pytest.raises(TimeoutError):
            repo.append_messages([make_message(message_id="m-1")])
        api.append = original
        with pytest.raises(ValueError, match="already exists"):  # the retry sees the row
            repo.append_messages([make_message(message_id="m-1")])
        assert len(api.rows("Messages")) == 1

    def test_duplicate_and_unreadable_message_rows_are_excluded(self):
        repo, api = make_repository()
        repo.append_messages([make_message(message_id="m-1")])
        api.human_add_row("Messages", **api.rows("Messages")[0])
        api.human_add_row(
            "Messages",
            message_id="m-x",
            conversation_id="c-1",
            prospect_id="p-1",
            direction="sideways",
            channel="whatsapp",
            timestamp="2026-01-15T10:00:00+01:00",
        )
        assert [m.message_id for m in repo.get_conversation(PROSPECT).messages] == ["m-1"]
        assert {p.row for p in repo.problems} == {3, 4}

    def test_inconsistent_conversation_ids_are_reported_not_hidden(self):
        repo, api = make_repository()
        repo.append_messages([make_message(message_id="m-1")])
        api.human_add_row(
            "Messages",
            message_id="m-2",
            conversation_id="other",
            prospect_id="p-1",
            direction="inbound",
            channel="whatsapp",
            timestamp="2026-01-15T10:00:00+01:00",
            text="x",
        )
        with pytest.raises(SheetDataError, match="inconsistent"):
            repo.get_conversation(PROSPECT)


class TestOnboarding:
    def test_a_correct_spreadsheet_verifies_clean(self):
        repo, _ = make_repository()
        assert repo.verify() == []

    def test_verify_reports_every_missing_tab_and_column(self):
        repo, api = make_repository()
        del api.tabs["Log"]
        api.tabs["Actions"] = [["action_id", "status"]]
        problems = repo.verify()
        assert any("tab 'Log' does not exist" in p for p in problems)
        assert any("'idempotency_key' is missing" in p for p in problems)

    def test_a_time_zone_mismatch_is_a_soft_warning(self):
        repo, api = make_repository()
        api.timezone = "America/New_York"
        (problem,) = repo.verify()
        assert problem.startswith("warning:") and "America/New_York" in problem

    def test_verify_reports_an_api_failure_instead_of_raising(self):
        repo, api = make_repository()
        api.fail_next("metadata", SheetsApiError("no access", status=403))
        assert repo.verify() == ["no access"]

    def test_initialize_builds_a_blank_spreadsheet(self):
        repo, api = make_repository(layout=False)
        done = repo.initialize_layout()
        assert len(done) == 10 and repo.verify() == []  # five tabs, five header rows
        assert api.headers("Prospects")[0] == "prospect_id"

    def test_initialize_adds_columns_to_an_existing_client_sheet_without_touching_it(self):
        api = FakeSheetsApi()
        api.tabs["Prospects"] = [["Name", "Mobile"], ["Alex", "+1555"]]
        mapping = {"prospects.display_name": "Name", "prospects.phone": "Mobile"}
        repo, api = make_repository(api, layout=False, column_mapping=mapping)
        repo.initialize_layout()
        assert api.tabs["Prospects"][1][:2] == ["Alex", "+1555"]  # existing data untouched
        assert api.headers("Prospects")[:2] == ["Name", "Mobile"]
        assert "prospect_id" in api.headers("Prospects") and "follow_up_count" in api.headers(
            "Prospects"
        )
        assert repo.verify() == []

    def test_initialize_is_idempotent(self):
        repo, _ = make_repository(layout=False)
        repo.initialize_layout()
        assert repo.initialize_layout() == []

    def test_a_new_spreadsheet_is_immediately_usable(self):
        repo, api = make_repository(layout=False)
        repo.initialize_layout()
        api.human_add_row("Prospects", display_name="Alex", phone="+1555", status="new")
        (prospect,) = repo.list_prospects()
        assert prospect.prospect_id.startswith("p-")
