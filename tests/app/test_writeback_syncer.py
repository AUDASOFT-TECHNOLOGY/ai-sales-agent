"""The write-back synchroniser (11.2.5c), on the fakes: no network, no real time, no randomness."""

import itertools
import logging
import re
from datetime import timedelta

import pytest
from consent_support import EMAIL, PHONE, Clock, make_world
from consent_support import contact as consent_contact
from factories import CLIENT, make_prospect
from sourcing_fakes import FakeOperationalStore, FakeProspectSource, default_capabilities

from sales_app.sourcing import (
    ProspectField,
    ProspectState,
    SourceAuthError,
    SourceRateLimitedError,
    SourceUnavailableError,
    StoreUnavailableError,
)
from sales_app.sourcing.writeback import (
    FailureCode,
    InMemoryWriteBackLedger,
    Outcome,
    SkipReason,
    WriteBackPolicy,
    WriteBackPolicyError,
    WriteBackSyncer,
)
from sales_core.contracts import ContactInfo, ProspectId, ProspectStatus

S = ProspectStatus
ON = WriteBackPolicy.of(ProspectField.STATUS)
ON_WITH_OPT_OUT = WriteBackPolicy(fields=ON.fields, propagate_opt_out=True)
OTHER_PHONE = "+216 99 555 666"


class RacySource(FakeProspectSource):
    """A person edits a record right after the pass has read the source."""

    after_list = None

    def list_prospects(self):
        result = super().list_prospects()
        if self.after_list is not None:
            edit, self.after_list = self.after_list, None
            edit()
        return result


class LostAnswerSource(FakeProspectSource):
    """The write goes through, but the answer never comes back."""

    lose_next = False

    def update_prospects(self, changes):
        super().update_prospects(changes)
        if self.lose_next:
            self.lose_next = False
            raise SourceUnavailableError("the answer was lost")


def person(pid, phone=None, status=S.NEW):
    return make_prospect(
        prospect_id=ProspectId(pid), status=status, contact=ContactInfo(phone=phone, email=None)
    )


class Env:
    def __init__(
        self,
        policy=ON,
        *,
        source_cls=FakeProspectSource,
        allow_writes=True,
        initialise=True,
        capabilities=None,
        **options,
    ):
        self.source = source_cls(
            CLIENT,
            capabilities=capabilities or default_capabilities(),
            allow_writes=allow_writes,
        )
        self.store = FakeOperationalStore(CLIENT)
        self.world = make_world(client_id=CLIENT, initialise=initialise)
        self.ledger = InMemoryWriteBackLedger(CLIENT)
        self.clock = Clock()
        counter = itertools.count(1)
        self.syncer = WriteBackSyncer(
            self.source,
            self.store,
            self.world.registry,
            self.ledger,
            policy,
            clock=self.clock,
            new_run_id=lambda: f"run{next(counter)}",
            **options,
        )

    def crm(self, pid, phone=None, status=S.NEW):
        self.source.add_record(person(pid, phone, status))

    def revansa_says(self, pid, status):
        self.store.save_states(
            [
                ProspectState(
                    client_id=CLIENT, prospect_id=ProspectId(f"fake_crm:{pid}"), status=status
                )
            ]
        )

    def status_in_source(self, pid):
        return self.source.records[pid].status

    def writes_sent(self):
        return [r for r in self.source.requests if r.startswith("write:")]

    def outcomes(self):
        return [(e.prospect_id, e.outcome, e.code) for e in self.ledger.entries()]


# ---- off by default, and what it writes -----------------------------------------------------


def test_an_empty_policy_sends_nothing_and_does_not_even_read():
    env = Env(WriteBackPolicy())
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    report = env.syncer.run_once()
    assert report.planned == 0 and report.stopped is None
    assert env.source.requests == () and env.ledger.entries() == ()
    assert env.status_in_source("1") is S.NEW


def test_a_restriction_revansa_holds_is_written_and_recorded():
    env = Env()
    env.crm("1")
    env.crm("2")
    env.revansa_says("1", S.CLOSED)
    report = env.syncer.run_once()
    assert (report.planned, report.applied, report.stopped) == (1, 1, None)
    assert env.status_in_source("1") is S.CLOSED and env.status_in_source("2") is S.NEW
    assert env.outcomes() == [("fake_crm:1", Outcome.APPLIED, None)]
    assert report.skipped[SkipReason.NOTHING_TO_WRITE] == 1


