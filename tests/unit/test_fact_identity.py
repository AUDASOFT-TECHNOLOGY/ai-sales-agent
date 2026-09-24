"""Slice 4: the identity that makes recording a fact idempotent, in every storage backend."""

from dataclasses import replace
from datetime import timedelta, timezone

import pytest
from factories import NOW

from sales_core.contracts import (
    ClientId,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    MessageId,
    ProspectId,
    fact_identity,
)
from sales_core.ports import FactsReader, FactsWriter

CLIENT = ClientId("client-a")
P1 = ProspectId("p1")


def obs(**overrides):
    fields = {
        "key": "role",
        "value": "manager",
        "status": FactStatus.DISCOVERED,
        "recorded_at": NOW,
    }
    return Fact(**(fields | overrides))


def ident(fact=None, *, client=CLIENT, prospect=P1):
    return fact_identity(client, prospect, fact or obs())


class TestFactIdentity:
    def test_the_same_fact_always_has_the_same_identity(self):
        assert ident(obs()) == ident(obs())

    def test_it_is_a_readable_opaque_string(self):
        assert ident().startswith("fact-") and len(ident()) == len("fact-") + 24

    @pytest.mark.parametrize(
        "change",
        [
            {"value": "director"},
            {"key": "topic"},
            {"status": FactStatus.CONFIRMED},
            {"confidence": 0.5},
            {"recorded_at": NOW + timedelta(seconds=1)},
            {"evidence": FactEvidence(source=FactSource.PROSPECT)},
        ],
    )
    def test_any_difference_in_what_was_observed_is_another_observation(self, change):
        assert ident(replace(obs(), **change)) != ident(obs())

    def test_the_same_key_with_another_value_later_keeps_both_identities(self):
        earlier, later = obs(value="manager"), obs(value="director", recorded_at=NOW + timedelta(1))
        assert ident(earlier) != ident(later)

    def test_a_value_that_comes_back_later_is_a_new_observation(self):
        """manager, director, then manager again: the third is not the first."""
        first = obs(value="manager")
        again = obs(value="manager", recorded_at=NOW + timedelta(days=2))
        assert ident(first) != ident(again)

    def test_every_evidence_detail_counts(self):
        base = FactEvidence(source=FactSource.CONVERSATION, message_id=MessageId("m1"))
        for other in (
            replace(base, message_id=MessageId("m2")),
            replace(base, excerpt="said so"),
            replace(base, observed_at=NOW),
            replace(base, recorded_by="op"),
            replace(base, source=FactSource.HUMAN),
        ):
            assert ident(obs(evidence=other)) != ident(obs(evidence=base))

    def test_whose_fact_it_is_counts(self):
        assert ident(prospect=ProspectId("p2")) != ident()
        assert ident(client=ClientId("client-b")) != ident()

    def test_updated_at_says_when_a_record_was_touched_not_what_was_observed(self):
        assert ident(obs(updated_at=NOW + timedelta(hours=1))) == ident(obs())

    def test_the_instant_matters_not_the_zone_it_is_written_in(self):
        plus_one = timezone(timedelta(hours=1))
        assert ident(obs(recorded_at=NOW.astimezone(plus_one))) == ident()

    def test_an_integer_and_a_float_confidence_are_the_same_observation(self):
        assert ident(obs(confidence=1)) == ident(obs(confidence=1.0))

    def test_a_fact_without_recorded_at_has_no_identity(self):
        with pytest.raises(ValueError, match="recorded_at"):
            ident(obs(recorded_at=None))


def test_reading_and_writing_are_separate_ports():
    class OnlyReads:
        def get_facts(self, prospect_id):
            return ()

    class OnlyWrites:
        client_id = CLIENT

        def append_facts(self, prospect_id, facts):
            return None

    assert isinstance(OnlyReads(), FactsReader) and not isinstance(OnlyReads(), FactsWriter)
    assert isinstance(OnlyWrites(), FactsWriter) and not isinstance(OnlyWrites(), FactsReader)
