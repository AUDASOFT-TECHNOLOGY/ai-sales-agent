"""`Engine.summarize_prospects`: the counts behind the client's summary, computed in the Core.

In memory only (fake repository): no network, no database.
"""

from datetime import timedelta

from engine_harness import ScriptedBrain
from factories import CLIENT, NOW, make_client_context, make_prospect

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    Channel,
    ClientId,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)
from sales_core.engine import ChannelRouter, Engine, EngineConfig
from sales_integrations.fakes import FakeClock, FakeMessagingChannel, FakeSalesRepository


def engine_with(*prospects) -> Engine:
    clock = FakeClock(NOW)
    return Engine(
        client=make_client_context(client_id=ClientId(CLIENT)),
        repository=FakeSalesRepository(ClientId(CLIENT), prospects=prospects),
        brain=ScriptedBrain(),
        router=ChannelRouter(
            {Channel.WHATSAPP: FakeMessagingChannel(Channel.WHATSAPP, clock=clock)}, ()
        ),
        clock=clock,
        config=EngineConfig(mode=ApprovalMode.AUTONOMOUS),
    )


def person(pid: str, status: ProspectStatus, kind=None, days=None):
    schedule = (
        Scheduling(next_action_type=kind, next_action_due_at=NOW + timedelta(days=days))
        if kind is not None
        else Scheduling()
    )
    return make_prospect(
        prospect_id=ProspectId(pid), display_name=pid, status=status, schedule=schedule
    )


ALL_STATUSES = {"new", "active", "paused", "opted_out", "closed"}


def test_no_prospects_gives_real_zeros_and_no_next_date():
    summary = engine_with().summarize_prospects()
    assert summary.total == 0
    assert summary.by_status == dict.fromkeys(ALL_STATUSES, 0)
    assert summary.upcoming.scheduled == 0
    assert summary.upcoming.by_type == {}
    assert summary.upcoming.next_due_at is None


def test_it_counts_every_status():
    summary = engine_with(
        person("p1", ProspectStatus.NEW),
        person("p2", ProspectStatus.ACTIVE),
        person("p3", ProspectStatus.ACTIVE),
        person("p4", ProspectStatus.PAUSED),
        person("p5", ProspectStatus.OPTED_OUT),
        person("p6", ProspectStatus.CLOSED),
    ).summarize_prospects()
    assert summary.total == 6
    assert summary.by_status == {
        "new": 1,
        "active": 2,
        "paused": 1,
        "opted_out": 1,
        "closed": 1,
    }


def test_only_actions_of_prospects_that_can_still_be_contacted_are_upcoming():
    follow, callback = SalesActionType.FOLLOW_UP, SalesActionType.CALLBACK
    summary = engine_with(
        person("p1", ProspectStatus.NEW, follow, 3),
        person("p2", ProspectStatus.ACTIVE, follow, 1),
        person("p3", ProspectStatus.ACTIVE, callback, 2),
        person("p4", ProspectStatus.PAUSED, follow, 0),
        person("p5", ProspectStatus.OPTED_OUT, callback, 0),
        person("p6", ProspectStatus.CLOSED, follow, 0),
        person("p7", ProspectStatus.ACTIVE),  # nothing recorded
    ).summarize_prospects()
    assert summary.upcoming.scheduled == 3
    assert summary.upcoming.by_type == {"follow_up": 2, "callback": 1}
    assert summary.upcoming.next_due_at == NOW + timedelta(days=1)
