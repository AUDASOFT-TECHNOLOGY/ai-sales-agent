"""Lanes in the Engine: two fake channels, one client, and what stays exactly as it was without lanes.

Everything is in memory (fake repository, fake channels, scripted Brain): no network, no database.
These tests prove the Core's routing, attribution and pinning, not any real WhatsApp, e-mail or CRM.
"""

from dataclasses import replace

import pytest
from engine_harness import ScriptedBrain, inbound, send
from factories import CLIENT, NOW, make_client_context, make_prospect

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    Channel,
    ClientId,
    ContactInfo,
    LaneRoute,
    ProspectId,
    ProspectStatus,
)
from sales_core.engine import (
    ChannelRouter,
    Engine,
    EngineConfig,
    InboundPolicy,
    UnknownSenderPolicy,
)
from sales_integrations.fakes import FakeClock, FakeMessagingChannel, FakeSalesRepository

SHEET_A = LaneRoute(
    lane_id="sheet-a", channel=Channel.WHATSAPP, product_ids=("a", "b"), source_id="sheeta"
)
CRM = LaneRoute(lane_id="crm", channel=Channel.EMAIL, product_ids=("c",), source_id="crm")


def person(pid: str, *, phone: str | None = None, email: str | None = None, **kw):
    return make_prospect(
        prospect_id=ProspectId(pid),
        display_name=pid,
        contact=ContactInfo(phone=phone, email=email),
        **kw,
    )


class World:
    """One client with a WhatsApp channel and an e-mail channel, and the lanes asked for."""

    def __init__(
        self,
        prospects=(),
        lanes=(SHEET_A, CRM),
        *,
        default_lane=None,
        mode=ApprovalMode.AUTONOMOUS,
        client_id: str = CLIENT,
        inbound_policy: InboundPolicy | None = None,
        repo=None,
        extra_channels=(),
    ):
        self.clock = FakeClock(NOW)
        self.client_id = ClientId(client_id)
        self.repo = repo or FakeSalesRepository(self.client_id, prospects=prospects)
        self.whatsapp = FakeMessagingChannel(Channel.WHATSAPP, clock=self.clock)
        self.email = FakeMessagingChannel(Channel.EMAIL, clock=self.clock)
        self.channels = {Channel.WHATSAPP: self.whatsapp, Channel.EMAIL: self.email}
        for extra in extra_channels:
            self.channels[extra.channel] = extra
        self.brain = ScriptedBrain()
        self.mode = mode
        self.lanes, self.default_lane = lanes, default_lane
        self.inbound_policy = inbound_policy or InboundPolicy(spontaneous_conversations=True)
        self.engine = self.new_engine()

    def new_engine(self, *, lanes=None, brain=None) -> Engine:
        """A fresh Engine over the same repository, as after a process restart."""
        return Engine(
            client=make_client_context(client_id=self.client_id),
            repository=self.repo,
            brain=brain or self.brain,
            router=ChannelRouter(
                self.channels,
                self.lanes if lanes is None else lanes,
                default_lane=self.default_lane,
            ),
            clock=self.clock,
            config=EngineConfig(mode=self.mode, inbound=self.inbound_policy),
        )

    def prospect(self, pid: str):
        found = self.repo.get_prospect(ProspectId(pid))
        assert found is not None
        return found

    def action_of(self, pid: str):
        (only,) = [a for a in self.repo.list_actions() if a.prospect_id == pid]
        return only


A1 = "sheeta:1"
C1 = "crm:1"
PHONE = "+21620000001"
MAIL = "lead@example.org"


def two_sources() -> World:
    return World([person(A1, phone=PHONE), person(C1, email=MAIL)])


# ---- two lanes, two channels --------------------------------------------------------------


def test_each_prospect_is_written_on_the_channel_of_its_lane():
    w = two_sources()
    w.engine.run_cycle()
    assert [m.prospect_id for m in w.whatsapp.sent] == [A1]
    assert [m.prospect_id for m in w.email.sent] == [C1]
    assert w.whatsapp.sent[0].recipient == PHONE and w.email.sent[0].recipient == MAIL


