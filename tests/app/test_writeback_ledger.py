"""The write-back ledger contract (11.2.5b), checked on the in-memory implementation.

A durable ledger will be added to the `ledger` fixture and must pass every test unchanged.
"""

import dataclasses
import threading
from datetime import UTC, datetime, timedelta

import pytest
from factories import CLIENT

from sales_app.sourcing import ProspectField
from sales_app.sourcing.writeback import (
    FailureCode,
    InMemoryWriteBackLedger,
    LedgerEntry,
    Outcome,
    WriteAttempt,
    WriteBackLedger,
)
from sales_core.contracts import ProspectId, ProspectStatus

S = ProspectStatus
OTHER = "client-b"
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+216 99 888 777"


def attempt(attempt_id="a-1", pid="fake_crm:1", **overrides):
    values = {
        "client_id": CLIENT,
        "attempt_id": attempt_id,
        "prospect_id": ProspectId(pid),
        "new": S.CLOSED,
        "expected": S.NEW,
        "outcome": Outcome.APPLIED,
        "at": T0,
    }
    return WriteAttempt(**(values | overrides))


@pytest.fixture(params=["memory"])
def ledger(request):
    return InMemoryWriteBackLedger(CLIENT)


# ---- the contract ---------------------------------------------------------------------------


def test_it_is_a_ledger_bound_to_one_client(ledger):
    assert isinstance(ledger, WriteBackLedger)
    assert ledger.client_id == CLIENT
    assert ledger.entries() == () and ledger.latest(ProspectId("fake_crm:1")) is None


def test_entries_come_back_oldest_first_with_a_sequence_without_gaps(ledger):
    first = ledger.append([attempt("a-1", "fake_crm:1")])
    second = ledger.append([attempt("a-2", "fake_crm:2"), attempt("a-3", "fake_crm:1")])
    assert [e.sequence for e in first + second] == [1, 2, 3]
    assert [e.attempt_id for e in ledger.entries()] == ["a-1", "a-2", "a-3"]
    assert [e.attempt_id for e in ledger.entries(ProspectId("fake_crm:1"))] == ["a-1", "a-3"]
    assert ledger.entries(ProspectId("fake_crm:9")) == ()


def test_the_order_is_the_order_of_recording_not_the_time_given(ledger):
    ledger.append([attempt("late", at=T0 + timedelta(hours=5))])
    ledger.append([attempt("early", at=T0)])
    assert [e.attempt_id for e in ledger.entries()] == ["late", "early"]
    assert ledger.latest(ProspectId("fake_crm:1")).attempt_id == "early"


def test_append_returns_each_attempt_as_recorded_in_the_order_given(ledger):
    entries = ledger.append([attempt("b", "fake_crm:2"), attempt("a", "fake_crm:1")])
    assert [(e.attempt_id, e.sequence) for e in entries] == [("b", 1), ("a", 2)]
    assert isinstance(entries[0], LedgerEntry) and entries[0].prospect_id == "fake_crm:2"


def test_recording_an_attempt_twice_records_it_once(ledger):
    (first,) = ledger.append([attempt()])
    (again,) = ledger.append([attempt()])
    assert again == first and again.sequence == 1
    assert len(ledger.entries()) == 1


def test_an_attempt_repeated_in_one_call_is_recorded_once(ledger):
    entries = ledger.append([attempt("a"), attempt("b", "fake_crm:2"), attempt("a")])
    assert [e.sequence for e in entries] == [1, 2, 1]
    assert len(ledger.entries()) == 2


def test_a_retry_after_other_entries_does_not_move_or_duplicate_anything(ledger):
    ledger.append([attempt("a")])
    ledger.append([attempt("b", "fake_crm:2")])
    ledger.append([attempt("a")])
    assert [(e.attempt_id, e.sequence) for e in ledger.entries()] == [("a", 1), ("b", 2)]
    (next_one,) = ledger.append([attempt("c", "fake_crm:3")])
    assert next_one.sequence == 3


def test_the_same_id_with_another_content_is_refused_and_nothing_is_written(ledger):
    ledger.append([attempt("a")])
    with pytest.raises(ValueError):
        ledger.append([attempt("fresh", "fake_crm:7"), attempt("a", outcome=Outcome.CONFLICT)])
    assert [e.attempt_id for e in ledger.entries()] == ["a"]
    with pytest.raises(ValueError):
        ledger.append([attempt("x"), attempt("x", new=S.PAUSED)])
    assert [e.attempt_id for e in ledger.entries()] == ["a"]


def test_an_attempt_of_another_client_is_refused_and_nothing_is_written(ledger):
    with pytest.raises(ValueError):
        ledger.append([attempt("a"), attempt("b", client_id=OTHER)])
    assert ledger.entries() == ()


def test_two_clients_have_two_separate_ledgers():
    mine, other = InMemoryWriteBackLedger(CLIENT), InMemoryWriteBackLedger(OTHER)
    mine.append([attempt("a")])
    other.append([attempt("a", client_id=OTHER, outcome=Outcome.CONFLICT)])
    assert [e.outcome for e in mine.entries()] == [Outcome.APPLIED]
    assert [e.outcome for e in other.entries()] == [Outcome.CONFLICT]
    assert other.entries()[0].sequence == 1


