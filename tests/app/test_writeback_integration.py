"""The write-back path as the pieces really fit together, with nothing mocked between them.

What Revansa decides goes through the real `CompositeRepository` into a real SQL operational store
(a file database, so a restart is a real restart); the consent registry is the real one, over files;
only the CRM is a fake, because the one real CRM source is read-only. The synchroniser then reads
those same store and registry and writes into the source.

This proves the pieces agree with each other. It does not prove that the application runs a
synchroniser: it does not yet (see the Lot E report).
"""

import itertools
from dataclasses import replace

import pytest
from consent_support import Clock, make_world
from consent_support import contact as consent_contact
from factories import make_prospect
from sourcing_fakes import FakeProspectSource, default_capabilities

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.sourcing import ProspectField, SourceUnavailableError
from sales_app.sourcing.composite import KEYWORD_OPT_OUT_EVENT, CompositeRepository
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_app.sourcing.writeback import (
    InMemoryWriteBackLedger,
    Outcome,
    SkipReason,
    WriteBackPolicy,
    WriteBackSyncer,
)
from sales_core.contracts import AuditEvent, ContactInfo, ProspectId, ProspectStatus

S = ProspectStatus
A, B = "client-a", "client-b"
ON = WriteBackPolicy.of(ProspectField.STATUS)
ON_WITH_OPT_OUT = WriteBackPolicy.of(ProspectField.STATUS, propagate_opt_out=True)
PHONE_1, PHONE_2 = "+216 99 100 001", "+216 99 100 002"


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{tmp_path / 'ops.db'}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    yield tmp_path, url
    engine.dispose()


class Client:
    """One client's whole write-back stack. `restart()` rebuilds everything that lives in memory
    (engine, store, repository, ledger, syncer) over the same database and registry files."""

    def __init__(self, tmp_path, url, client_id, *, source=None, policy=ON, healthy=True):
        self.tmp_path, self.url, self.client_id, self.policy = tmp_path, url, client_id, policy
        self.world = make_world(tmp_path, client_id=client_id, initialise=healthy)
        self.source = source or FakeProspectSource(
            client_id, capabilities=default_capabilities(), allow_writes=True
        )
        self.clock = Clock()
        self._runs = itertools.count(1)
        self.engine = None
        self.start()

    def start(self, ledger=None):
        self.engine = create_database_engine(self.url)
        self.store = SqlOperationalStore(SqlOperationalRows(self.engine, self.client_id))
        self.repository = CompositeRepository(self.source, self.store, self.world.registry)
        self.ledger = ledger or InMemoryWriteBackLedger(self.client_id)
        self.syncer = WriteBackSyncer(
            self.source,
            self.store,
            self.world.registry,
            self.ledger,
            self.policy,
            clock=self.clock,
            new_run_id=lambda: f"run{next(self._runs)}",
        )

    def restart(self):
        """The process dies: nothing in memory survives, not even the ledger."""
        self.engine.dispose()
        self.start()

    def person(self, native_id, phone):
        self.source.add_record(
            make_prospect(
                prospect_id=ProspectId(native_id),
                client_id=self.client_id,
                status=S.NEW,
                contact=ContactInfo(phone=phone, email=None),
            )
        )
        return f"fake_crm:{native_id}"

    def revansa_sets(self, public_id, status):
        current = self.repository.get_prospect(ProspectId(public_id))
        self.repository.update_prospects([replace(current, status=status)])

    def writes_sent(self):
        return [r for r in self.source.requests if r.startswith("write:")]

    def in_source(self, native_id):
        return self.source.records[native_id].status


@pytest.fixture
def client(database):
    tmp_path, url = database
    made = Client(tmp_path, url, A)
    yield made
    made.engine.dispose()


# ---- action -> state -> write -> ledger ---------------------------------------------------------


def test_a_pause_decided_in_revansa_reaches_the_source_and_the_ledger(client):
    pid = client.person("1", PHONE_1)
    client.person("2", PHONE_2)
    client.revansa_sets(pid, S.PAUSED)
    report = client.syncer.run_once()
    assert (report.planned, report.applied, report.stopped) == (1, 1, None)
    assert client.in_source("1") is S.PAUSED and client.in_source("2") is S.NEW
    [entry] = client.ledger.entries()
    assert (entry.prospect_id, entry.outcome, entry.new, entry.expected) == (
        pid,
        Outcome.APPLIED,
        S.PAUSED,
        S.NEW,
    )


def test_a_second_pass_writes_nothing_more(client):
    pid = client.person("1", PHONE_1)
    client.revansa_sets(pid, S.CLOSED)
    client.syncer.run_once()
    again = client.syncer.run_once()
    assert again.planned == 0 and again.skipped[SkipReason.ALREADY_APPLIED] == 1
    assert len(client.writes_sent()) == 1 and len(client.ledger.entries()) == 1


def test_nothing_is_written_while_the_policy_is_empty(database):
    tmp_path, url = database
    off = Client(tmp_path, url, A, policy=WriteBackPolicy())
    pid = off.person("1", PHONE_1)
    off.revansa_sets(pid, S.CLOSED)
    assert off.syncer.run_once().planned == 0
    assert off.writes_sent() == [] and off.in_source("1") is S.NEW
    off.engine.dispose()


# ---- consent ------------------------------------------------------------------------------------


def keyword_opt_out(client, pid):
    """What the Engine emits when an inbound message is an opt-out keyword."""
    client.repository.append_audit_events(
        [
            AuditEvent(
                timestamp=client.clock(),
                client_id=client.client_id,
                event_type=KEYWORD_OPT_OUT_EVENT,
                actor="system:inbound",
                prospect_id=ProspectId(pid),
            )
        ]
    )