def test_the_planned_actions_keep_their_lane_and_channel():
    w = two_sources()
    w.engine.run_cycle()
    assert (w.action_of(A1).lane_id, w.action_of(A1).channel) == ("sheet-a", Channel.WHATSAPP)
    assert (w.action_of(C1).lane_id, w.action_of(C1).channel) == ("crm", Channel.EMAIL)


def test_the_outbound_message_is_recorded_on_the_channel_it_went_out_on():
    w = two_sources()
    w.engine.run_cycle()
    (message,) = w.repo.get_conversation(ProspectId(C1)).messages
    assert message.channel is Channel.EMAIL


def test_the_brain_is_told_the_lane_and_so_the_products_in_scope():
    w = two_sources()
    w.engine.prepare()
    lanes = {c.prospect.prospect_id: c.lane for c in w.brain.calls}
    assert lanes[A1].product_ids == ("a", "b") and lanes[C1].product_ids == ("c",)


def test_two_lanes_may_share_a_channel():
    other = LaneRoute(
        lane_id="sheet-b", channel=Channel.WHATSAPP, product_ids=("a",), source_id="sheetb"
    )
    w = World(
        [person("sheeta:1", phone=PHONE), person("sheetb:1", phone="+21620000002")],
        lanes=(SHEET_A, other),
    )
    w.engine.run_cycle()
    assert sorted(m.prospect_id for m in w.whatsapp.sent) == ["sheeta:1", "sheetb:1"]
    assert w.email.sent == ()
    assert w.prospect("sheetb:1").lane_id == "sheet-b"


def test_a_prospect_on_a_channel_where_it_must_speak_first_is_skipped_only_for_that_lane():
    telegram = FakeMessagingChannel(Channel.TELEGRAM, clock=FakeClock(NOW))
    tg_lane = LaneRoute(
        lane_id="tg", channel=Channel.TELEGRAM, product_ids=("a",), source_id="tgsrc"
    )
    w = World(
        [person(A1, phone=PHONE), person("tgsrc:1", phone="+21620000009")],
        lanes=(SHEET_A, tg_lane),
        extra_channels=(telegram,),
    )
    w.engine.run_cycle()
    assert [m.prospect_id for m in w.whatsapp.sent] == [A1]  # the other lane did not hold it back
    assert telegram.sent == ()  # no chat id yet: waiting for them to say hello
    assert [p.prospect_id for p in w.engine.prospects_awaiting_opt_in()] == ["tgsrc:1"]


# ---- attribution is written once and never moved ------------------------------------------


def test_the_attribution_is_written_once_and_audited():
    w = two_sources()
    w.engine.prepare()
    assert w.prospect(A1).lane_id == "sheet-a" and w.prospect(C1).lane_id == "crm"
    events = [e for e in w.repo.audit_events if e.event_type == "prospect.lane_assigned"]
    assert sorted(e.prospect_id for e in events) == sorted([A1, C1])
    w.engine.prepare()  # nothing left to attribute
    assert len([e for e in w.repo.audit_events if e.event_type == "prospect.lane_assigned"]) == 2


def test_a_prospect_already_attributed_is_never_moved_whatever_its_id_says():
    w = World([person(A1, phone=PHONE, lane_id="crm")])
    w.engine.prepare()
    assert w.prospect(A1).lane_id == "crm"


def test_a_prospect_of_a_lane_that_no_longer_exists_is_never_written_to():
    w = World([person(A1, phone=PHONE, lane_id="gone")])
    w.engine.run_cycle()
    assert w.whatsapp.sent == () and w.email.sent == ()
    assert w.repo.list_actions() == []  # not even planned: it is in no lane


def test_a_prospect_matching_no_source_goes_to_the_default_lane_or_to_none():
    stranger = person("zzz9", phone=PHONE)
    with_default = World([stranger], default_lane="sheet-a")
    with_default.engine.run_cycle()
    assert with_default.prospect("zzz9").lane_id == "sheet-a"
    assert [m.prospect_id for m in with_default.whatsapp.sent] == ["zzz9"]

    without = World([stranger])
    without.engine.run_cycle()
    assert without.prospect("zzz9").lane_id is None and without.whatsapp.sent == ()


