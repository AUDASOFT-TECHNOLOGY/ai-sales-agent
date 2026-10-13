"""The SalesRepository port contract, checked against the fake AND the Google Sheets adapter.

The Engine is tested against the fake, so the Sheets adapter must behave identically wherever the
port says something. Anything true of one and not the other is a bug in the adapter.
"""

from dataclasses import replace

import pytest
from engine_harness import prospect
from factories import CLIENT, CONVERSATION, NOW, PROSPECT, make_action, make_message
from sheets_support import TUNIS, make_repository

from sales_core.contracts import (
    ActionId,
    ActionStatus,
    AuditEvent,
    Channel,
    ClientId,
    ContactInfo,
    ConversationId,
    ProspectId,
    ProspectStatus,
    Scheduling,
)
from sales_core.ports import SalesRepository
from sales_integrations.fakes import FakeSalesRepository
from sales_integrations.google_sheets import codecs

OTHER = ClientId("client-b")


class FakeEnv:
    def __init__(self):
        self.repo = FakeSalesRepository(CLIENT)

    def seed_prospects(self, *prospects):
        self.repo.add_prospects(*prospects)

    def audit_count(self):
        return len(self.repo.audit_events)

    def audit_types(self):
        return [e.event_type for e in self.repo.audit_events]


class SheetsEnv:
    def __init__(self):
        self.repo, self.api = make_repository(client_id="client-a")

    def seed_prospects(self, *prospects):
        for p in prospects:
            cells = codecs.encode_prospect(p, TUNIS)
            cells = {k: v for k, v in cells.items() if not k.startswith("@")}
            self.api.human_add_row("Prospects", prospect_id=p.prospect_id, **cells)

    def audit_count(self):
        return len(self.api.rows("Log"))

    def audit_types(self):
        return [r["event_type"] for r in self.api.rows("Log")]


@pytest.fixture(params=["fake", "sheets"])
def env(request):
    return FakeEnv() if request.param == "fake" else SheetsEnv()


def p(pid, **kw):
    return prospect(pid, **kw)


def test_both_implementations_satisfy_the_port(env):
    assert isinstance(env.repo, SalesRepository)
    assert env.repo.client_id == CLIENT


class TestProspects:
    def test_listed_in_order_and_fetched_by_id(self, env):
        first, second = p("p1"), p("p2")
        env.seed_prospects(first, second)
        assert list(env.repo.list_prospects()) == [first, second]
        assert env.repo.get_prospect(ProspectId("p2")) == second
        assert env.repo.get_prospect(ProspectId("missing")) is None

    def test_update_persists_new_state(self, env):
        env.seed_prospects(p("p1"))
        current = env.repo.get_prospect(ProspectId("p1"))
        env.repo.update_prospects([replace(current, status=ProspectStatus.ACTIVE, stage="s1")])
        stored = env.repo.get_prospect(ProspectId("p1"))
        assert stored.status is ProspectStatus.ACTIVE and stored.stage == "s1"

    def test_update_of_an_unknown_prospect_raises_and_writes_nothing(self, env):
        env.seed_prospects(p("p1"))
        current = env.repo.get_prospect(ProspectId("p1"))
        changed = replace(current, status=ProspectStatus.CLOSED)
        with pytest.raises(KeyError):
            env.repo.update_prospects([changed, p("ghost")])
        assert env.repo.get_prospect(ProspectId("p1")).status is ProspectStatus.NEW

    def test_other_clients_prospects_are_rejected(self, env):
        env.seed_prospects(p("p1"))
        with pytest.raises(ValueError, match="client-b"):
            env.repo.update_prospects([p("p1", client_id=OTHER)])

    def test_find_by_contact_ignores_phone_formatting_and_email_case(self, env):
        contact = ContactInfo(phone="+1 (555) 000-0001", email="Alex@Example.test")
        env.seed_prospects(p("p1", contact=contact), p("p2"))
        for phone in ("+15550000001", "15550000001", "1 555 000 0001"):
            assert env.repo.find_prospect_by_contact(phone=phone).prospect_id == "p1"
        assert env.repo.find_prospect_by_contact(email=" alex@example.TEST ").prospect_id == "p1"
        assert env.repo.find_prospect_by_contact(phone="+19999999999") is None
        assert env.repo.find_prospect_by_contact() is None


