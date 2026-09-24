"""Slice 4: the Engine hands persisted facts to the Brain (the Sheets adapter is the FactsReader).

Nothing here extracts or invents a fact: they are recorded by the test, the way a later component
will, and the Engine only reads them.
"""

from datetime import timedelta

import pytest
from engine_harness import ScriptedBrain, inbound, phone_for, send
from factories import NOW, make_client_context
from sheets_support import make_repository

from sales_core.approval import ApprovalMode
from sales_core.contracts import Fact, FactStatus, ProspectId
from sales_core.engine import (
    Engine,
    EngineConfig,
    InboundPolicy,
    InboundStatus,
    UnknownSenderPolicy,
)
from sales_integrations.fakes import FakeClock, FakeMessagingChannel

P1 = ProspectId("p1")


def obs(key, value, at=NOW, **kw):
    return Fact(key=key, value=value, status=FactStatus.CONFIRMED, recorded_at=at, **kw)


class World:
    def __init__(self, **config):
        self.repo, self.api = make_repository()
        self.clock = FakeClock(NOW)
        self.brain = ScriptedBrain(lambda request: send("Hello!"))
        self.engine = Engine(
            client=make_client_context(),
            repository=self.repo,
            brain=self.brain,
            channel=FakeMessagingChannel(clock=self.clock),
            clock=self.clock,
            config=EngineConfig(mode=ApprovalMode.APPROVAL, **config),
            facts=self.repo,
        )

    def add_prospect(self, pid="p1"):
        self.api.human_add_row(
            "Prospects", prospect_id=pid, display_name="Alex", phone=phone_for(pid), status="new"
        )

    def reply(self, pid="p1", external_id="wamid.1"):
        receipt = self.engine.receive_inbound(inbound(pid=pid, external_id=external_id))
        return receipt, self.engine.process_inbound(receipt)


@pytest.fixture
def world():
    return World()


class TestOutbound:
    def test_persisted_facts_reach_the_brain_oldest_first(self, world):  # L
        world.add_prospect()
        world.repo.append_facts(
            P1,
            [
                obs("b", "2", NOW + timedelta(seconds=1)),
                obs("a", "1"),
                obs("a", "3", NOW + timedelta(days=1)),
            ],
        )
        world.engine.prepare()
        (request,) = world.brain.calls
        assert request.facts == (
            obs("a", "1"),
            obs("b", "2", NOW + timedelta(seconds=1)),
            obs("a", "3", NOW + timedelta(days=1)),
        )
        assert isinstance(request.facts, tuple)

    def test_a_prospect_with_no_facts_gets_an_empty_collection(self, world):  # M
        world.add_prospect()
        world.engine.prepare()
        assert world.brain.calls[0].facts == ()

    def test_only_the_prospects_own_facts_are_passed(self, world):
        world.add_prospect("p1")
        world.add_prospect("p2")
        world.repo.append_facts(ProspectId("p2"), [obs("secret", "theirs")])
        world.repo.append_facts(P1, [obs("mine", "1")])
        world.engine.prepare()
        by_prospect = {c.prospect.prospect_id: c.facts for c in world.brain.calls}
        assert by_prospect == {"p1": (obs("mine", "1"),), "p2": (obs("secret", "theirs"),)}

    def test_facts_are_read_fresh_for_every_request(self, world):
        world.add_prospect()
        world.engine.prepare()
        assert world.brain.calls[0].facts == ()
        world.repo.append_facts(P1, [obs("late", "1")])
        world.engine.prepare()  # nothing new is planned, but a later request reads afresh
        world.reply()
        assert world.brain.calls[-1].facts == (obs("late", "1"),)


class TestInbound:
    def test_a_reply_from_a_known_prospect_carries_the_persisted_facts(self, world):
        world.add_prospect()
        world.repo.append_facts(P1, [obs("k", "v")])
        _, report = world.reply()
        assert report.ok
        assert world.brain.calls[0].facts == (obs("k", "v"),)

    def test_a_reply_from_a_prospect_without_facts_gets_none(self, world):
        world.add_prospect()
        world.reply()
        assert world.brain.calls[0].facts == ()

    def test_an_inbound_created_prospect_reads_its_persisted_facts(self):
        world = World(
            inbound=InboundPolicy(
                spontaneous_conversations=True, unknown_sender=UnknownSenderPolicy.ENGAGE
            )
        )
        stranger = inbound(sender="+19998887777", external_id="wamid.s1")
        receipt = world.engine.receive_inbound(stranger)
        assert receipt.status is InboundStatus.STORED
        assert world.brain.calls == []  # facts are only ever read, never made up here
        assert list(world.repo.get_facts(receipt.prospect_id)) == []

        world.repo.append_facts(receipt.prospect_id, [obs("k", "recorded elsewhere")])
        report = world.engine.process_inbound(receipt)
        assert report.ok
        (request,) = world.brain.calls
        assert request.prospect.prospect_id == receipt.prospect_id
        assert request.facts == (obs("k", "recorded elsewhere"),)

    def test_the_engine_never_writes_facts_itself(self, world):
        world.add_prospect()
        world.reply()
        world.engine.prepare()
        assert world.api.rows("Facts") == []