def test_a_second_pass_has_nothing_to_do():
    env = Env()
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    env.syncer.run_once()
    again = env.syncer.run_once()
    assert again.planned == 0 and again.skipped[SkipReason.ALREADY_APPLIED] == 1
    assert len(env.writes_sent()) == 1 and len(env.ledger.entries()) == 1


def test_it_never_loosens_a_status_nor_writes_over_a_stricter_one():
    env = Env()
    env.crm("1", status=S.CLOSED)
    env.crm("2", status=S.OPTED_OUT)
    env.revansa_says("1", S.PAUSED)
    env.revansa_says("2", S.CLOSED)
    report = env.syncer.run_once()
    assert report.planned == 0 and env.writes_sent() == []
    assert (env.status_in_source("1"), env.status_in_source("2")) == (S.CLOSED, S.OPTED_OUT)


def test_only_the_status_changes_in_the_source():
    env = Env()
    env.crm("1", PHONE)
    env.revansa_says("1", S.CLOSED)
    before = env.source.records["1"]
    env.syncer.run_once()
    after = env.source.records["1"]
    assert after.contact == before.contact and after.display_name == before.display_name
    assert after.stage == before.stage and after.schedule == before.schedule


# ---- conflicts, batches ----------------------------------------------------------------------


def test_a_person_editing_between_read_and_write_is_never_overwritten():
    env = Env(source_cls=RacySource)
    env.crm("1")
    env.crm("2")
    env.revansa_says("1", S.CLOSED)
    env.revansa_says("2", S.CLOSED)
    env.source.after_list = lambda: env.source.human_edit(person("1", status=S.PAUSED))
    report = env.syncer.run_once()
    # the batch was refused as a whole, so it was retried one at a time: only prospect 1 is lost
    assert (report.applied, report.conflicts) == (1, 1)
    assert env.status_in_source("1") is S.PAUSED and env.status_in_source("2") is S.CLOSED
    assert {p: o for p, o, _ in env.outcomes()} == {
        "fake_crm:1": Outcome.CONFLICT,
        "fake_crm:2": Outcome.APPLIED,
    }


def test_a_conflict_is_not_retried_at_once_but_after_the_cooldown():
    env = Env(source_cls=RacySource, cooldown=timedelta(hours=1))
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    env.source.after_list = lambda: env.source.human_edit(person("1", status=S.PAUSED))
    assert env.syncer.run_once().conflicts == 1
    env.clock.advance(minutes=30)
    held = env.syncer.run_once()
    assert (held.held_back, held.planned) == (1, 0) and env.status_in_source("1") is S.PAUSED
    env.clock.advance(minutes=31)
    assert env.syncer.run_once().applied == 1 and env.status_in_source("1") is S.CLOSED


def test_batches_are_sent_in_the_size_asked_and_a_refusal_hurts_only_its_prospect():
    env = Env(source_cls=RacySource, batch_size=2)
    for n in range(5):
        env.crm(str(n))
        env.revansa_says(str(n), S.CLOSED)
    env.source.after_list = lambda: env.source.human_edit(person("3", status=S.PAUSED))
    report = env.syncer.run_once()
    assert (report.planned, report.applied, report.conflicts) == (5, 4, 1)
    assert {env.status_in_source(str(n)) for n in (0, 1, 2, 4)} == {S.CLOSED}
    assert env.status_in_source("3") is S.PAUSED
    assert env.writes_sent().count("write:update_prospects") >= 3


# ---- failures --------------------------------------------------------------------------------


def test_an_unreachable_source_defers_stops_the_pass_and_is_retried_after_a_delay():
    env = Env(backoff_base=timedelta(minutes=5))
    env.crm("1")
    env.crm("2")
    env.revansa_says("1", S.CLOSED)
    env.revansa_says("2", S.CLOSED)
    env.source.fail_next("update_prospects", SourceUnavailableError("down"))
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.UNAVAILABLE and (report.deferred, report.applied) == (2, 0)
    assert {c for *_, c in env.outcomes()} == {FailureCode.UNAVAILABLE}
    assert env.status_in_source("1") is S.NEW
    env.clock.advance(minutes=1)
    assert env.syncer.run_once().held_back == 2  # not hammered
    env.clock.advance(minutes=5)
    assert env.syncer.run_once().applied == 2


