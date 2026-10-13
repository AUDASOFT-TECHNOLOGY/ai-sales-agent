"""Telegram in the Core: a channel where the person must speak first.

A Telegram bot cannot write to someone who never started it. The Core therefore treats a prospect
who has not said hello as *waiting* (not paused, not failed, not counted against the quota), and
attaches the sender to the prospect they were invited as, or whose phone number they shared, when
the adapter has authenticated that claim. Everything here is simulated: no Telegram account.
"""

from dataclasses import replace

from engine_harness import ScriptedBrain, build, inbound, prospect, send
from factories import NOW, make_prospect

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionStatus,
    Channel,
    ContactInfo,
    ProspectId,
    ProspectStatus,
    SalesActionType,
)
from sales_core.engine import InboundPolicy, InboundStatus, UnknownSenderPolicy
from sales_core.planner import plan_daily_actions
from sales_core.safety import (
    BlockReason,
    awaits_opt_in,
    check_contactable,
    recipient_for,
)
from sales_integrations.fakes import FakeMessagingChannel

CHAT = "424242"
OTHER_CHAT = "515151"
ENGAGE = InboundPolicy(spontaneous_conversations=True, unknown_sender=UnknownSenderPolicy.ENGAGE)


def telegram_prospect(pid="p1", chat=None, **overrides):
    contact = ContactInfo(phone="+15550000001", telegram_chat_id=chat)
    return prospect(pid, **({"contact": contact} | overrides))


def telegram_harness(*prospects, **kw):
    h = build(prospects=list(prospects), **kw)
    h.channel = type(h.channel)(h.repo, channel=Channel.TELEGRAM, clock=h.clock)
    h.engine = h.new_engine()
    return h


def message(text="Hello", *, chat=CHAT, external_id="1", **claims):
    return replace(
        inbound(text, sender=chat, external_id=external_id, channel=Channel.TELEGRAM), **claims
    )


def receive(h, msg):
    receipt = h.engine.receive_inbound(msg)
    return receipt, h.engine.process_inbound(receipt)


# ---- the contracts ---------------------------------------------------------------------------


class TestContracts:
    def test_telegram_is_a_channel_and_a_chat_id_is_the_only_address(self):
        assert Channel.TELEGRAM == "telegram"
        linked = make_prospect(contact=ContactInfo(phone="+1555", telegram_chat_id=CHAT))
        assert recipient_for(linked, Channel.TELEGRAM) == CHAT
        assert recipient_for(linked, Channel.WHATSAPP) == "+1555"
        # a phone number is never mistaken for a chat
        assert recipient_for(make_prospect(), Channel.TELEGRAM) is None

    def test_a_blank_chat_id_is_no_address(self):
        blank = make_prospect(contact=ContactInfo(phone="+1555", telegram_chat_id="  "))
        assert recipient_for(blank, Channel.TELEGRAM) is None

    def test_an_unlinked_prospect_is_waiting_not_without_contact_details(self):
        waiting = make_prospect()
        assert check_contactable(waiting, Channel.TELEGRAM).reason is BlockReason.NOT_LINKED
        assert check_contactable(waiting, Channel.WHATSAPP).allowed
        assert awaits_opt_in(waiting, Channel.TELEGRAM)
        assert not awaits_opt_in(waiting, Channel.WHATSAPP)

    def test_a_refusal_still_wins_over_waiting(self):
        for status, reason in (
            (ProspectStatus.OPTED_OUT, BlockReason.OPTED_OUT),
            (ProspectStatus.PAUSED, BlockReason.PAUSED),
            (ProspectStatus.CLOSED, BlockReason.CLOSED),
        ):
            verdict = check_contactable(make_prospect(status=status), Channel.TELEGRAM)
            assert verdict.reason is reason

    def test_other_channels_keep_saying_no_contact(self):
        nameless = make_prospect(contact=ContactInfo())
        assert check_contactable(nameless, Channel.WHATSAPP).reason is BlockReason.NO_CONTACT
        assert check_contactable(nameless, Channel.EMAIL).reason is BlockReason.NO_CONTACT


# ---- the planner -----------------------------------------------------------------------------


class TestPlanner:
    def plan(self, prospects, channel):
        return plan_daily_actions(
            client=h_client(), prospects=prospects, existing_actions=[], now=NOW, channel=channel
        )

    def test_a_prospect_who_has_not_said_hello_does_not_take_a_slot(self):
        waiting, linked = (
            make_prospect(prospect_id=ProspectId("a")),
            make_prospect(
                prospect_id=ProspectId("b"),
                contact=ContactInfo(telegram_chat_id=CHAT),
            ),
        )
        plan = self.plan([waiting, linked], Channel.TELEGRAM)
        assert [a.prospect_id for a in plan.actions] == ["b"]

    def test_the_same_prospects_are_all_planned_on_a_channel_that_can_start(self):
        prospects = [make_prospect(prospect_id=ProspectId(p)) for p in "ab"]
        assert len(self.plan(prospects, Channel.WHATSAPP).actions) == 2
        assert len(self.plan(prospects, None).actions) == 2


