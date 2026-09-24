"""Slice 4: facts persisted in the Google Sheets `Facts` tab (over an in-memory sheet)."""

import copy
import json
import logging
from datetime import timedelta
from typing import ClassVar

import pytest
from engine_harness import phone_for
from factories import NOW
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence
from sheets_support import SID, TUNIS, FakeSheetsApi, make_repository

from sales_core.contracts import (
    ClientId,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    ProspectId,
    fact_identity,
)
from sales_integrations.google_sheets import (
    GoogleSheetsApi,
    GoogleSheetsRepository,
    SheetsApiError,
    SheetTabs,
)
from sales_integrations.google_sheets.layout import FACTS, TAB_SPECS

P1 = ProspectId("p1")
CLIENT = ClientId("client-a")

HEADERS = [
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
]


def obs(key="role", value="manager", *, at=NOW, **kw):
    fields = {"status": FactStatus.DISCOVERED, "recorded_at": at} | kw
    return Fact(key=key, value=value, **fields)


def fact_row(fact=None, *, fact_id=None, client="client-a", prospect="p1", **overrides):
    """A Facts row as a person (or an older export) might have typed it."""
    fact = fact or obs()
    row = {
        "fact_id": fact_id or fact_identity(ClientId(client), ProspectId(prospect), fact),
        "client_id": client,
        "prospect_id": prospect,
        "fact_key": fact.key,
        "fact_value": fact.value,
        "fact_status": str(fact.status),
        "recorded_at": "2026-01-15T10:00:00+01:00",
    }
    return row | overrides


@pytest.fixture
def sheet():
    repo, api = make_repository(client_id="client-a")
    api.human_add_row("Prospects", prospect_id="p1", display_name="Alex", phone=phone_for("p1"))
    api.human_add_row("Prospects", prospect_id="p2", display_name="Sam", phone=phone_for("p2"))
    return repo, api


def legacy_sheet():
    """A spreadsheet set up before facts existed: the four original tabs only."""
    api = FakeSheetsApi()
    api.create_layout()
    del api.tabs["Facts"]
    api.human_add_row("Prospects", prospect_id="p1", display_name="Alex", phone=phone_for("p1"))
    repo, _ = make_repository(api, layout=False)
    return repo, api


class TestFactsTabLayout:
    def test_the_facts_tab_is_a_standard_system_tab(self):
        assert TAB_SPECS["facts"] is FACTS and FACTS.default_title == "Facts"
        assert list(FACTS.fields) == HEADERS
        assert SheetTabs().title("facts") == "Facts"

    def test_no_business_columns_only_key_value_columns(self):
        banned = ("country", "profession", "product", "sector", "team_size")
        assert not [f for f in FACTS.fields if any(word in f for word in banned)]

    def test_initialize_creates_the_facts_tab_with_its_headers(self):  # A
        repo, api = make_repository(layout=False)
        done = repo.initialize_layout()
        assert "created tab 'Facts'" in done
        assert api.headers("Facts") == HEADERS
        assert repo.verify() == []

    def test_initialize_completes_a_partial_facts_tab_to_the_right(self):
        api = FakeSheetsApi()
        api.tabs["Facts"] = [["fact_id", "my notes", "prospect_id"], ["x", "keep me", "p1"]]
        repo, api = make_repository(api, layout=False)
        repo.initialize_layout()
        assert api.headers("Facts")[:3] == ["fact_id", "my notes", "prospect_id"]
        assert api.tabs["Facts"][1][:3] == ["x", "keep me", "p1"]  # existing data untouched
        assert set(HEADERS) <= set(api.headers("Facts"))

    def test_initialize_is_idempotent_with_the_facts_tab(self):
        repo, _ = make_repository(layout=False)
        repo.initialize_layout()
        assert repo.initialize_layout() == []


