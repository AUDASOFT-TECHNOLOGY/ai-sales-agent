"""The spreadsheet and Telegram: where a prospect's chat is kept, and what happens to a sheet that
was set up before there was a place for it. Simulated Sheets API (an in-memory grid)."""

from dataclasses import replace

import pytest
from engine_harness import prospect
from sheets_support import make_repository

from sales_core.contracts import ContactInfo, ProspectId, ProspectStatus

LEGACY_HEADERS = [
    "prospect_id",
    "display_name",
    "phone",
    "email",
    "preferred_channel",
    "locale",
    "status",
    "stage",
    "next_action_type",
    "next_action_due_at",
    "conversation_id",
    "last_contacted_at",
    "last_inbound_at",
    "follow_up_count",
    "Budget",  # a column of the client's own, which must stay where it is
]


def legacy_sheet():
    repo, api = make_repository(prospect_headers=LEGACY_HEADERS)
    api.human_add_row(
        "Prospects",
        prospect_id="p1",
        display_name="Alex",
        phone="+15550000001",
        status="new",
        Budget="5000",
    )
    return repo, api


def link(repo, pid="p1", chat="424242"):
    current = repo.get_prospect(ProspectId(pid))
    repo.update_prospects(
        [replace(current, contact=replace(current.contact, telegram_chat_id=chat))]
    )


class TestASheetWithoutTheColumn:
    def test_the_column_is_added_to_the_right_when_the_first_chat_is_written(self):
        repo, api = legacy_sheet()
        assert "telegram_chat_id" not in api.headers("Prospects")
        link(repo)
        headers = api.headers("Prospects")
        assert headers[: len(LEGACY_HEADERS)] == LEGACY_HEADERS  # nothing moved
        assert headers[-1] == "telegram_chat_id"
        assert repo.get_prospect(ProspectId("p1")).contact.telegram_chat_id == "424242"

    def test_the_link_is_never_silently_lost(self):
        repo, _ = legacy_sheet()
        link(repo)
        assert repo.find_prospect_by_contact(telegram_chat_id="424242").prospect_id == "p1"

    def test_the_clients_own_data_is_untouched(self):
        repo, api = legacy_sheet()
        link(repo)
        row = api.rows("Prospects")[0]
        assert row["Budget"] == "5000" and row["phone"] == "+15550000001"
        assert repo.get_prospect(ProspectId("p1")).attributes == {"Budget": "5000"}

    def test_the_column_is_added_once(self):
        repo, api = legacy_sheet()
        api.human_add_row("Prospects", prospect_id="p2", display_name="Sam", status="new")
        link(repo, "p1", "1001")
        link(repo, "p2", "1002")
        assert api.headers("Prospects").count("telegram_chat_id") == 1

    def test_an_unknown_sender_created_from_a_chat_is_kept_with_its_chat(self):
        repo, api = legacy_sheet()
        repo.create_prospects(
            [
                prospect(
                    "inbound-2026-01-15-tg515151",
                    contact=ContactInfo(telegram_chat_id="515151"),
                    status=ProspectStatus.ACTIVE,
                )
            ]
        )
        found = repo.find_prospect_by_contact(telegram_chat_id="515151")
        assert found.prospect_id == "inbound-2026-01-15-tg515151"
        assert api.rows("Prospects")[-1]["telegram_chat_id"] == "515151"

    def test_a_sheet_that_does_not_use_telegram_is_never_given_the_column(self):
        repo, api = legacy_sheet()
        current = repo.get_prospect(ProspectId("p1"))
        repo.update_prospects([replace(current, status=ProspectStatus.ACTIVE)])
        assert "telegram_chat_id" not in api.headers("Prospects")


class TestTheColumnIsTheClients:
    def test_it_may_be_renamed_like_any_other(self):
        repo, api = make_repository(
            column_mapping={"prospects.telegram_chat_id": "Telegram"},
            prospect_headers=[*LEGACY_HEADERS, "Telegram"],
        )
        api.human_add_row("Prospects", prospect_id="p1", display_name="Alex", status="new")
        link(repo)
        assert api.rows("Prospects")[0]["Telegram"] == "424242"

    def test_a_renamed_column_is_created_under_its_name(self):
        repo, api = make_repository(
            column_mapping={"prospects.telegram_chat_id": "Telegram"},
            prospect_headers=LEGACY_HEADERS,
        )
        api.human_add_row("Prospects", prospect_id="p1", display_name="Alex", status="new")
        link(repo)
        assert api.headers("Prospects")[-1] == "Telegram"

    def test_a_chat_id_stored_as_a_number_by_the_sheet_is_still_the_same_chat(self):
        repo, api = make_repository()
        api.human_add_row(
            "Prospects",
            prospect_id="p1",
            display_name="Alex",
            status="new",
            telegram_chat_id=424242,
        )
        assert repo.find_prospect_by_contact(telegram_chat_id="424242").prospect_id == "p1"

    def test_two_rows_cannot_claim_the_same_chat_when_one_is_created(self):
        repo, api = make_repository()
        api.human_add_row(
            "Prospects", prospect_id="p1", display_name="Alex", status="new", telegram_chat_id="1"
        )
        with pytest.raises(ValueError, match="chat is already in use"):
            repo.create_prospects([prospect("p2", contact=ContactInfo(telegram_chat_id="1"))])