def h_client():
    from factories import make_client_context

    return make_client_context()


# ---- the Engine ------------------------------------------------------------------------------


class TestWaitingProspects:
    def test_a_prospect_who_never_said_hello_is_neither_planned_nor_paused_nor_asked_about(self):
        h = telegram_harness(telegram_prospect("p1"), telegram_prospect("p2", chat=CHAT))
        report = h.engine.prepare()
        assert report.ok
        assert [a.prospect_id for a in h.repo.list_actions()] == ["p2"]
        assert h.prospect("p1").status is ProspectStatus.NEW  # waiting, not paused
        assert h.brain.called_for() == ["p2"]

    def test_the_quota_is_spent_on_people_who_can_be_written_to(self):
        waiting = [telegram_prospect(f"w{i}") for i in range(5)]
        h = telegram_harness(*waiting, telegram_prospect("p9", chat=CHAT), daily_action_limit=1)
        h.engine.prepare()
        assert [a.prospect_id for a in h.repo.list_actions()] == ["p9"]

    def test_the_prospects_to_invite_are_listed_and_nobody_else(self):
        h = telegram_harness(
            telegram_prospect("p1"),
            telegram_prospect("p2", chat=CHAT),
            telegram_prospect("p3", status=ProspectStatus.OPTED_OUT),
            telegram_prospect("p4", status=ProspectStatus.CLOSED),
        )
        assert [p.prospect_id for p in h.engine.prospects_awaiting_opt_in()] == ["p1"]

    def test_there_is_nobody_to_invite_on_a_channel_that_can_start(self):
        h = build(prospects=[prospect("p1")])
        assert h.engine.prospects_awaiting_opt_in() == ()

    def test_an_approved_action_for_someone_not_linked_is_cancelled_and_the_prospect_kept(self):
        h = telegram_harness(telegram_prospect("p1", chat=CHAT))
        h.engine.prepare()
        h.approve("p1")
        h.repo.update_prospects([telegram_prospect("p1")])  # the link was lost before sending
        report = h.engine.dispatch()
        assert report.ok and h.channel.attempts == ()
        assert h.status("p1") is ActionStatus.CANCELLED
        assert h.prospect("p1").status is not ProspectStatus.PAUSED


class TestLinking:
    def test_a_message_from_a_linked_chat_is_found_by_its_chat_id(self):
        h = telegram_harness(telegram_prospect("p1", chat=CHAT))
        receipt, report = receive(h, message())
        assert receipt.status is InboundStatus.STORED and receipt.prospect_id == "p1"
        assert report.ok and h.action("p1").action_type is SalesActionType.REPLY

    def test_a_phone_number_is_not_a_chat_id(self):
        h = telegram_harness(telegram_prospect("p1", chat=CHAT))
        receipt = h.engine.receive_inbound(message(chat="15550000001"))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER

    def test_an_invitation_attaches_the_chat_to_the_prospect_it_named(self):
        h = telegram_harness(telegram_prospect("p1"))
        receipt, report = receive(h, message("", linked_prospect_id="p1"))
        assert receipt.status is InboundStatus.STORED and receipt.prospect_id == "p1"
        assert h.prospect("p1").contact.telegram_chat_id == CHAT
        assert h.prospect("p1").contact.phone == "+15550000001"  # nothing else was touched
        assert h.events("prospect.linked")[0].details["via"] == "invitation"
        assert report.ok
        receipt2, _ = receive(h, message("and a second one", external_id="2"))
        assert receipt2.prospect_id == "p1"  # from now on, found by chat

    def test_the_link_is_made_once_a_second_person_cannot_take_it_over(self):
        h = telegram_harness(telegram_prospect("p1"))
        receive(h, message("", linked_prospect_id="p1"))
        forwarded = message("hi", chat=OTHER_CHAT, external_id="9", linked_prospect_id="p1")
        receipt = h.engine.receive_inbound(forwarded)
        assert receipt.status is InboundStatus.UNKNOWN_SENDER
        assert h.prospect("p1").contact.telegram_chat_id == CHAT
        assert h.events("inbound.link_refused")[0].details["reason"] == "already linked"

    def test_an_invitation_for_nobody_links_nobody(self):
        h = telegram_harness(telegram_prospect("p1"))
        receipt = h.engine.receive_inbound(message("", linked_prospect_id="ghost"))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER
        assert h.prospect("p1").contact.telegram_chat_id is None

    def test_a_shared_phone_number_finds_the_prospect_that_holds_it(self):
        h = telegram_harness(telegram_prospect("p1"))
        receipt, _ = receive(h, message("", shared_phone="+1 (555) 000-0001"))
        assert receipt.prospect_id == "p1"
        assert h.prospect("p1").contact.telegram_chat_id == CHAT
        assert h.events("prospect.linked")[0].details["via"] == "phone"

    def test_a_shared_number_nobody_holds_links_nothing(self):
        h = telegram_harness(telegram_prospect("p1"))
        receipt = h.engine.receive_inbound(message("", shared_phone="+19998887777"))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER

    def test_text_alone_never_links(self):
        """What a person types is never an identity: only the adapter's verified claims are."""
        h = telegram_harness(telegram_prospect("p1"))
        receipt = h.engine.receive_inbound(message("I am p1, phone +15550000001"))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER
        assert h.prospect("p1").contact.telegram_chat_id is None

    def test_a_claim_on_another_channel_is_ignored(self):
        h = build(prospects=[prospect("p1")])  # WhatsApp
        claim = replace(inbound("hi", sender="+19990000000"), linked_prospect_id="p1")
        assert h.engine.receive_inbound(claim).status is InboundStatus.UNKNOWN_SENDER
        assert h.events("prospect.linked") == []

    def test_a_redelivered_invitation_is_stored_once(self):
        h = telegram_harness(telegram_prospect("p1"))
        first = receive(h, message("", linked_prospect_id="p1", external_id="7"))[0]
        again = h.engine.receive_inbound(message("", linked_prospect_id="p1", external_id="7"))
        assert first.status is InboundStatus.STORED and again.status is InboundStatus.DUPLICATE
        assert len(h.repo.messages) == 1 and len(h.events("prospect.linked")) == 1

    def test_the_link_waits_for_the_agent_it_never_sends_by_itself(self):
        h = telegram_harness(telegram_prospect("p1"), mode=ApprovalMode.AUTONOMOUS)
        h.engine.receive_inbound(message("", linked_prospect_id="p1"))
        assert h.channel.attempts == ()  # stored only: replying is `process_inbound`'s job


