"""The spreadsheet is live: people add, edit, sort and delete rows all day, agent running or not.

Every behaviour here follows from one rule: the sheet is the only source of truth and is read
afresh on every call. There is no import step, no local copy and no cache to go stale.
"""

from dataclasses import replace

import pytest
from factories import make_action, make_message
from sheets_support import make_repository

from sales_core.contracts import (
    ActionId,
    ActionStatus,
    ProspectId,
    ProspectStatus,
)
from sales_integrations.google_sheets import SheetConflictError


def add(api, pid="p1", **cells):
    n = "".join(c for c in pid if c.isdigit()) or "0"
    values = {
        "prospect_id": pid,
        "display_name": f"Name {pid}",
        "phone": f"+1555000{int(n):04d}",
        "status": "new",
    }
    return api.human_add_row("Prospects", **(values | cells))


def ids(prospects):
    return [p.prospect_id for p in prospects]


class TestNewRowsAreDiscovered:
    def test_a_row_added_at_any_time_is_visible_on_the_very_next_read(self):
        repo, api = make_repository()
        add(api, "p1")
        assert ids(repo.list_prospects()) == ["p1"]
        add(api, "p2")
        add(api, "p3")
        assert ids(repo.list_prospects()) == ["p1", "p2", "p3"]
        assert repo.get_prospect(ProspectId("p3")).display_name == "Name p3"
        assert repo.find_prospect_by_contact(phone="+15550000003").prospect_id == "p3"

    def test_a_fixed_typo_makes_a_row_eligible_at_the_next_read(self):
        repo, api = make_repository()
        add(api, "p1", status="acitve")
        assert repo.list_prospects() == [] and len(repo.problems) == 1
        api.human_set("Prospects", 2, "status", "active")
        assert ids(repo.list_prospects()) == ["p1"] and repo.problems == ()

    def test_actions_and_history_added_by_other_means_are_seen_too(self):
        repo, api = make_repository()
        assert repo.list_actions() == []
        api.human_add_row(
            "Actions",
            action_id="a-x",
            prospect_id="p1",
            action_type="callback",
            due_at="2026-01-15T10:00:00+01:00",
            status="approved",
            idempotency_key="manual:1",
            draft_text="Call back",
        )
        (action,) = repo.list_actions()
        assert action.action_id == "a-x" and action.status is ActionStatus.APPROVED