class TestTelegramContact:
    """A prospect's Telegram chat is a contact like the others, found by what Telegram gave us."""

    def test_found_by_the_chat_id_and_only_by_it(self, env):
        contact = ContactInfo(phone="+15550000001", telegram_chat_id="424242")
        env.seed_prospects(p("p1", contact=contact), p("p2"))
        assert env.repo.find_prospect_by_contact(telegram_chat_id="424242").prospect_id == "p1"
        assert env.repo.find_prospect_by_contact(telegram_chat_id=" 424242 ").prospect_id == "p1"
        assert env.repo.find_prospect_by_contact(telegram_chat_id="42424") is None
        assert env.repo.find_prospect_by_contact(telegram_chat_id="") is None
        # a phone number is not a chat id, and a chat id is not a phone number
        assert env.repo.find_prospect_by_contact(telegram_chat_id="15550000001") is None
        assert env.repo.find_prospect_by_contact(phone="424242") is None

    def test_a_chat_id_written_by_an_update_is_read_back_and_found(self, env):
        env.seed_prospects(p("p1"))
        current = env.repo.get_prospect(ProspectId("p1"))
        linked = replace(current, contact=replace(current.contact, telegram_chat_id="424242"))
        env.repo.update_prospects([linked])
        assert env.repo.get_prospect(ProspectId("p1")) == linked
        assert env.repo.find_prospect_by_contact(telegram_chat_id="424242") == linked
        assert env.repo.find_prospect_by_contact(phone=current.contact.phone) == linked

    def test_a_prospect_created_from_a_chat_is_read_listed_and_found(self, env):
        created = p(
            "p2",
            contact=ContactInfo(telegram_chat_id="515151", preferred_channel=Channel.TELEGRAM),
            status=ProspectStatus.ACTIVE,
            schedule=Scheduling(last_inbound_at=NOW),
        )
        env.repo.create_prospects([created])
        assert env.repo.get_prospect(ProspectId("p2")) == created
        assert env.repo.find_prospect_by_contact(telegram_chat_id="515151") == created


class TestCreateProspects:
    def test_a_created_prospect_can_be_read_listed_and_found_by_contact(self, env):
        existing = p("p1")
        env.seed_prospects(existing)
        created = p(
            "p2",
            display_name="+15550000002",
            contact=ContactInfo(phone="+15550000002", preferred_channel=Channel.WHATSAPP),
            status=ProspectStatus.ACTIVE,
            schedule=Scheduling(last_inbound_at=NOW),
        )
        env.repo.create_prospects([created])
        assert list(env.repo.list_prospects()) == [existing, created]
        assert env.repo.get_prospect(ProspectId("p2")) == created
        assert env.repo.find_prospect_by_contact(phone="15550000002").prospect_id == "p2"

    def test_a_created_prospect_can_be_updated_afterwards(self, env):
        env.repo.create_prospects([p("p1")])
        current = env.repo.get_prospect(ProspectId("p1"))
        env.repo.update_prospects([replace(current, status=ProspectStatus.OPTED_OUT)])
        assert env.repo.get_prospect(ProspectId("p1")).status is ProspectStatus.OPTED_OUT

    def test_an_existing_id_is_rejected_and_nothing_is_written(self, env):
        env.seed_prospects(p("p1"))
        with pytest.raises(ValueError, match="already exists"):
            env.repo.create_prospects([p("p2"), p("p1")])
        assert [x.prospect_id for x in env.repo.list_prospects()] == ["p1"]

    def test_a_repeated_id_inside_one_batch_is_rejected(self, env):
        with pytest.raises(ValueError, match="already exists"):
            env.repo.create_prospects([p("p1"), p("p1")])
        assert env.repo.list_prospects() == []

    def test_other_clients_prospects_are_rejected(self, env):
        with pytest.raises(ValueError, match="client-b"):
            env.repo.create_prospects([p("p1", client_id=OTHER)])
        assert env.repo.list_prospects() == []