class TestFactsTabVerification:
    def test_a_correct_facts_tab_verifies_clean(self):  # B
        repo, _ = make_repository()
        assert repo.verify() == []

    def test_missing_facts_columns_are_reported(self):  # B
        repo, api = make_repository()
        api.tabs["Facts"] = [["fact_id", "prospect_id"]]
        problems = repo.verify()
        assert any("'fact_key' is missing" in p for p in problems)
        assert any("'recorded_at' is missing" in p for p in problems)
        assert not any(p.startswith("warning:") for p in problems)

    def test_a_repeated_facts_header_is_reported(self):
        repo, api = make_repository()
        api.tabs["Facts"][0].append("fact_key")
        assert any("'fact_key' appears more than once" in p for p in repo.verify())

    def test_a_missing_facts_tab_alone_is_a_soft_warning_not_a_failure(self):
        repo, _ = legacy_sheet()
        (problem,) = repo.verify()
        assert problem.startswith("warning:") and "'Facts'" in problem

    def test_a_legacy_spreadsheet_is_brought_up_to_date_by_initialize(self):
        repo, _ = legacy_sheet()
        done = repo.initialize_layout()
        assert done[0] == "created tab 'Facts'" and "added columns to 'Facts'" in done[1]
        assert repo.verify() == []

    def test_a_missing_regular_tab_is_still_a_hard_failure(self):
        repo, api = make_repository()
        del api.tabs["Messages"]
        assert any("tab 'Messages' does not exist" in p for p in repo.verify())

    def test_column_mapping_accepts_facts_fields(self):
        mapping = {"facts.fact_key": "Clé", "facts.fact_value": "Valeur"}
        repo, api = make_repository(column_mapping=mapping)
        assert api.headers("Facts")[3:5] == ["Clé", "Valeur"]
        api.human_add_row("Prospects", prospect_id="p1", display_name="A", phone="+1")
        repo.append_facts(P1, [obs()])
        assert api.rows("Facts")[0]["Clé"] == "role" and api.rows("Facts")[0]["Valeur"] == "manager"
        assert repo.get_facts(P1) == [obs()]

    def test_an_unknown_facts_mapping_key_is_still_refused(self):
        with pytest.raises(ValueError, match="facts.nope"):
            make_repository(column_mapping={"facts.nope": "X"})


class TestRowsWritten:
    def test_a_fact_is_one_row_with_every_field_as_text(self, sheet):
        repo, api = sheet
        fact = obs(
            "note",
            "=1+1",
            status=FactStatus.HYPOTHESIS,
            confidence=0.35,
            evidence=FactEvidence(
                source=FactSource.HUMAN,
                message_id="m-9",
                excerpt="é",
                observed_at=NOW,
                recorded_by="op",
            ),
            updated_at=NOW + timedelta(seconds=30),
        )
        repo.append_facts(P1, [fact])
        (row,) = api.rows("Facts")
        assert row == {
            "fact_id": fact_identity(CLIENT, P1, fact),
            "client_id": "client-a",
            "prospect_id": "p1",
            "fact_key": "note",
            "fact_value": "=1+1",  # literal text, never evaluated
            "fact_status": "hypothesis",
            "confidence": "0.35",
            "evidence_source": "human",
            "evidence_message_id": "m-9",
            "evidence_excerpt": "é",
            "evidence_observed_at": "2026-01-15T10:00:00+01:00",
            "evidence_recorded_by": "op",
            "recorded_at": "2026-01-15T10:00:00+01:00",
            "updated_at": "2026-01-15T10:00:30+01:00",
        }

    def test_optional_parts_are_left_blank(self, sheet):
        repo, api = sheet
        repo.append_facts(P1, [obs()])
        (row,) = api.rows("Facts")
        assert row["confidence"] == row["evidence_source"] == row["updated_at"] == ""

    def test_facts_do_not_leak_into_the_other_tabs(self, sheet):
        repo, api = sheet
        others = {
            t: copy.deepcopy(api.tabs[t]) for t in ("Prospects", "Actions", "Messages", "Log")
        }
        repo.append_facts(P1, [obs()])
        assert {t: api.tabs[t] for t in others} == others

    def test_reordered_headers_and_a_clients_own_column_are_fine(self):
        api = FakeSheetsApi()
        api.create_layout()
        api.tabs["Facts"] = [["my column", *reversed(HEADERS)]]
        repo, api = make_repository(api, layout=False)
        api.human_add_row("Prospects", prospect_id="p1", display_name="A", phone="+1")
        repo.append_facts(P1, [obs(), obs("k", "v", at=NOW + timedelta(seconds=1))])
        assert [f.key for f in repo.get_facts(P1)] == ["role", "k"]
        assert api.rows("Facts")[0]["my column"] == ""

    def test_recorded_times_are_written_in_the_clients_zone_and_read_as_the_same_instant(
        self, sheet
    ):
        repo, api = sheet
        repo.append_facts(P1, [obs()])
        assert api.rows("Facts")[0]["recorded_at"].endswith("+01:00")
        assert repo.get_facts(P1)[0].recorded_at == NOW.astimezone(TUNIS) == NOW