def test_a_dry_run_attributes_in_memory_only():
    w = World([person(A1, phone=PHONE)], mode=ApprovalMode.DRY_RUN)
    w.engine.prepare()
    assert w.prospect(A1).lane_id is None  # nothing was written
    assert w.whatsapp.sent == ()


def test_a_failed_attribution_write_does_not_stop_the_run_and_is_reported():
    w = two_sources()
    w.repo.fail_next("update_prospects", RuntimeError("store down"))
    report = w.engine.prepare()
    assert any("lane" in e for e in report.errors)
    assert w.prospect(A1).lane_id is None  # recomputed next time, deterministically
    w.engine.prepare()
    assert w.prospect(A1).lane_id == "sheet-a"


# ---- after a restart -----------------------------------------------------------------------


def test_an_action_keeps_its_channel_after_a_restart_even_if_the_lane_is_reconfigured():
    w = World([person(C1, email=MAIL)], mode=ApprovalMode.APPROVAL)
    w.engine.prepare()
    (proposal,) = w.engine.pending_proposals()
    assert w.action_of(C1).channel is Channel.EMAIL
    # restart, with the CRM lane now pointed at WhatsApp
    moved = replace(CRM, channel=Channel.WHATSAPP)
    restarted = w.new_engine(lanes=(SHEET_A, moved))
    restarted.approve_proposal(proposal.action_id, actor="test")
    restarted.dispatch()
    assert [m.prospect_id for m in w.email.sent] == [C1]  # it went where it was planned for
    assert w.whatsapp.sent == ()


def test_work_pinned_to_a_channel_the_client_no_longer_has_is_blocked_not_redirected():
    w = World([person(C1, email=MAIL)], mode=ApprovalMode.APPROVAL)
    w.engine.prepare()
    (proposal,) = w.engine.pending_proposals()
    w.channels = {Channel.WHATSAPP: w.whatsapp}  # e-mail is gone
    restarted = w.new_engine(lanes=(SHEET_A,))
    with pytest.raises(Exception, match="can no longer be contacted"):
        restarted.approve_proposal(proposal.action_id, actor="test")
    assert w.whatsapp.sent == ()


def test_the_lane_survives_a_restart_and_is_not_attributed_again():
    w = two_sources()
    w.engine.prepare()
    again = w.new_engine()
    again.prepare()
    assert [
        e.prospect_id for e in w.repo.audit_events if e.event_type == "prospect.lane_assigned"
    ] == [
        A1,
        C1,
    ]


# ---- replies and strangers ------------------------------------------------------------------


def test_a_reply_goes_out_on_the_channel_the_person_wrote_on():
    w = World([person(A1, phone=PHONE, email=MAIL)])
    w.engine.prepare()
    message = inbound("Tell me more", sender=MAIL, channel=Channel.EMAIL)
    receipt = w.engine.receive_inbound(message)
    w.engine.process_inbound(receipt)
    reply = [a for a in w.repo.list_actions() if a.action_type.value == "reply"]
    assert [(a.lane_id, a.channel) for a in reply] == [("sheet-a", Channel.EMAIL)]
    assert [m.recipient for m in w.email.sent] == [MAIL]


def test_a_stranger_who_writes_is_put_in_the_lane_written_to_on_that_channel():
    policy = InboundPolicy(
        spontaneous_conversations=True, unknown_sender=UnknownSenderPolicy.ENGAGE
    )
    w = World(lanes=(SHEET_A, CRM), inbound_policy=policy)
    receipt = w.engine.receive_inbound(
        inbound("hello", sender="new@example.org", channel=Channel.EMAIL, external_id="m1")
    )
    assert w.prospect(receipt.prospect_id).lane_id == "crm"


# ---- isolation ------------------------------------------------------------------------------


