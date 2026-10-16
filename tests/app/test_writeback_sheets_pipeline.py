"""Revansa's results reaching a client's Google Sheet, with nothing mocked between the pieces.

Real here: the Sheets source and adapter, the composite repository, the SQL operational store, the
consent registry (files), the durable SQL ledger and the synchroniser. Simulated: the Google Sheets
API (`FakeSheetsApi`, an in-memory grid) and the clock. A restart is a restart: the database and the
sheets stay, everything in memory is rebuilt. Nothing here proves a real Google call, credential or
quota works.
"""

import itertools
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from consent_support import Clock, make_world
from consent_support import contact as consent_contact
from sheets_support import SID, TUNIS, FakeSheetsApi

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.persistence.writeback_ledger_rows import SqlWriteBackLedgerRows
from sales_app.sourcing import ProspectField, ProspectState
from sales_app.sourcing.composite import CompositeRepository
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_app.sourcing.writeback import (
    DurableWriteBackLedger,
    FailureCode,
    Outcome,
    SkipReason,
    WriteBackPolicy,
    WriteBackSyncer,
)
from sales_app.wiring.providers.google_sheets import build_prospect_source, writeback_of
from sales_core.contracts import ProspectId, ProspectStatus, SalesActionType, Scheduling
from sales_integrations.google_sheets import SheetsApiError