class TestLegacySpreadsheets:
    def test_reading_facts_from_a_sheet_without_the_tab_is_empty_and_writes_nothing(self):
        repo, api = legacy_sheet()
        assert list(repo.get_facts(P1)) == []
        assert (api.count("write"), api.count("append"), api.count("add_tabs")) == (0, 0, 0)
        assert "Facts" not in api.tabs and repo.problems == ()

    def test_the_first_recorded_fact_creates_the_tab_by_itself(self):
        repo, api = legacy_sheet()
        repo.append_facts(P1, [obs()])
        assert api.headers("Facts") == HEADERS and len(api.rows("Facts")) == 1
        assert repo.get_facts(P1) == [obs()]
        assert repo.verify() == []

    def test_creating_the_tab_leaves_the_existing_tabs_untouched(self):
        repo, api = legacy_sheet()
        before = {t: copy.deepcopy(api.tabs[t]) for t in api.tabs}
        repo.append_facts(P1, [obs()])
        assert {t: api.tabs[t] for t in before} == before

    def test_a_blank_facts_tab_someone_created_gets_its_headers(self):
        repo, api = legacy_sheet()
        api.tabs["Facts"] = []
        repo.append_facts(P1, [obs()])
        assert api.headers("Facts") == HEADERS and len(repo.get_facts(P1)) == 1

    def test_the_existing_repository_still_works_without_the_tab(self):
        repo, _ = legacy_sheet()
        (prospect,) = repo.list_prospects()
        assert prospect.prospect_id == "p1" and repo.get_conversation(P1).messages == ()

    def test_a_refused_first_write_does_not_create_the_tab(self):
        repo, api = legacy_sheet()
        with pytest.raises(ValueError, match="recorded_at"):
            repo.append_facts(P1, [obs(at=None)])
        with pytest.raises(KeyError):
            repo.append_facts(ProspectId("nobody"), [obs()])
        assert "Facts" not in api.tabs

    def test_another_failure_while_reading_is_not_mistaken_for_a_missing_tab(self):
        repo, api = legacy_sheet()
        api.tabs["Facts"] = [HEADERS]
        api.fail_next("read", SheetsApiError("no access", status=403))
        with pytest.raises(SheetsApiError, match="no access"):
            repo.get_facts(P1)