class TestNoDuplicatesAndStableIds:
    def test_reading_repeatedly_never_multiplies_or_renames_prospects(self):
        repo, api = make_repository(id_factory=iter(f"p-gen{i}" for i in range(100)).__next__)
        api.human_add_row("Prospects", display_name="No id yet", phone="+15550009999", status="new")
        add(api, "p1")
        first = ids(repo.list_prospects())
        for _ in range(5):
            assert ids(repo.list_prospects()) == first
        assert len(api.rows("Prospects")) == 2  # the sheet gained no rows

    def test_ids_assigned_by_the_agent_survive_new_rows_sorting_and_restarts(self):
        repo, api = make_repository()
        api.human_add_row("Prospects", display_name="Generated", phone="+15550009999", status="new")
        (generated,) = repo.list_prospects()
        add(api, "p1")
        api.tabs["Prospects"][1:] = list(reversed(api.tabs["Prospects"][1:]))  # a person sorts
        api.tabs["Prospects"].insert(1, [""] * len(api.headers("Prospects")))  # and inserts a row
        restarted, _ = make_repository(api, layout=False)  # a new process
        by_name = {p.display_name: p.prospect_id for p in restarted.list_prospects()}
        assert by_name["Generated"] == generated.prospect_id

    def test_a_write_lands_on_the_right_row_however_the_sheet_was_rearranged(self):
        repo, api = make_repository()
        for pid in ("p1", "p2", "p3"):
            add(api, pid)
        seen = repo.get_prospect(ProspectId("p2"))
        api.tabs["Prospects"][1:] = list(reversed(api.tabs["Prospects"][1:]))
        api.tabs["Prospects"].insert(1, [""] * len(api.headers("Prospects")))
        repo.update_prospects([replace(seen, stage="warm")])
        assert len(api.rows("Prospects")) == 3  # updated in place, nothing appended
        assert {r["prospect_id"]: r["stage"] for r in api.rows("Prospects")} == {
            "p1": "",
            "p2": "warm",
            "p3": "",
        }

    def test_the_same_id_on_two_rows_is_never_guessed(self):
        repo, api = make_repository()
        add(api, "dup", display_name="A", phone="+15550000001")
        add(api, "dup", display_name="B", phone="+15550000002")
        add(api, "ok", phone="+15550000003")
        assert ids(repo.list_prospects()) == ["ok"]
        assert repo.get_prospect(ProspectId("dup")) is None

    def test_the_same_person_added_twice_is_contacted_once(self):
        repo, api = make_repository()
        add(api, "p1", phone="+216 12 345 678")
        add(api, "p2", phone="21612345678")  # same number, different formatting and id
        assert ids(repo.list_prospects()) == ["p1"]  # the first row wins
        (problem,) = repo.problems
        assert problem.row == 3 and "same phone number as row 2" in problem.message
        assert repo.find_prospect_by_contact(phone="+216-12-345-678").prospect_id == "p1"

    def test_a_duplicate_of_an_opted_out_person_is_not_contacted_either(self):
        repo, api = make_repository()
        add(api, "p1", phone="+21612345678", status="opted_out")
        add(api, "p2", phone="+21612345678", status="new")
        assert [p.status for p in repo.list_prospects()] == [ProspectStatus.OPTED_OUT]

    def test_a_person_who_fixes_the_number_makes_the_second_row_a_new_prospect(self):
        repo, api = make_repository()
        add(api, "p1", phone="+21612345678")
        add(api, "p2", phone="+21612345678")
        assert ids(repo.list_prospects()) == ["p1"]
        api.human_set("Prospects", 3, "phone", "+21698765432")
        assert ids(repo.list_prospects()) == ["p1", "p2"]


class TestRowsChangedByPeople:
    def test_edits_are_reflected_on_the_next_read(self):
        repo, api = make_repository()
        add(api, "p1")
        api.human_set("Prospects", 2, "display_name", "Renamed")
        api.human_set("Prospects", 2, "status", "paused")
        api.human_set("Prospects", 2, "phone", "+15551112222")
        seen = repo.get_prospect(ProspectId("p1"))
        assert seen.display_name == "Renamed" and seen.status is ProspectStatus.PAUSED
        assert repo.find_prospect_by_contact(phone="+15551112222").prospect_id == "p1"
        assert repo.find_prospect_by_contact(phone="+15550000001") is None  # the old number is gone

    def test_history_belongs_to_the_prospect_id_not_to_the_phone_or_the_row(self):
        repo, api = make_repository()
        add(api, "p-1", phone="+15550000001")
        repo.append_messages([make_message(message_id="m-1"), make_message(message_id="m-2")])
        api.human_set("Prospects", 2, "phone", "+15559999999")
        api.tabs["Prospects"].insert(1, [""] * len(api.headers("Prospects")))
        conversation = repo.get_conversation(ProspectId("p-1"))
        assert [m.message_id for m in conversation.messages] == ["m-1", "m-2"]

    def test_a_deleted_prospect_disappears_but_its_history_is_kept_and_returns_with_it(self):
        repo, api = make_repository()
        add(api, "p-1")
        repo.append_messages([make_message(message_id="m-1")])
        removed = api.tabs["Prospects"].pop(1)
        assert repo.list_prospects() == [] and repo.get_prospect(ProspectId("p-1")) is None
        assert len(repo.get_conversation(ProspectId("p-1")).messages) == 1  # history is not lost
        api.tabs["Prospects"].append(removed)  # the same person is added back with the same id
        assert repo.get_prospect(ProspectId("p-1")) is not None
        assert len(repo.get_conversation(ProspectId("p-1")).messages) == 1

    def test_updating_a_prospect_that_was_deleted_meanwhile_fails_loudly(self):
        repo, api = make_repository()
        add(api, "p1")
        seen = repo.get_prospect(ProspectId("p1"))
        api.tabs["Prospects"].pop(1)
        with pytest.raises(KeyError):
            repo.update_prospects([replace(seen, stage="x")])