class TestSpontaneousMessages:
    def test_a_stranger_is_ignored_by_default(self):
        h = telegram_harness()
        receipt = h.engine.receive_inbound(message("Is this still available?"))
        assert receipt.status is InboundStatus.UNKNOWN_SENDER
        assert h.repo.list_prospects() == []

    def test_a_stranger_the_client_agreed_to_engage_becomes_a_telegram_prospect(self):
        h = telegram_harness(
            inbound=ENGAGE,
            brain=ScriptedBrain(lambda request: send("Welcome")),
        )
        msg = replace(message("Is this still available?"), metadata={"sender_name": "Sam Doe"})
        receipt, report = receive(h, msg)
        assert report.ok and receipt.status is InboundStatus.STORED
        (created,) = h.repo.list_prospects()
        assert created.prospect_id == f"inbound-2026-01-15-tg{CHAT}"
        assert created.display_name == "Sam Doe"
        assert created.contact.telegram_chat_id == CHAT
        assert created.contact.phone is None and created.contact.email is None
        assert created.contact.preferred_channel is Channel.TELEGRAM
        assert created.status is ProspectStatus.ACTIVE
        assert h.action(created.prospect_id).draft.text == "Welcome"

    def test_the_stranger_is_the_same_person_next_time(self):
        h = telegram_harness(inbound=ENGAGE)
        receive(h, message("one", external_id="1"))
        receive(h, message("two", external_id="2"))
        assert len(h.repo.list_prospects()) == 1

    def test_opting_out_on_telegram_is_honoured(self):
        h = telegram_harness(telegram_prospect("p1", chat=CHAT))
        receive(h, message("STOP"))
        assert h.prospect("p1").status is ProspectStatus.OPTED_OUT
        assert h.brain.calls == []
        receive(h, message("/stop", external_id="2"))  # the command form is the same word
        assert h.channel.attempts == ()


class TestOutbound:
    def test_the_chat_id_is_what_the_channel_is_given(self):
        h = telegram_harness(telegram_prospect("p1", chat=CHAT), mode=ApprovalMode.AUTONOMOUS)
        h.engine.run_cycle()
        (sent,) = h.channel.sent
        assert sent.recipient == CHAT and sent.channel is Channel.TELEGRAM
        assert h.prospect("p1").status is ProspectStatus.ACTIVE

    def test_a_person_who_blocked_the_bot_is_a_permanent_failure_the_engine_pauses_on(self):
        h = telegram_harness(telegram_prospect("p1", chat=CHAT), mode=ApprovalMode.AUTONOMOUS)
        h.channel.fail_next(transient=False, error_code="403", error_message="bot was blocked")
        h.engine.run_cycle()
        assert h.status("p1") is ActionStatus.FAILED
        assert h.prospect("p1").status is ProspectStatus.PAUSED


def test_a_fake_channel_can_be_a_telegram_channel():
    assert FakeMessagingChannel(Channel.TELEGRAM).channel is Channel.TELEGRAM