class TestReadingIsolationAndFreshness:
    def test_another_clients_rows_in_the_tab_are_invisible_and_not_problems(self, sheet):  # D
        repo, api = sheet
        api.human_add_row("Facts", **fact_row(obs(value="theirs"), client="client-b"))
        api.human_add_row(
            "Facts", **fact_row(obs(value="ours"), client="client-a", fact_id="ours-1")
        )
        assert [f.value for f in repo.get_facts(P1)] == ["ours"]
        assert repo.problems == ()

    def test_two_clients_on_one_tab_only_ever_see_their_own_facts(self):
        api = FakeSheetsApi()
        a, _ = make_repository(api, client_id="client-a")
        b, _ = make_repository(api, client_id="client-b", layout=False)
        api.human_add_row("Prospects", prospect_id="p1", display_name="A", phone="+1")
        a.append_facts(P1, [obs(value="a's")])
        b.append_facts(P1, [obs(value="b's")])  # same fact shape, another client: its own row
        assert [f.value for f in a.get_facts(P1)] == ["a's"]
        assert [f.value for f in b.get_facts(P1)] == ["b's"]
        assert {r["client_id"] for r in api.rows("Facts")} == {"client-a", "client-b"}

    def test_reads_are_always_fresh_no_history_is_cached(self, sheet):
        repo, api = sheet
        repo.append_facts(P1, [obs("a", "1")])
        assert len(repo.get_facts(P1)) == 1
        api.human_add_row("Facts", **fact_row(obs("b", "2"), fact_id="human-1"))
        assert [f.key for f in repo.get_facts(P1)] == ["a", "b"]  # a row added elsewhere shows
        api.tabs["Facts"].pop()  # ... and one removed elsewhere is gone
        assert [f.key for f in repo.get_facts(P1)] == ["a"]

    def test_a_read_costs_one_request(self, sheet):
        repo, api = sheet
        repo.append_facts(P1, [obs()])
        before = api.count("read")
        repo.get_facts(P1)
        assert api.count("read") == before + 1 and api.count("write") == 0

    def test_a_new_process_reads_what_the_last_one_wrote(self, sheet):
        repo, api = sheet
        repo.append_facts(P1, [obs()])
        restarted, _ = make_repository(api, layout=False)
        assert restarted.get_facts(P1) == [obs()]


class TestUnreadableRows:
    BAD: ClassVar[dict[str, dict[str, str]]] = {
        "an unknown status": {"fact_status": "certain"},
        "no status": {"fact_status": ""},
        "no key": {"fact_key": ""},
        "no value": {"fact_value": ""},
        "no id": {"fact_id": ""},
        "no prospect": {"prospect_id": ""},
        "no client": {"client_id": ""},
        "no recorded time": {"recorded_at": ""},
        "a bad recorded time": {"recorded_at": "yesterday-ish"},
        "a confidence that is not a number": {"confidence": "high"},
        "a confidence above 1": {"confidence": "1.5"},
        "evidence details without a source": {"evidence_excerpt": "said so"},
        "an unknown evidence source": {"evidence_source": "rumour"},
        "an update before the recording": {"updated_at": "2026-01-15T09:00:00+01:00"},
        "a confirmed inference": {"fact_status": "confirmed", "evidence_source": "brain_inference"},
    }

    @pytest.mark.parametrize("what", sorted(BAD))
    def test_a_row_that_cannot_be_read_is_reported_and_the_others_still_load(
        self, sheet, what
    ):  # J
        repo, api = sheet
        api.human_add_row("Facts", **fact_row(obs("good", "1"), fact_id="good-1"))
        api.human_add_row("Facts", **fact_row(obs("bad", "2"), fact_id="bad-1") | self.BAD[what])
        api.human_add_row("Facts", **fact_row(obs("also good", "3"), fact_id="good-2"))
        assert [f.key for f in repo.get_facts(P1)] == ["good", "also good"]
        (problem,) = repo.problems
        assert (problem.tab, problem.row) == ("Facts", 3)

    def test_every_problem_row_is_listed_and_logged_once(self, sheet, caplog):
        repo, api = sheet
        api.human_add_row("Facts", **fact_row(fact_id="a", fact_status="???"))
        api.human_add_row("Facts", **fact_row(fact_id="b", confidence="lots"))
        with caplog.at_level(logging.WARNING):
            repo.get_facts(P1)
            repo.get_facts(P1)
        assert {p.row for p in repo.problems} == {2, 3}
        assert sum("Facts row" in r.message for r in caplog.records) == 2

    def test_a_repeated_fact_id_keeps_the_first_row_and_reports_the_rest(self, sheet):
        repo, api = sheet
        api.human_add_row("Facts", **fact_row(obs("k", "first"), fact_id="same"))
        api.human_add_row("Facts", **fact_row(obs("k", "second"), fact_id="same"))
        assert [f.value for f in repo.get_facts(P1)] == ["first"]
        (problem,) = repo.problems
        assert problem.row == 3 and "duplicate fact_id" in problem.message

    def test_problems_are_dropped_once_the_row_is_fixed(self, sheet):
        repo, api = sheet
        api.human_add_row("Facts", **fact_row(fact_status="???"))
        assert repo.get_facts(P1) == [] and repo.problems
        api.human_set("Facts", 2, "fact_status", "discovered")
        assert len(repo.get_facts(P1)) == 1 and repo.problems == ()

    def test_a_human_typed_value_and_a_number_typed_confidence_are_read(self, sheet):
        repo, api = sheet
        api.human_add_row("Facts", **fact_row(obs("k", "v"), confidence=0.5, fact_value=42))
        (fact,) = repo.get_facts(P1)
        assert fact.value == "42" and fact.confidence == 0.5

    def test_a_human_typed_status_in_any_case_is_read(self, sheet):
        repo, api = sheet
        api.human_add_row("Facts", **fact_row(fact_status=" Confirmed "))
        assert repo.get_facts(P1)[0].status is FactStatus.CONFIRMED