class TestConcurrentAdditions:
    def test_a_row_added_to_actions_while_the_agent_inserts_is_not_overwritten(self):
        repo, api = make_repository()
        repo.save_actions([make_action(idempotency_key="k1")])
        api.before(
            "append",
            lambda: api.human_add_row(
                "Actions",
                action_id="manual",
                prospect_id="p9",
                action_type="callback",
                due_at="2026-01-15T10:00:00+01:00",
                status="proposed",
                idempotency_key="manual:1",
            ),
        )
        repo.save_actions([make_action(action_id=ActionId("a-2"), idempotency_key="k2")])
        assert {r["action_id"] for r in api.rows("Actions")} == {"a-1", "manual", "a-2"}

    def test_a_prospect_added_at_the_bottom_while_the_agent_writes_is_untouched(self):
        repo, api = make_repository()
        add(api, "p1")
        seen = repo.get_prospect(ProspectId("p1"))
        api.before("write", lambda: add(api, "p2"))
        repo.update_prospects([replace(seen, stage="warm")])
        assert {r["prospect_id"]: r["stage"] for r in api.rows("Prospects")} == {
            "p1": "warm",
            "p2": "",
        }

    def test_new_history_rows_are_appended_after_rows_a_person_added(self):
        repo, api = make_repository()
        api.before(
            "append",
            lambda: api.human_add_row(
                "Messages",
                message_id="typed",
                conversation_id="c-1",
                prospect_id="p-1",
                direction="inbound",
                channel="whatsapp",
                timestamp="2026-01-15T10:00:00+01:00",
                text="typed just now",
            ),
        )
        repo.append_messages([make_message(message_id="m-1")])
        assert [r["message_id"] for r in api.rows("Messages")] == ["typed", "m-1"]


class TestChangeDetectionIsScopedToWhatWasRead:
    def test_reading_one_prospect_does_not_hide_a_concurrent_change_to_another(self):
        repo, api = make_repository()
        add(api, "p1")
        add(api, "p2")
        _, second = repo.list_prospects()  # the agent's view of p2
        api.human_set("Prospects", 3, "status", "opted_out")  # a person opts p2 out
        repo.get_prospect(ProspectId("p1"))  # an unrelated read must not refresh p2's baseline
        with pytest.raises(SheetConflictError):
            repo.update_prospects([replace(second, status=ProspectStatus.ACTIVE)])
        assert api.cell("Prospects", 3, "status") == "opted_out"

    def test_a_filtered_action_read_does_not_refresh_the_actions_it_did_not_return(self):
        repo, api = make_repository()
        repo.save_actions(
            [
                make_action(
                    action_id=ActionId("a-1"), idempotency_key="k1", status=ActionStatus.APPROVED
                ),
                make_action(
                    action_id=ActionId("a-2"), idempotency_key="k2", status=ActionStatus.PROPOSED
                ),
            ]
        )
        two = next(a for a in repo.list_actions() if a.action_id == "a-2")
        api.human_set("Actions", api.find_row("Actions", "action_id", "a-2"), "status", "rejected")
        repo.list_actions({ActionStatus.APPROVED})  # returns only a-1
        with pytest.raises(SheetConflictError):
            repo.save_actions([replace(two, status=ActionStatus.APPROVED)])

    def test_baselines_of_rows_that_no_longer_exist_are_dropped(self):
        repo, api = make_repository()
        add(api, "p1")
        add(api, "p2", phone="+15550000002")
        repo.list_prospects()
        api.tabs["Prospects"].pop(2)
        repo.list_prospects()
        assert set(repo._prospect_snaps) == {"p1"}


def test_idempotency_keys_stay_unique_however_the_sheet_changes():
    repo, api = make_repository()
    repo.save_actions([make_action(idempotency_key="client-a:p1:first_contact:2026-01-15")])
    restarted, _ = make_repository(api, layout=False)
    clash = make_action(
        action_id=ActionId("another"), idempotency_key="client-a:p1:first_contact:2026-01-15"
    )
    with pytest.raises(ValueError, match="already in use"):
        restarted.save_actions([clash])