def test_latest_is_the_newest_entry_of_that_prospect(ledger):
    ledger.append([attempt("a", outcome=Outcome.CONFLICT)])
    ledger.append([attempt("b", "fake_crm:2")])
    ledger.append([attempt("c")])
    assert ledger.latest(ProspectId("fake_crm:1")).attempt_id == "c"
    assert ledger.latest(ProspectId("fake_crm:2")).attempt_id == "b"
    assert ledger.latest(ProspectId("fake_crm:1"), ProspectField.STATUS).sequence == 3


def test_what_is_returned_cannot_change_the_ledger(ledger):
    ledger.append([attempt("a")])
    listing = ledger.entries()
    assert isinstance(listing, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        listing[0].outcome = Outcome.FAILED  # type: ignore[misc]
    assert ledger.entries()[0].outcome is Outcome.APPLIED


def test_concurrent_recording_never_repeats_or_skips_a_sequence(ledger):
    def work(worker):
        for n in range(25):
            ledger.append([attempt(f"w{worker}-{n}", f"fake_crm:{worker}")])

    threads = [threading.Thread(target=work, args=(w,)) for w in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [e.sequence for e in ledger.entries()] == list(range(1, 201))
    assert len({e.attempt_id for e in ledger.entries()}) == 200


# ---- what an entry may hold -----------------------------------------------------------------


def test_every_outcome_with_its_code():
    soon = T0 + timedelta(minutes=5)
    attempt("1", outcome=Outcome.APPLIED)
    attempt("2", outcome=Outcome.CONFLICT)
    attempt("3", outcome=Outcome.DEFERRED, code=FailureCode.UNAVAILABLE)
    attempt("4", outcome=Outcome.DEFERRED, code=FailureCode.RATE_LIMITED, not_before=soon)
    for code in (FailureCode.AUTH, FailureCode.READ_ONLY, FailureCode.UNEXPECTED):
        attempt("5", outcome=Outcome.FAILED, code=code)


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": Outcome.APPLIED, "code": FailureCode.UNAVAILABLE},
        {"outcome": Outcome.CONFLICT, "code": FailureCode.AUTH},
        {"outcome": Outcome.DEFERRED},
        {"outcome": Outcome.DEFERRED, "code": FailureCode.AUTH},
        {"outcome": Outcome.FAILED},
        {"outcome": Outcome.FAILED, "code": FailureCode.RATE_LIMITED},
        {"outcome": Outcome.FAILED, "code": FailureCode.AUTH, "not_before": T0},
        {"outcome": Outcome.APPLIED, "not_before": T0},
        {"new": S.NEW, "expected": S.NEW},
        {"at": T0.replace(tzinfo=None)},
        {
            "outcome": Outcome.DEFERRED,
            "code": FailureCode.UNAVAILABLE,
            "not_before": T0.replace(tzinfo=None),
        },
        {"client_id": ""},
        {"prospect_id": ProspectId("")},
        {"prospect_id": ProspectId("inbound-2026-01-15-21699888777")},
        {"field": ProspectField.PHONE},
        {"field": ProspectField.DISPLAY_NAME},
        {"attempt_id": ""},
        {"attempt_id": "has space"},
        {"attempt_id": "line\n"},
        {"attempt_id": "x" * 129},
    ],
)
def test_an_attempt_that_does_not_make_sense_is_refused(overrides):
    with pytest.raises(ValueError):
        attempt(**overrides)


def test_a_sequence_starts_at_one():
    base = {f.name: getattr(attempt(), f.name) for f in dataclasses.fields(WriteAttempt)}
    assert LedgerEntry(**base, sequence=1).sequence == 1
    with pytest.raises(ValueError):
        LedgerEntry(**base, sequence=0)


# ---- nothing personal -----------------------------------------------------------------------


def test_an_entry_has_no_field_that_could_hold_a_contact_or_a_value_of_the_source():
    assert {f.name for f in dataclasses.fields(LedgerEntry)} == {
        "client_id",
        "attempt_id",
        "prospect_id",
        "new",
        "expected",
        "outcome",
        "at",
        "field",
        "code",
        "not_before",
        "sequence",
    }
    assert {f.name for f in dataclasses.fields(WriteAttempt)} == {
        f.name for f in dataclasses.fields(LedgerEntry)
    } - {"sequence"}


def test_nothing_personal_is_in_the_entries_or_any_representation(ledger):
    ledger.append(
        [
            attempt("a", outcome=Outcome.DEFERRED, code=FailureCode.RATE_LIMITED, not_before=T0),
            attempt("b", "fake_crm:2", outcome=Outcome.FAILED, code=FailureCode.AUTH),
            attempt("c", "fake_crm:3", new=S.OPTED_OUT),
        ]
    )
    text = " ".join(
        [repr(ledger), str(ledger), repr(ledger.entries()), repr(attempt())]
        + [repr(e) for e in ledger.entries()]
    )
    for leak in (PHONE, "21699888777", "99 888", "Alex", "Example", "@"):
        assert leak not in text


def test_a_refusal_never_repeats_what_it_was_given():
    for bad in ({"attempt_id": PHONE}, {"prospect_id": ProspectId("inbound-1-21699888777")}):
        with pytest.raises(ValueError) as caught:
            attempt(**bad)
        assert PHONE not in str(caught.value) and "21699888777" not in str(caught.value)
    ledger = InMemoryWriteBackLedger(CLIENT)
    with pytest.raises(ValueError) as caught:
        ledger.append([attempt("p21699888777", client_id=OTHER)])
    assert "21699888777" not in str(caught.value)