class TestAppendingSafely:
    def test_existing_rows_are_never_rewritten(self, sheet):
        repo, api = sheet
        repo.append_facts(P1, [obs("a", "1")])
        api.human_add_row("Facts", **fact_row(obs("h", "hand"), fact_id="h-1"))
        rows_before = copy.deepcopy(api.tabs["Facts"])
        writes_before = api.count("write")
        repo.append_facts(P1, [obs("b", "2", at=NOW + timedelta(seconds=1))])
        assert api.tabs["Facts"][: len(rows_before)] == rows_before
        assert api.count("write") == writes_before and len(api.rows("Facts")) == 3

    def test_a_row_a_person_added_a_moment_ago_is_not_overwritten(self, sheet):
        repo, api = sheet
        api.before(
            "append",
            lambda: api.human_add_row("Facts", **fact_row(obs("h", "hand"), fact_id="h-1")),
        )
        repo.append_facts(P1, [obs("a", "1")])
        assert {r["fact_key"] for r in api.rows("Facts")} == {"h", "a"}

    def test_an_identity_already_on_the_sheet_is_never_written_again_even_if_unreadable(
        self, sheet
    ):
        repo, api = sheet
        fact = obs()
        broken = fact_row(fact, fact_status="???")  # its id is the real one; the row is bad
        api.human_add_row("Facts", **broken)
        repo.append_facts(P1, [fact])
        assert len(api.rows("Facts")) == 1

    def test_an_identity_recorded_under_another_client_does_not_block_ours(self, sheet):
        repo, api = sheet
        fact = obs()
        api.human_add_row(
            "Facts", **fact_row(fact, fact_id=fact_identity(CLIENT, P1, fact), client="client-b")
        )
        repo.append_facts(P1, [fact])
        assert len(api.rows("Facts")) == 1  # the id is on the sheet: nothing is written twice

    def test_a_too_large_fact_is_refused_before_anything_is_written(self, sheet):
        repo, api = sheet
        with pytest.raises(ValueError, match="characters"):
            repo.append_facts(P1, [obs("ok"), obs("huge", "x" * 60_000)])
        assert api.rows("Facts") == [] and api.count("append") == 0

    def test_a_failed_append_writes_nothing_and_the_retry_records_it_once(self, sheet):  # N
        repo, api = sheet
        api.fail_next("append", SheetsApiError("boom", status=500))
        with pytest.raises(SheetsApiError):
            repo.append_facts(P1, [obs()])
        assert api.rows("Facts") == []
        repo.append_facts(P1, [obs()])
        repo.append_facts(P1, [obs()])
        assert len(api.rows("Facts")) == 1

    def test_a_retry_after_an_append_whose_outcome_was_lost_does_not_duplicate(self, sheet):
        """The rows landed but the caller saw an error (a dropped connection): it just retries."""
        repo, api = sheet
        repo.append_facts(P1, [obs("a", "1"), obs("b", "2")])
        repo.append_facts(P1, [obs("a", "1"), obs("b", "2")])
        assert len(api.rows("Facts")) == 2 and api.count("append") == 1

    def test_writes_for_one_client_never_carry_another_clients_id(self, sheet):
        repo, api = sheet
        repo.append_facts(P1, [obs()])
        assert {r["client_id"] for r in api.rows("Facts")} == {"client-a"}

    def test_only_prospects_of_this_client_can_have_facts(self):
        api = FakeSheetsApi()
        repo, _ = make_repository(api, client_id="client-a")
        with pytest.raises(KeyError, match="p1"):
            repo.append_facts(P1, [obs()])