class TestActions:
    def test_save_inserts_and_updates_by_action_id(self, env):
        env.repo.save_actions([make_action()])
        current = env.repo.list_actions()[0]
        env.repo.save_actions([replace(current, status=ActionStatus.APPROVED)])
        actions = env.repo.list_actions()
        assert len(actions) == 1 and actions[0].status is ActionStatus.APPROVED

    def test_list_filters_by_status_and_finds_by_key(self, env):
        env.repo.save_actions(
            [
                make_action(action_id=ActionId("a-1"), idempotency_key="k1"),
                make_action(
                    action_id=ActionId("a-2"), idempotency_key="k2", status=ActionStatus.SENT
                ),
            ]
        )
        assert [a.action_id for a in env.repo.list_actions({ActionStatus.SENT})] == ["a-2"]
        assert len(env.repo.list_actions()) == 2
        assert env.repo.list_actions(set()) == []
        assert env.repo.find_action_by_idempotency_key("k1").action_id == "a-1"
        assert env.repo.find_action_by_idempotency_key("nope") is None

    def test_a_second_action_with_the_same_key_is_rejected_atomically(self, env):
        env.repo.save_actions([make_action(idempotency_key="k1")])
        fresh = make_action(action_id=ActionId("a-2"), idempotency_key="k2")
        clash = make_action(action_id=ActionId("a-3"), idempotency_key="k1")
        with pytest.raises(ValueError, match="already in use"):
            env.repo.save_actions([fresh, clash])
        assert [a.action_id for a in env.repo.list_actions()] == ["a-1"]

    def test_duplicate_keys_inside_one_batch_are_rejected(self, env):
        batch = [
            make_action(action_id=ActionId("a-1"), idempotency_key="k"),
            make_action(action_id=ActionId("a-2"), idempotency_key="k"),
        ]
        with pytest.raises(ValueError, match="already in use"):
            env.repo.save_actions(batch)
        assert env.repo.list_actions() == []

    def test_other_clients_actions_are_rejected(self, env):
        with pytest.raises(ValueError, match="client-b"):
            env.repo.save_actions([make_action(client_id=OTHER)])


class TestHistory:
    def test_no_history_gives_an_empty_conversation_with_a_stable_id(self, env):
        conversation = env.repo.get_conversation(PROSPECT)
        assert conversation.messages == ()
        assert conversation.client_id == CLIENT and conversation.prospect_id == PROSPECT
        assert env.repo.get_conversation(PROSPECT).conversation_id == conversation.conversation_id

    def test_uses_the_prospects_conversation_reference_when_set(self, env):
        env.seed_prospects(p("p-1", conversation_id=ConversationId("conv-x")))
        assert env.repo.get_conversation(ProspectId("p-1")).conversation_id == "conv-x"

    def test_append_is_ordered_and_scoped_per_prospect(self, env):
        p2 = ProspectId("p-2")
        m1 = make_message(message_id="m-1")
        m2 = make_message(message_id="m-2", prospect_id=p2, conversation_id="c-2")
        m3 = make_message(message_id="m-3")
        env.repo.append_messages([m1, m2])
        env.repo.append_messages([m3])
        assert env.repo.get_conversation(PROSPECT).messages == (m1, m3)
        assert env.repo.get_conversation(p2).messages == (m2,)
        assert env.repo.get_conversation(PROSPECT).conversation_id == CONVERSATION

    def test_duplicate_message_ids_are_rejected_atomically(self, env):
        env.repo.append_messages([make_message(message_id="m-1")])
        with pytest.raises(ValueError, match="already exists"):
            env.repo.append_messages(
                [make_message(message_id="m-2"), make_message(message_id="m-1")]
            )
        assert len(env.repo.get_conversation(PROSPECT).messages) == 1

    def test_a_prospect_keeps_one_conversation_id(self, env):
        env.repo.append_messages([make_message(message_id="m-1")])
        with pytest.raises(ValueError, match="uses"):
            env.repo.append_messages([make_message(message_id="m-2", conversation_id="other")])

    def test_message_must_match_the_prospects_conversation_reference(self, env):
        env.seed_prospects(p("p-1", conversation_id=ConversationId("conv-x")))
        with pytest.raises(ValueError, match="conv-x"):
            env.repo.append_messages([make_message()])

    def test_other_clients_messages_are_rejected(self, env):
        with pytest.raises(ValueError, match="client-b"):
            env.repo.append_messages([make_message(client_id=OTHER)])


class TestAudit:
    def _event(self, client=CLIENT, kind="x.y"):
        return AuditEvent(timestamp=NOW, client_id=client, event_type=kind, actor="core")

    def test_events_are_appended_in_order(self, env):
        env.repo.append_audit_events([self._event(kind="x.y")])
        env.repo.append_audit_events([self._event(kind="x.z")])
        assert env.audit_types() == ["x.y", "x.z"]

    def test_other_clients_events_are_rejected_atomically(self, env):
        with pytest.raises(ValueError, match="client-b"):
            env.repo.append_audit_events([self._event(), self._event(OTHER)])
        assert env.audit_count() == 0


def test_sheets_refuses_to_create_a_prospect_whose_phone_is_already_on_the_sheet():
    """Even a row the adapter cannot read counts: it may still be contacted by a person."""
    env = SheetsEnv()
    env.api.human_add_row("Prospects", prospect_id="broken", phone="+1 555 000 0001", status="???")
    with pytest.raises(ValueError, match="phone number is already in use"):
        env.repo.create_prospects([p("p1", contact=ContactInfo(phone="+15550000001"))])
    assert [r["prospect_id"] for r in env.api.rows("Prospects")] == ["broken"]