def test_the_backoff_doubles_while_the_source_stays_down_up_to_a_cap():
    env = Env(backoff_base=timedelta(minutes=5), backoff_cap=timedelta(minutes=12))
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    env.source.fail_next("update_prospects", SourceUnavailableError("down"), times=3)
    env.syncer.run_once()  # delay 5
    env.clock.advance(minutes=5)
    env.syncer.run_once()  # delay 10
    env.clock.advance(minutes=9)
    assert env.syncer.run_once().held_back == 1
    env.clock.advance(minutes=1)
    env.syncer.run_once()  # delay capped at 12
    env.clock.advance(minutes=11)
    assert env.syncer.run_once().held_back == 1
    env.clock.advance(minutes=1)
    assert env.syncer.run_once().applied == 1


def test_a_rate_limit_waits_as_long_as_the_source_asked():
    env = Env()
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    env.source.fail_next("update_prospects", SourceRateLimitedError(retry_after=120))
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.RATE_LIMITED
    (entry,) = env.ledger.entries()
    assert entry.not_before == env.clock() + timedelta(seconds=120)
    env.clock.advance(seconds=60)
    assert env.syncer.run_once().held_back == 1
    env.clock.advance(seconds=61)
    assert env.syncer.run_once().applied == 1


def test_refused_credentials_stop_the_pass_and_are_held_back_for_a_while():
    env = Env()
    env.crm("1")
    env.crm("2")
    env.revansa_says("1", S.CLOSED)
    env.revansa_says("2", S.CLOSED)
    env.source.fail_next("update_prospects", SourceAuthError("no"))
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.AUTH and report.failed == 2
    assert {o for _, o, _ in env.outcomes()} == {Outcome.FAILED}
    assert env.syncer.run_once().held_back == 2
    env.clock.advance(hours=2)
    assert env.syncer.run_once().applied == 2


def test_a_read_only_source_receives_no_write_request():
    env = Env(allow_writes=False)
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    report = env.syncer.run_once()
    assert report.stopped is FailureCode.READ_ONLY and report.failed == 1
    assert env.writes_sent() == [] and env.status_in_source("1") is S.NEW
    assert env.outcomes() == [("fake_crm:1", Outcome.FAILED, FailureCode.READ_ONLY)]


def test_a_source_or_store_that_cannot_be_read_ends_the_pass_without_writing():
    env = Env()
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    env.source.fail_next("list_prospects", SourceUnavailableError("down"))
    assert env.syncer.run_once().stopped is FailureCode.UNAVAILABLE
    env.store.fail_next("list_states", StoreUnavailableError("down"))
    assert env.syncer.run_once().stopped is FailureCode.UNAVAILABLE
    env.source.fail_next("list_prospects", SourceAuthError("no"))
    assert env.syncer.run_once().stopped is FailureCode.AUTH
    assert env.writes_sent() == [] and env.ledger.entries() == ()