class TestGoogleRetryBehaviourReachesFacts:
    """The adapter's real API wrapper, driven at HTTP level, for the facts operations."""

    @staticmethod
    def make(*responses):
        http = HttpMockSequence(list(responses))
        service = build("sheets", "v4", http=http, static_discovery=True)
        sleeps: list[float] = []
        api = GoogleSheetsApi(service, sleep=sleeps.append)
        repo = GoogleSheetsRepository(api, client_id=CLIENT, spreadsheet_id=SID, timezone=TUNIS)
        return repo, http, sleeps

    @staticmethod
    def ok(body=None):
        return ({"status": "200"}, json.dumps(body if body is not None else {}))

    @staticmethod
    def fail(status):
        body = {"error": {"code": status, "message": "nope"}}
        return ({"status": str(status)}, json.dumps(body))

    @staticmethod
    def values(rows):
        return {"valueRanges": [{"values": rows}]}

    def prospects(self):
        return self.ok(self.values([
            ["prospect_id", "display_name", "phone", "status", "stage", "next_action_type",
             "next_action_due_at", "last_contacted_at", "last_inbound_at", "follow_up_count"],
            ["p1", "Alex", "+15550000001", "new", "", "", "", "", "", "0"],
        ]))  # fmt: skip

    def facts(self, *rows):
        return self.ok(self.values([HEADERS, *rows]))

    def test_a_read_is_retried_on_429_and_5xx(self):
        repo, http, sleeps = self.make(self.fail(429), self.fail(503), self.facts())
        assert repo.get_facts(P1) == []
        assert len(http.request_sequence) == 3 and sleeps == [1.0, 2.0]

    def test_an_append_is_retried_on_429_only_and_never_after_a_5xx(self):
        repo, http, sleeps = self.make(self.prospects(), self.facts(), self.fail(429), self.ok())
        repo.append_facts(P1, [obs()])
        assert len(http.request_sequence) == 4 and sleeps == [1.0]

        repo, http, _ = self.make(self.prospects(), self.facts(), self.fail(500), self.ok())
        with pytest.raises(SheetsApiError) as raised:
            repo.append_facts(P1, [obs()])
        assert raised.value.status == 500 and len(http.request_sequence) == 3

    def test_a_dropped_connection_on_a_read_is_retried(self):
        class Flaky(HttpMockSequence):
            dropped = False

            def request(self, *args, **kwargs):
                if not self.dropped:
                    self.dropped = True
                    raise ConnectionResetError("reset")
                return super().request(*args, **kwargs)

        http = Flaky([self.facts()])
        service = build("sheets", "v4", http=http, static_discovery=True)
        sleeps: list[float] = []
        api = GoogleSheetsApi(service, sleep=sleeps.append)
        repo = GoogleSheetsRepository(api, client_id=CLIENT, spreadsheet_id=SID, timezone=TUNIS)
        assert repo.get_facts(P1) == [] and sleeps == [1.0]
