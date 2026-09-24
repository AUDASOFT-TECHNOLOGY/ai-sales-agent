"""The facts ports, checked against the fake AND the Google Sheets adapter.

Whatever the port says must be true of both, so the Engine (tested on the fake) and a client's real
spreadsheet behave the same, and a later SQL backend has a contract to meet.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from engine_harness import prospect
from factories import CLIENT, NOW
from sheets_support import TUNIS, make_repository

from sales_core.contracts import (
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    MessageId,
    ProspectId,
)
from sales_core.ports import FactsReader, FactsWriter
from sales_integrations.fakes import FakeSalesRepository
from sales_integrations.google_sheets import codecs

P1, P2 = ProspectId("p1"), ProspectId("p2")


class FakeEnv:
    def __init__(self):
        self.repo = FakeSalesRepository(CLIENT)

    def seed(self, *pids):
        self.repo.add_prospects(*(prospect(pid) for pid in pids))

    def writes(self):
        return 0


class SheetsEnv:
    def __init__(self):
        self.repo, self.api = make_repository(client_id="client-a")

    def seed(self, *pids):
        for pid in pids:
            cells = codecs.encode_prospect(prospect(pid), TUNIS)
            cells = {k: v for k, v in cells.items() if not k.startswith("@")}
            self.api.human_add_row("Prospects", prospect_id=pid, **cells)

    def writes(self):
        return self.api.count("append") + self.api.count("write") + self.api.count("add_tabs")


@pytest.fixture(params=["fake", "sheets"])
def env(request):
    made = FakeEnv() if request.param == "fake" else SheetsEnv()
    made.seed("p1", "p2")
    return made


def obs(key="role", value="manager", *, at=NOW, **kw):
    fields = {"status": FactStatus.DISCOVERED, "recorded_at": at} | kw
    return Fact(key=key, value=value, **fields)


def test_both_implementations_satisfy_the_facts_ports(env):
    assert isinstance(env.repo, FactsReader) and isinstance(env.repo, FactsWriter)


class TestReading:
    def test_no_facts_is_an_empty_collection(self, env):  # K
        assert list(env.repo.get_facts(P1)) == []

    def test_a_prospect_that_does_not_exist_simply_has_no_facts(self, env):
        assert list(env.repo.get_facts(ProspectId("nobody"))) == []

    def test_an_appended_fact_is_read_back_intact(self, env):  # C, F
        rich = obs(
            "note",
            "  spaced  value ",
            status=FactStatus.HYPOTHESIS,
            confidence=0.35,
            evidence=FactEvidence(
                source=FactSource.CONVERSATION,
                message_id=MessageId("in:whatsapp:w1"),
                excerpt="it depends",
                observed_at=NOW - timedelta(minutes=5),
                recorded_by="operator",
            ),
            updated_at=NOW + timedelta(minutes=1),
        )
        plain = obs("k2", "v2", at=NOW + timedelta(seconds=1))
        env.repo.append_facts(P1, [rich, plain])
        assert list(env.repo.get_facts(P1)) == [rich, plain]

    def test_prospects_never_see_each_others_facts(self, env):  # E
        env.repo.append_facts(P1, [obs(value="one")])
        env.repo.append_facts(P2, [obs(value="two")])
        assert [f.value for f in env.repo.get_facts(P1)] == ["one"]
        assert [f.value for f in env.repo.get_facts(P2)] == ["two"]

    def test_results_are_ordered_by_when_they_were_recorded_whatever_the_append_order(self, env):
        late, early = obs(value="late", at=NOW + timedelta(days=1)), obs(value="early")
        env.repo.append_facts(P1, [late])
        env.repo.append_facts(P1, [early])
        assert [f.value for f in env.repo.get_facts(P1)] == ["early", "late"]

    def test_facts_recorded_at_the_same_instant_keep_their_append_order(self, env):
        env.repo.append_facts(P1, [obs("b", "1"), obs("a", "2"), obs("c", "3")])
        assert [f.key for f in env.repo.get_facts(P1)] == ["b", "a", "c"]

    def test_repeated_reads_give_the_same_answer(self, env):
        env.repo.append_facts(
            P1, [obs("k", str(i), at=NOW + timedelta(seconds=i)) for i in range(4)]
        )
        assert list(env.repo.get_facts(P1)) == list(env.repo.get_facts(P1))


class TestAppending:
    def test_facts_accumulate_and_earlier_ones_are_preserved(self, env):  # F
        first, second = obs("a", "1"), obs("b", "2", at=NOW + timedelta(seconds=1))
        env.repo.append_facts(P1, [first])
        env.repo.append_facts(P1, [second])
        assert list(env.repo.get_facts(P1)) == [first, second]

    def test_the_same_key_observed_again_keeps_the_history(self, env):  # G, I
        manager = obs(value="manager")
        director = obs(value="director", at=NOW + timedelta(days=30))
        env.repo.append_facts(P1, [manager])
        env.repo.append_facts(P1, [director])
        history = list(env.repo.get_facts(P1))
        assert history == [manager, director]
        assert history[-1].value == "director"  # the newest observation is the current one

    def test_a_value_that_returns_is_recorded_again_not_swallowed(self, env):
        env.repo.append_facts(P1, [obs(value="manager")])
        env.repo.append_facts(P1, [obs(value="director", at=NOW + timedelta(days=1))])
        env.repo.append_facts(P1, [obs(value="manager", at=NOW + timedelta(days=2))])
        assert [f.value for f in env.repo.get_facts(P1)] == ["manager", "director", "manager"]

    def test_a_status_or_confidence_change_is_a_new_observation(self, env):
        env.repo.append_facts(P1, [obs(status=FactStatus.HYPOTHESIS, confidence=0.4)])
        env.repo.append_facts(P1, [obs(status=FactStatus.HYPOTHESIS, confidence=0.8)])
        env.repo.append_facts(P1, [obs(status=FactStatus.CONFIRMED)])
        assert len(env.repo.get_facts(P1)) == 3

    def test_a_retry_of_the_same_fact_creates_no_second_record(self, env):  # H
        fact = obs()
        env.repo.append_facts(P1, [fact])
        env.repo.append_facts(P1, [fact])
        env.repo.append_facts(P1, [replace(fact)])  # an equal copy, as a retry rebuilds it
        assert list(env.repo.get_facts(P1)) == [fact]

    def test_a_retry_writes_nothing_at_all(self, env):
        env.repo.append_facts(P1, [obs()])
        before = env.writes()
        env.repo.append_facts(P1, [obs()])
        assert env.writes() == before

    def test_a_repeat_inside_one_call_is_recorded_once(self, env):
        env.repo.append_facts(P1, [obs(), obs(), obs("k2", "v2")])
        assert [f.key for f in env.repo.get_facts(P1)] == ["role", "k2"]

    def test_a_retry_with_some_new_facts_records_only_the_new_ones(self, env):
        first = obs("a", "1")
        env.repo.append_facts(P1, [first])
        env.repo.append_facts(P1, [first, obs("b", "2", at=NOW + timedelta(seconds=1))])
        assert [f.key for f in env.repo.get_facts(P1)] == ["a", "b"]

    def test_the_same_fact_for_another_prospect_is_its_own_record(self, env):
        env.repo.append_facts(P1, [obs()])
        env.repo.append_facts(P2, [obs()])
        assert len(env.repo.get_facts(P1)) == len(env.repo.get_facts(P2)) == 1

    def test_appending_nothing_is_a_no_op(self, env):
        before = env.writes()
        env.repo.append_facts(P1, [])
        assert env.writes() == before and list(env.repo.get_facts(P1)) == []


class TestRefusals:
    def test_an_unknown_prospect_is_refused_and_nothing_is_written(self, env):
        before = env.writes()
        with pytest.raises(KeyError, match="nobody"):
            env.repo.append_facts(ProspectId("nobody"), [obs()])
        assert env.writes() == before and list(env.repo.get_facts(ProspectId("nobody"))) == []

    def test_a_fact_without_recorded_at_is_refused_and_the_whole_call_writes_nothing(self, env):
        before = env.writes()
        with pytest.raises(ValueError, match="recorded_at"):
            env.repo.append_facts(P1, [obs("ok"), obs("undated", at=None)])
        assert env.writes() == before and list(env.repo.get_facts(P1)) == []

    def test_the_repository_is_bound_to_its_client(self, env):
        assert env.repo.client_id == CLIENT