def test_an_opt_out_keyword_reaches_the_source_only_when_the_operator_asked(database):
    tmp_path, url = database
    quiet = Client(tmp_path, url, A, policy=ON)
    pid = quiet.person("1", PHONE_1)
    keyword_opt_out(quiet, pid)
    report = quiet.syncer.run_once()
    # The registry is not even read when opt-outs are not to be propagated.
    assert quiet.writes_sent() == [] and report.skipped[SkipReason.NOTHING_TO_WRITE] == 1
    assert quiet.in_source("1") is S.NEW
    quiet.engine.dispose()


def test_an_opt_out_keyword_is_propagated_to_the_source_when_asked(database):
    tmp_path, url = database
    loud = Client(tmp_path, url, A, policy=ON_WITH_OPT_OUT)
    pid = loud.person("1", PHONE_1)
    loud.person("2", PHONE_2)
    keyword_opt_out(loud, pid)
    report = loud.syncer.run_once()
    assert (report.applied, report.stopped) == (1, None)
    assert loud.in_source("1") is S.OPTED_OUT and loud.in_source("2") is S.NEW
    loud.engine.dispose()


def test_an_unhealthy_registry_never_becomes_a_refusal_of_every_record(database):
    tmp_path, url = database
    broken = Client(tmp_path, url, A, policy=ON_WITH_OPT_OUT, healthy=False)
    broken.person("1", PHONE_1)
    broken.person("2", PHONE_2)
    report = broken.syncer.run_once()
    assert report.applied == 0 and broken.writes_sent() == []
    assert broken.in_source("1") is S.NEW and broken.in_source("2") is S.NEW
    broken.engine.dispose()


def test_a_refusal_recorded_by_the_operator_survives_a_restart_and_is_propagated(database):
    tmp_path, url = database
    first = Client(tmp_path, url, A, policy=ON_WITH_OPT_OUT)
    first.person("1", PHONE_1)
    first.world.block(consent_contact(PHONE_1))
    first.restart()
    assert first.syncer.run_once().applied == 1
    assert first.in_source("1") is S.OPTED_OUT
    first.engine.dispose()


# ---- idempotence and restart --------------------------------------------------------------------


def test_after_a_restart_with_no_ledger_at_all_nothing_is_sent_twice(client):
    pid = client.person("1", PHONE_1)
    client.revansa_sets(pid, S.CLOSED)
    client.syncer.run_once()
    client.restart()  # the ledger was only in memory: it is gone
    assert client.ledger.entries() == ()
    report = client.syncer.run_once()
    assert report.planned == 0 and report.skipped[SkipReason.ALREADY_APPLIED] == 1
    assert len(client.writes_sent()) == 1


def test_what_revansa_decided_before_a_restart_is_written_after_it(client):
    pid = client.person("1", PHONE_1)
    client.revansa_sets(pid, S.PAUSED)
    client.restart()  # decided, not yet written: the state is in the database
    assert client.syncer.run_once().applied == 1
    assert client.in_source("1") is S.PAUSED


def test_an_unreachable_source_defers_and_the_next_pass_after_the_delay_writes(client):
    pid = client.person("1", PHONE_1)
    client.revansa_sets(pid, S.CLOSED)
    client.source.fail_next("update_prospects", SourceUnavailableError("down"))
    down = client.syncer.run_once()
    assert (down.deferred, down.applied) == (1, 0) and client.in_source("1") is S.NEW
    assert client.ledger.latest(ProspectId(pid)).outcome is Outcome.DEFERRED
    assert client.syncer.run_once().held_back == 1  # not hammered
    client.clock.advance(hours=1)
    assert client.syncer.run_once().applied == 1 and client.in_source("1") is S.CLOSED


def test_a_person_changing_the_source_meanwhile_is_not_overwritten(client):
    pid = client.person("1", PHONE_1)
    client.revansa_sets(pid, S.PAUSED)
    client.source.human_edit(replace(client.source.records["1"], status=S.CLOSED))
    report = client.syncer.run_once()
    assert report.planned == 0 and client.in_source("1") is S.CLOSED
    assert client.writes_sent() == []


# ---- isolation between clients ------------------------------------------------------------------


def test_two_clients_on_one_database_write_only_into_their_own_source(database):
    tmp_path, url = database
    a = Client(tmp_path, url, A)
    b = Client(tmp_path, url, B)
    pid_a = a.person("1", PHONE_1)  # the same native id and number in two accounts
    b.person("1", PHONE_1)
    a.revansa_sets(pid_a, S.CLOSED)
    b.world.block(consent_contact(PHONE_1))  # B's refusal says nothing about A's contact
    assert a.syncer.run_once().applied == 1
    assert b.syncer.run_once().applied == 0
    assert a.in_source("1") is S.CLOSED and b.in_source("1") is S.NEW
    assert b.writes_sent() == [] and b.ledger.entries() == ()
    assert {e.client_id for e in a.ledger.entries()} == {A}
    a.engine.dispose()
    b.engine.dispose()


def test_a_syncer_cannot_be_built_from_two_clients_pieces(database):
    tmp_path, url = database
    a = Client(tmp_path, url, A)
    b = Client(tmp_path, url, B)
    with pytest.raises(ValueError):
        WriteBackSyncer(a.source, a.store, a.world.registry, b.ledger, ON, clock=a.clock)
    with pytest.raises(ValueError):
        WriteBackSyncer(a.source, b.store, a.world.registry, a.ledger, ON, clock=a.clock)
    a.engine.dispose()
    b.engine.dispose()