def test_a_bug_is_not_swallowed():
    env = Env()
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    env.source.fail_next("update_prospects", RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        env.syncer.run_once()


def test_a_lost_answer_is_recovered_without_writing_twice():
    env = Env(source_cls=LostAnswerSource)
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    env.source.lose_next = True
    first = env.syncer.run_once()
    assert first.stopped is FailureCode.UNAVAILABLE and env.status_in_source("1") is S.CLOSED
    env.clock.advance(minutes=1)  # still inside the delay: the result is found all the same
    second = env.syncer.run_once()
    assert second.recovered == 1 and second.planned == 0
    assert [o for _, o, _ in env.outcomes()] == [Outcome.DEFERRED, Outcome.APPLIED]
    assert len(env.writes_sent()) == 1
    assert env.syncer.run_once().recovered == 0


# ---- dry run ---------------------------------------------------------------------------------


def test_a_dry_run_plans_and_reports_but_writes_and_records_nothing():
    env = Env(dry_run=True)
    env.crm("1")
    env.crm("2")
    env.revansa_says("1", S.CLOSED)
    report = env.syncer.run_once()
    assert report.dry_run and report.planned == 1 and report.applied == 0
    assert env.writes_sent() == [] and env.ledger.entries() == ()
    assert env.status_in_source("1") is S.NEW


# ---- the consent registry --------------------------------------------------------------------


def test_an_opt_out_is_not_propagated_unless_asked():
    env = Env(ON)
    env.crm("1", PHONE)
    env.world.block(consent_contact(PHONE))
    assert env.syncer.run_once().planned == 0
    assert env.status_in_source("1") is S.NEW


def test_a_blocked_contact_is_marked_opted_out_when_asked_and_only_that_one():
    env = Env(ON_WITH_OPT_OUT)
    env.crm("1", PHONE)
    env.crm("2", OTHER_PHONE)
    env.world.block(consent_contact(PHONE))
    report = env.syncer.run_once()
    assert report.applied == 1
    assert env.status_in_source("1") is S.OPTED_OUT and env.status_in_source("2") is S.NEW
    assert env.syncer.run_once().planned == 0


def test_an_unhealthy_registry_is_never_turned_into_a_refusal_of_every_record():
    env = Env(ON_WITH_OPT_OUT, initialise=False)  # the registry was never set up: it blocks all
    for n in range(4):
        env.crm(str(n), f"+216 99 000 00{n}")
    report = env.syncer.run_once()
    assert report.planned == 0 and env.writes_sent() == []
    assert {env.status_in_source(str(n)) for n in range(4)} == {S.NEW}


def test_the_registry_is_not_read_when_no_opt_out_is_to_be_propagated():
    env = Env(ON)

    class Boom:
        def snapshot(self):
            raise AssertionError("the registry must not be read")

    env.syncer._consent = Boom()
    env.crm("1")
    env.revansa_says("1", S.CLOSED)
    assert env.syncer.run_once().applied == 1


def test_a_policy_the_source_cannot_honour_fails_when_the_syncer_is_built():
    with pytest.raises(WriteBackPolicyError):
        Env(ON_WITH_OPT_OUT, capabilities=default_capabilities(consent_signal=False))
    with pytest.raises(WriteBackPolicyError):
        Env(ON, capabilities=default_capabilities(writable=frozenset()))


def test_the_pieces_must_belong_to_one_client_and_the_options_make_sense():
    env = Env()
    with pytest.raises(ValueError):
        WriteBackSyncer(
            env.source,
            env.store,
            env.world.registry,
            InMemoryWriteBackLedger("client-b"),
            ON,
            clock=env.clock,
        )
    for bad in ({"batch_size": 0}, {"cooldown": timedelta(0)}, {"backoff_base": timedelta(-1)}):
        with pytest.raises(ValueError):
            Env(**bad)


# ---- opaque ids and nothing personal ---------------------------------------------------------


def test_attempt_ids_are_a_run_and_a_counter_never_a_contact():
    env = Env()
    for n in range(3):
        env.crm(str(n), f"+216 99 000 00{n}")
        env.revansa_says(str(n), S.CLOSED)
    env.syncer.run_once()
    ids = [e.attempt_id for e in env.ledger.entries()]
    assert len(ids) == 3 and all(re.fullmatch(r"run1-\d", i) for i in ids)
    assert len(set(ids)) == 3


def test_nothing_personal_reaches_the_ledger_the_report_the_logs_or_a_representation(caplog):
    caplog.set_level(logging.DEBUG)
    env = Env(ON_WITH_OPT_OUT, source_cls=RacySource)
    env.source.add_record(
        make_prospect(
            prospect_id=ProspectId("1"),
            display_name="Leaky Name",
            contact=ContactInfo(phone=PHONE, email=EMAIL),
        )
    )
    env.crm("2", OTHER_PHONE)
    env.crm("3")
    env.world.block(consent_contact(PHONE))
    env.revansa_says("2", S.CLOSED)
    env.revansa_says("3", S.CLOSED)
    env.source.after_list = lambda: env.source.human_edit(person("3", status=S.PAUSED))
    report = env.syncer.run_once()
    env.source.fail_next("update_prospects", SourceUnavailableError(PHONE))
    env.clock.advance(hours=3)
    env.syncer.run_once()
    text = " ".join(
        [repr(report), repr(env.syncer), repr(env.ledger), caplog.text]
        + [repr(e) for e in env.ledger.entries()]
    )
    for leak in (PHONE, "21699888777", EMAIL, "Leaky", OTHER_PHONE, "21699555666"):
        assert leak not in text