def test_two_clients_with_the_same_lane_names_never_share_a_channel_or_a_prospect():
    one = World([person(A1, phone=PHONE, client_id=ClientId("client-a"))], client_id="client-a")
    two = World([person(A1, phone=PHONE, client_id=ClientId("client-b"))], client_id="client-b")
    one.engine.run_cycle()
    assert [m.client_id for m in one.whatsapp.sent] == ["client-a"]
    assert two.whatsapp.sent == () and two.repo.list_actions() == []
    two.engine.run_cycle()
    assert [m.client_id for m in two.whatsapp.sent] == ["client-b"]
    assert [m.client_id for m in one.whatsapp.sent] == ["client-a"]


def test_an_engine_refuses_a_repository_of_another_client_even_with_lanes():
    w = two_sources()
    with pytest.raises(ValueError, match="different client"):
        Engine(
            client=make_client_context(client_id=ClientId("client-b")),
            repository=w.repo,
            brain=w.brain,
            router=ChannelRouter(w.channels, (SHEET_A,)),
            clock=w.clock,
            config=EngineConfig(mode=w.mode),
        )


# ---- the router itself -----------------------------------------------------------------------


def test_a_router_refuses_what_cannot_work():
    wa, mail = FakeMessagingChannel(Channel.WHATSAPP), FakeMessagingChannel(Channel.EMAIL)
    with pytest.raises(ValueError, match="at least one"):
        ChannelRouter({})
    with pytest.raises(ValueError, match="exactly one"):
        ChannelRouter({Channel.WHATSAPP: wa, Channel.EMAIL: mail})
    with pytest.raises(ValueError, match="listed twice"):
        ChannelRouter({Channel.WHATSAPP: wa}, (SHEET_A, SHEET_A))
    with pytest.raises(ValueError, match="no adapter"):
        ChannelRouter({Channel.WHATSAPP: wa}, (CRM,))
    with pytest.raises(ValueError, match="default lane"):
        ChannelRouter({Channel.WHATSAPP: wa}, (SHEET_A,), default_lane="nope")
    with pytest.raises(ValueError, match="filed under"):
        ChannelRouter({Channel.WHATSAPP: mail})


def test_a_lane_cannot_list_a_product_twice():
    with pytest.raises(ValueError, match="twice"):
        LaneRoute(lane_id="x", channel=Channel.EMAIL, product_ids=("a", "a"))


# ---- a client with no lanes is exactly what it was ---------------------------------------------


def test_a_client_with_one_channel_and_no_lanes_is_untouched():
    clock = FakeClock(NOW)
    repo = FakeSalesRepository(CLIENT, prospects=[person("p1", phone=PHONE)])
    channel = FakeMessagingChannel(Channel.WHATSAPP, clock=clock)
    brain = ScriptedBrain(lambda request: send())
    engine = Engine(
        client=make_client_context(),
        repository=repo,
        brain=brain,
        channel=channel,
        clock=clock,
        config=EngineConfig(mode=ApprovalMode.AUTONOMOUS),
    )
    engine.run_cycle()
    (action,) = repo.list_actions()
    assert (action.lane_id, action.channel) == (None, None)  # nothing pinned
    assert repo.get_prospect(ProspectId("p1")).lane_id is None  # nothing attributed
    assert brain.calls[0].lane is None
    assert not [e for e in repo.audit_events if e.event_type == "prospect.lane_assigned"]
    assert [m.prospect_id for m in channel.sent] == ["p1"]
    assert repo.get_prospect(ProspectId("p1")).status is not ProspectStatus.NEW


def test_the_constructor_takes_exactly_one_of_channel_and_router():
    clock = FakeClock(NOW)
    repo = FakeSalesRepository(CLIENT)
    channel = FakeMessagingChannel(Channel.WHATSAPP, clock=clock)
    common = {
        "client": make_client_context(),
        "repository": repo,
        "brain": ScriptedBrain(),
        "clock": clock,
        "config": EngineConfig(mode=ApprovalMode.APPROVAL),
    }
    with pytest.raises(ValueError, match="exactly one"):
        Engine(**common)
    with pytest.raises(ValueError, match="exactly one"):
        Engine(**common, channel=channel, router=ChannelRouter.single(channel))