S = ProspectStatus
F = ProspectField
A, B = "client-a", "client-b"
TAB = "Prospects"
PHONE_1, PHONE_2 = "+216 99 100 001", "+216 99 100 002"
HEADERS = {
    "status": "Statut",
    "last_contacted_at": "Dernier contact",
    "last_inbound_at": "Derniere reponse",
    "follow_up_count": "Relances",
    "next_action_type": "Prochaine action",
    "next_action_due_at": "Echeance",
}
SETTINGS = {"spreadsheet_id": SID, "writeback": {"columns": HEADERS}}
SENT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)  # 10:00 in Tunis
DUE = datetime(2026, 3, 4, 8, 0, tzinfo=UTC)  # 09:00 in Tunis


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{(tmp_path / 'ops.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    yield tmp_path, url
    engine.dispose()


def new_sheet(*people):
    """A spreadsheet laid out with the client's own headers, and its people (id, name, phone)."""
    api = FakeSheetsApi()
    api.create_layout({f"prospects.{key}": header for key, header in HEADERS.items()})
    for pid, name, phone in people:
        api.human_add_row(TAB, prospect_id=pid, display_name=name, phone=phone, Statut="new")
    return api


class Hooked:
    """A source that lets a test act between the synchroniser's read and its write."""

    def __init__(self, source):
        self._source = source
        self.after_read = None

    def __getattr__(self, name):
        return getattr(self._source, name)

    def read_writeback_values(self):
        values = self._source.read_writeback_values()
        if self.after_read is not None:
            action, self.after_read = self.after_read, None
            action()
        return values


class Stack:
    """One client's whole stack over some spreadsheets. `restart()` rebuilds everything in memory."""

    def __init__(self, tmp_path, url, client_id, sheets, *, settings=None, dry_run=False):
        self.tmp_path, self.url, self.client_id = tmp_path, url, client_id
        self.sheets = sheets  # source id -> FakeSheetsApi
        self.settings = settings or SETTINGS
        self.dry_run = dry_run
        self.world = make_world(tmp_path, client_id=client_id)
        self.clock = Clock()
        self.runs = itertools.count(1)
        self.engine = None
        self.start()

    def start(self):
        fields, opt_out = writeback_of(self.settings)
        self.policy = WriteBackPolicy(fields=fields, propagate_opt_out=opt_out)
        self.engine = create_database_engine(self.url)
        self.store = SqlOperationalStore(SqlOperationalRows(self.engine, self.client_id))
        self.sources = {
            source_id: Hooked(
                build_prospect_source(
                    api,
                    client_id=self.client_id,
                    source_id=source_id,
                    settings=self.settings,
                    timezone=TUNIS,
                )
            )
            for source_id, api in self.sheets.items()
        }
        self.repository = CompositeRepository(self.sources, self.store, self.world.registry)
        self.ledger = DurableWriteBackLedger(SqlWriteBackLedgerRows(self.engine, self.client_id))
        self.syncers = {
            source_id: WriteBackSyncer(
                source,
                self.store,
                self.world.registry,
                self.ledger,
                self.policy,
                clock=self.clock,
                new_run_id=lambda: f"run{next(self.runs)}",
                dry_run=self.dry_run,
            )
            for source_id, source in self.sources.items()
        }

    def restart(self):
        self.engine.dispose()
        self.start()

    def sync(self, source_id=None):
        return self.syncers[source_id or next(iter(self.syncers))].run_once()

    def revansa(self, public_id, *, status=None, **schedule):
        """What the Engine does after a send or a reply: its own state, through the repository."""
        current = self.repository.get_prospect(ProspectId(public_id))
        self.repository.update_prospects(
            [
                replace(
                    current,
                    status=status or current.status,
                    schedule=replace(current.schedule, **schedule),
                )
            ]
        )

    def state(self, public_id, **schedule):
        """Revansa's state, written straight to the store: for a test whose sheets cannot all be
        read through the repository."""
        self.store.save_states(
            [
                ProspectState(
                    client_id=self.client_id,
                    prospect_id=ProspectId(public_id),
                    schedule=Scheduling(**schedule),
                )
            ]
        )

    def ledger_of(self, public_id, field):
        return [e for e in self.ledger.entries(ProspectId(public_id)) if e.field is field]


def cell(api, row, header):
    return api.cell(TAB, row, header)


def sheet_with_one():
    return new_sheet(("p1", "Ada Lovelace", PHONE_1))


@pytest.fixture
def stack(database):
    tmp_path, url = database
    api = sheet_with_one()
    made = Stack(tmp_path, url, A, {"sheet_a": api})
    made.api = api
    yield made
    made.engine.dispose()


# ---- Revansa's results reach the sheet -------------------------------------------------------------


def test_what_happened_after_a_send_reaches_the_clients_sheet_and_nothing_else_does(stack):
    api = stack.api
    api.human_set(TAB, 2, "display_name", "Ada Lovelace")
    before = [list(r) for r in api.tabs[TAB]]
    stack.revansa(
        "sheet_a:p1",
        status=S.ACTIVE,
        last_contacted_at=SENT,
        follow_up_count=1,
        next_action_type=SalesActionType.FOLLOW_UP,
        next_action_due_at=DUE,
    )
    report = stack.sync()
    assert (report.applied, report.conflicts, report.failed, report.stopped) == (3, 0, 0, None)
    assert cell(api, 2, "Dernier contact") == "2026-03-01T10:00:00+01:00"
    assert cell(api, 2, "Relances") == "1"
    assert (cell(api, 2, "Prochaine action"), cell(api, 2, "Echeance")) == (
        "follow_up",
        "2026-03-04T09:00:00+01:00",
    )
    # An active status is Revansa's alone: a CRM cannot say "in touch", and `new` would only loosen.
    assert cell(api, 2, "Statut") == "new"
    mirrored = {
        HEADERS[k]
        for k in ("last_contacted_at", "follow_up_count", "next_action_type", "next_action_due_at")
    }
    headers = api.headers(TAB)
    for r, (now, then) in enumerate(zip(api.tabs[TAB], before, strict=True)):
        for c, (a, b) in enumerate(zip(now, then, strict=True)):
            assert a == b or (r == 1 and headers[c] in mirrored)


def test_a_restriction_decided_in_revansa_is_written_as_the_status_in_the_same_pass(stack):
    stack.revansa("sheet_a:p1", status=S.PAUSED, follow_up_count=2)
    report = stack.sync()
    assert report.applied == 2
    assert cell(stack.api, 2, "Statut") == "paused" and cell(stack.api, 2, "Relances") == "2"


def test_a_second_pass_writes_nothing_and_asks_for_nothing_it_has_not_got_to(stack):
    stack.revansa("sheet_a:p1", last_contacted_at=SENT, follow_up_count=1)
    stack.sync()
    calls = stack.api.count("write")
    again = stack.sync()
    assert again.planned == 0 and again.applied == 0
    assert again.skipped[SkipReason.ALREADY_APPLIED] == 2
    assert stack.api.count("write") == calls and len(stack.ledger.entries()) == 2


def test_the_sheet_is_untouched_when_revansa_has_nothing_to_say(stack):
    report = stack.sync()
    assert report.planned == 0 and stack.api.count("write") == 0 and stack.ledger.entries() == ()


# ---- a person's own cells are never overwritten ----------------------------------------------------


def test_a_later_date_a_larger_count_and_their_own_next_action_are_left_alone(stack):
    api = stack.api
    api.human_set(TAB, 2, "Dernier contact", "2026-03-10 12:00")
    api.human_set(TAB, 2, "Relances", 7)
    api.human_set(TAB, 2, "Prochaine action", "callback")
    api.human_set(TAB, 2, "Echeance", "2026-03-20 09:00")
    stack.revansa(
        "sheet_a:p1",
        last_contacted_at=SENT,
        follow_up_count=2,
        next_action_type=SalesActionType.FOLLOW_UP,
        next_action_due_at=DUE,
    )
    report = stack.sync()
    assert report.planned == 0 and api.count("write") == 0
    assert (
        report.skipped[SkipReason.NOT_ADVANCING] == 2 and report.skipped[SkipReason.OCCUPIED] == 1
    )
    assert cell(api, 2, "Dernier contact") == "2026-03-10 12:00" and cell(api, 2, "Relances") == 7
    assert cell(api, 2, "Prochaine action") == "callback"


def test_an_earlier_date_and_a_smaller_count_are_advanced(stack):
    api = stack.api
    api.human_set(TAB, 2, "Dernier contact", "2026-02-01 09:00")
    api.human_set(TAB, 2, "Relances", 1)
    stack.revansa("sheet_a:p1", last_contacted_at=SENT, follow_up_count=3)
    assert stack.sync().applied == 2
    assert cell(api, 2, "Relances") == "3"
    assert cell(api, 2, "Dernier contact") == "2026-03-01T10:00:00+01:00"


def test_a_next_action_revansa_wrote_follows_it_and_is_cleared_with_it(stack):
    api = stack.api
    stack.revansa("sheet_a:p1", next_action_type=SalesActionType.FOLLOW_UP, next_action_due_at=DUE)
    stack.sync()
    later = DUE + timedelta(days=3)
    stack.revansa("sheet_a:p1", next_action_type=SalesActionType.CALLBACK, next_action_due_at=later)
    assert stack.sync().applied == 1
    assert cell(api, 2, "Prochaine action") == "callback"
    stack.revansa(
        "sheet_a:p1", next_action_type=None, next_action_due_at=None
    )  # the sale is closed
    assert stack.sync().applied == 1
    assert (cell(api, 2, "Prochaine action"), cell(api, 2, "Echeance")) == ("", "")


def test_a_next_action_a_person_took_over_is_not_cleared_nor_replaced(stack):
    api = stack.api
    stack.revansa("sheet_a:p1", next_action_type=SalesActionType.FOLLOW_UP, next_action_due_at=DUE)
    stack.sync()
    api.human_set(TAB, 2, "Prochaine action", "reminder")  # the person's own plan now
    stack.revansa("sheet_a:p1", next_action_type=None, next_action_due_at=None)
    report = stack.sync()
    assert report.applied == 0 and report.skipped[SkipReason.OCCUPIED] == 1
    assert cell(api, 2, "Prochaine action") == "reminder"


def test_a_cell_that_cannot_be_read_protects_its_row_and_only_its_row(database):
    tmp_path, url = database
    api = new_sheet(("p1", "Ada", PHONE_1), ("p2", "Grace", PHONE_2))
    api.human_set(TAB, 2, "Relances", "many")  # not a number: the row is not read at all
    stack = Stack(tmp_path, url, A, {"sheet_a": api})
    stack.revansa("sheet_a:p2", follow_up_count=2)
    report = stack.sync()
    assert report.applied == 1 and cell(api, 2, "Relances") == "many"
    assert cell(api, 3, "Relances") == "2"
    stack.engine.dispose()


# ---- a person edits while the agent works ------------------------------------------------------------


def test_a_person_editing_a_cell_after_it_was_read_is_never_overwritten_and_not_retried_at_once(
    stack,
):
    api, source = stack.api, stack.sources["sheet_a"]
    stack.revansa("sheet_a:p1", status=S.PAUSED)
    source.after_read = lambda: api.human_set(TAB, 2, "Statut", "closed")
    report = stack.sync()
    assert (report.conflicts, report.applied) == (1, 0)
    assert cell(api, 2, "Statut") == "closed"
    [entry] = stack.ledger_of("sheet_a:p1", F.STATUS)
    assert entry.outcome is Outcome.CONFLICT
    again = stack.sync()  # the person may be right: a status already as strict is left alone
    assert again.planned == 0 and cell(api, 2, "Statut") == "closed"


def test_a_conflict_on_one_field_does_not_block_the_others_of_the_same_row(stack):
    api, source = stack.api, stack.sources["sheet_a"]
    stack.revansa("sheet_a:p1", last_contacted_at=SENT, follow_up_count=2)
    source.after_read = lambda: api.human_set(TAB, 2, "Relances", 9)
    report = stack.sync()
    assert (report.conflicts, report.applied) == (1, 1)
    assert cell(api, 2, "Relances") == 9
    assert cell(api, 2, "Dernier contact") == "2026-03-01T10:00:00+01:00"


# ---- failures, retries and restarts --------------------------------------------------------------------


def test_an_unreachable_sheet_defers_and_survives_a_restart_then_the_next_pass_writes(stack):
    api = stack.api
    stack.revansa("sheet_a:p1", last_contacted_at=SENT)
    api.fail_next("write", SheetsApiError("boom", status=500))
    report = stack.sync()
    assert (report.deferred, report.applied, report.stopped) == (1, 0, FailureCode.UNAVAILABLE)
    assert cell(api, 2, "Dernier contact") == ""
    stack.restart()  # the ledger is in the database: the wait is remembered
    calls = api.count("write")
    held = stack.sync()
    assert held.held_back == 1 and held.applied == 0 and api.count("write") == calls
    stack.clock.advance(minutes=6)
    done = stack.sync()
    assert done.applied == 1 and cell(api, 2, "Dernier contact") == "2026-03-01T10:00:00+01:00"
    outcomes = [e.outcome for e in stack.ledger_of("sheet_a:p1", F.LAST_CONTACTED_AT)]
    assert outcomes == [Outcome.DEFERRED, Outcome.APPLIED]


def test_a_rate_limit_waits_for_as_long_as_google_asked_not_less(stack):
    api = stack.api
    stack.revansa("sheet_a:p1", follow_up_count=1)
    api.fail_next("write", SheetsApiError("slow down", status=429))
    assert stack.sync().stopped is FailureCode.RATE_LIMITED
    stack.clock.advance(minutes=6)
    assert stack.sync().applied == 1  # no retry-after was given: the backoff applies


def test_a_lost_answer_is_recognised_after_a_restart_and_nothing_is_sent_twice(stack):
    api = stack.api
    stack.revansa("sheet_a:p1", follow_up_count=2)
    api.fail_next("write", SheetsApiError("timeout", status=500))
    stack.sync()
    api.human_set(TAB, 2, "Relances", 2)  # the write did reach the sheet after all
    stack.restart()
    stack.clock.advance(minutes=1)
    calls = api.count("write")
    report = stack.sync()
    assert report.recovered == 1 and report.applied == 0 and api.count("write") == calls
    assert stack.ledger_of("sheet_a:p1", F.FOLLOW_UP_COUNT)[-1].outcome is Outcome.APPLIED


def test_after_a_restart_nothing_already_written_is_written_again(stack):
    stack.revansa("sheet_a:p1", last_contacted_at=SENT, follow_up_count=1)
    stack.sync()
    stack.restart()
    calls = stack.api.count("write")
    report = stack.sync()
    assert report.planned == 0 and stack.api.count("write") == calls
    assert [e.sequence for e in stack.ledger.entries()] == [1, 2]


def test_a_sheet_that_is_not_ready_stops_its_own_pass_and_writes_nothing(database):
    tmp_path, url = database
    broken = sheet_with_one()
    broken.tabs[TAB][0] = [h for h in broken.tabs[TAB][0] if h != "Relances"]
    fine = new_sheet(("p1", "Grace", PHONE_2))
    stack = Stack(tmp_path, url, A, {"sheet_a": broken, "sheet_b": fine})
    stack.state("sheet_b:p1", follow_up_count=2)
    first = stack.sync("sheet_a")
    assert first.stopped is not None and first.applied == 0 and broken.count("write") == 0
    second = stack.sync("sheet_b")
    assert second.applied == 1 and cell(fine, 2, "Relances") == "2"
    stack.engine.dispose()


def test_a_dry_run_reads_and_plans_but_never_writes_nor_records(database):
    tmp_path, url = database
    api = sheet_with_one()
    stack = Stack(tmp_path, url, A, {"sheet_a": api}, dry_run=True)
    stack.revansa("sheet_a:p1", status=S.CLOSED, follow_up_count=2)
    report = stack.sync()
    assert report.dry_run and report.planned == 2 and report.applied == 0
    assert api.count("write") == 0 and stack.ledger.entries() == ()
    stack.engine.dispose()


def test_a_refusal_in_the_registry_is_written_to_the_sheet_only_when_the_operator_asked(database):
    tmp_path, url = database
    quiet_api, loud_api = sheet_with_one(), sheet_with_one()
    quiet = Stack(tmp_path, url, A, {"sheet_a": quiet_api})
    quiet.world.block(consent_contact(PHONE_1))
    assert quiet.sync().planned == 0 and cell(quiet_api, 2, "Statut") == "new"
    on = {"spreadsheet_id": SID, "writeback": {"columns": HEADERS, "propagate_opt_out": True}}
    loud = Stack(tmp_path, url, B, {"sheet_a": loud_api}, settings=on)
    loud.world.block(consent_contact(PHONE_1))
    assert loud.sync().applied == 1 and cell(loud_api, 2, "Statut") == "opted_out"
    quiet.engine.dispose()
    loud.engine.dispose()


# ---- several sources, several clients ----------------------------------------------------------------


def test_two_sheets_of_one_client_with_the_same_ids_are_written_each_in_its_own(database):
    tmp_path, url = database
    one = new_sheet(("p1", "Ada", PHONE_1))
    two = new_sheet(("p1", "Grace", PHONE_2))
    stack = Stack(tmp_path, url, A, {"sheet_a": one, "sheet_b": two})
    stack.revansa("sheet_a:p1", follow_up_count=1)
    stack.revansa("sheet_b:p1", follow_up_count=5)
    assert stack.sync("sheet_a").applied == 1 and stack.sync("sheet_b").applied == 1
    assert (cell(one, 2, "Relances"), cell(two, 2, "Relances")) == ("1", "5")
    assert {e.prospect_id for e in stack.ledger.entries()} == {"sheet_a:p1", "sheet_b:p1"}
    stack.engine.dispose()


def test_two_clients_on_one_database_never_write_into_each_others_sheet_or_ledger(database):
    tmp_path, url = database
    mine = new_sheet(("p1", "Ada", PHONE_1))
    theirs = new_sheet(("p1", "Ada", PHONE_1))
    a = Stack(tmp_path, url, A, {"sheet_a": mine})
    b = Stack(tmp_path, url, B, {"sheet_a": theirs})
    a.revansa("sheet_a:p1", follow_up_count=3)
    assert a.sync().applied == 1
    assert cell(mine, 2, "Relances") == "3" and cell(theirs, 2, "Relances") == ""
    assert theirs.count("write") == 0
    assert b.sync().planned == 0 and b.ledger.entries() == ()
    assert [e.client_id for e in a.ledger.entries()] == [A]
    b.revansa("sheet_a:p1", follow_up_count=1)
    assert b.sync().applied == 1
    assert cell(mine, 2, "Relances") == "3" and cell(theirs, 2, "Relances") == "1"
    a.engine.dispose()
    b.engine.dispose()


def test_a_syncer_cannot_be_built_from_two_clients_pieces(database):
    tmp_path, url = database
    a = Stack(tmp_path, url, A, {"sheet_a": sheet_with_one()})
    b = Stack(tmp_path, url, B, {"sheet_a": sheet_with_one()})
    with pytest.raises(ValueError):
        WriteBackSyncer(
            a.sources["sheet_a"], a.store, a.world.registry, b.ledger, a.policy, clock=a.clock
        )
    a.engine.dispose()
    b.engine.dispose()


# ---- nothing personal in what is kept -------------------------------------------------------------------


def test_the_ledger_and_the_report_never_hold_a_name_a_number_or_a_cell(stack):
    stack.revansa("sheet_a:p1", status=S.PAUSED, last_contacted_at=SENT, follow_up_count=2)
    report = stack.sync()
    text = " ".join([repr(report), repr(stack.ledger), *(repr(e) for e in stack.ledger.entries())])
    for leak in (PHONE_1, "100001", "Ada", "Lovelace"):
        assert leak not in text


def test_a_policy_for_cells_the_source_cannot_describe_is_refused_when_the_syncer_is_built(stack):
    from sourcing_fakes import FakeProspectSource, default_capabilities

    from sales_app.sourcing.writeback import WriteBackPolicyError

    blind = FakeProspectSource(
        A,
        capabilities=default_capabilities(
            writable=frozenset({F.STATUS, F.FOLLOW_UP_COUNT}),
            mirrored=frozenset({F.FOLLOW_UP_COUNT}),
        ),
        allow_writes=True,
    )
    with pytest.raises(WriteBackPolicyError):
        WriteBackSyncer(
            blind, stack.store, stack.world.registry, stack.ledger, stack.policy, clock=stack.clock
        )


class Counting:
    """A ledger that counts how often its entries are listed, and otherwise is the real one."""

    def __init__(self, ledger):
        self._ledger = ledger
        self.listed = 0

    def __getattr__(self, name):
        return getattr(self._ledger, name)

    def entries(self, prospect_id=None):
        self.listed += 1
        return self._ledger.entries(prospect_id)


def test_a_pass_over_many_prospects_does_not_read_the_ledger_for_each_of_them(database):
    tmp_path, url = database
    people = [(f"p{n}", f"Person {n}", f"+216 99 200 {n:03d}") for n in range(1, 21)]
    api = new_sheet(*people)
    stack = Stack(tmp_path, url, A, {"sheet_a": api})
    for pid, _, _ in people:
        stack.state(f"sheet_a:{pid}", follow_up_count=2, last_contacted_at=SENT)
    counting = Counting(stack.ledger)
    syncer = stack.syncers["sheet_a"]
    syncer._ledger = counting
    report = syncer.run_once()
    assert report.applied == 40  # a count and a date for each of the twenty
    assert counting.listed == 0  # no next action anywhere: the ledger's entries were never listed
    again = syncer.run_once()
    assert again.planned == 0 and counting.listed == 0
    stack.engine.dispose()


def test_the_ledger_is_read_only_for_the_prospects_whose_next_action_cell_is_filled(database):
    tmp_path, url = database
    api = new_sheet(
        ("p1", "Ada", PHONE_1), ("p2", "Grace", PHONE_2), ("p3", "Joan", "+216 99 100 003")
    )
    api.human_set(TAB, 4, "Prochaine action", "callback")
    api.human_set(TAB, 4, "Echeance", "2026-03-20 09:00")
    stack = Stack(tmp_path, url, A, {"sheet_a": api})
    for pid in ("p1", "p2", "p3"):
        stack.state(
            f"sheet_a:{pid}", next_action_type=SalesActionType.FOLLOW_UP, next_action_due_at=DUE
        )
    counting = Counting(stack.ledger)
    syncer = stack.syncers["sheet_a"]
    syncer._ledger = counting
    report = syncer.run_once()
    assert report.applied == 2 and report.skipped[SkipReason.OCCUPIED] == 1
    # one listing for p3's filled cell; p1 and p2 were empty, so they cost none
    assert counting.listed == 1
    assert cell(api, 4, "Prochaine action") == "callback"
    stack.engine.dispose()
