"""Telegram for a client whose prospects live in a CRM: where the chat is kept, and what binds it.

A CRM never holds a prospect's Telegram chat (the person gave it to the bot, not to the CRM), so
Revansa keeps it in its own operational store, on the prospect's state, and reads it back joined to
the CRM's record. Run against the in-memory store and the SQL store alike, with the consent registry
real. Simulated: the CRM (`FakeProspectSource`).
"""

from dataclasses import replace

import pytest
from consent_support import make_world
from factories import CLIENT, make_prospect
from sourcing_fakes import FakeOperationalStore, FakeProspectSource, default_capabilities
from sql_store_support import SqlStoreUnderTest

from sales_app.sourcing import ProspectState, StatusMapping
from sales_app.sourcing.composite import KEYWORD_OPT_OUT_EVENT, CompositeRepository
from sales_app.sourcing.contacts import contact_keys, lookup_keys
from sales_core.contracts import (
    AuditEvent,
    Channel,
    ContactInfo,
    ProspectId,
    ProspectStatus,
    Scheduling,
)

S = ProspectStatus
MAPPING = StatusMapping.from_config({"lead": "new", "hold": "paused", "dnc": "opted_out"})
PHONE = "+216 99 888 777"
CHAT = "424242"
OTHER_CHAT = "515151"


def person(pid, phone=None, chat=None, **kw):
    contact = ContactInfo(phone=phone, telegram_chat_id=chat, preferred_channel=Channel.TELEGRAM)
    return make_prospect(prospect_id=ProspectId(pid), contact=contact, **kw)


def public(pid):
    return ProspectId(f"fake_crm:{pid}")


@pytest.fixture(params=["memory", "sql"])
def store(request, sql_engine):
    if request.param == "memory":
        return FakeOperationalStore(CLIENT)
    return SqlStoreUnderTest(sql_engine, CLIENT)


@pytest.fixture
def env(store):
    class Env:
        pass

    e = Env()
    e.source = FakeProspectSource(
        CLIENT, capabilities=default_capabilities(id_namespace="fake_crm"), status_mapping=MAPPING
    )
    e.store = store
    e.world = make_world(client_id=CLIENT, initialise=True)
    e.repo = CompositeRepository(e.source, store, e.world.registry)
    e.source.add_record(person("1", PHONE), raw_status="lead")
    return e


def link(env, pid="1", chat=CHAT):
    current = env.repo.get_prospect(public(pid))
    env.repo.update_prospects(
        [replace(current, contact=replace(current.contact, telegram_chat_id=chat))]
    )


# ---- the contact key --------------------------------------------------------------------------


class TestTheKey:
    def test_a_chat_is_an_identity_of_its_own(self):
        assert contact_keys(ContactInfo(telegram_chat_id=CHAT)) == {f"telegram:{CHAT}"}
        assert contact_keys(ContactInfo(phone="+1 555", telegram_chat_id=CHAT)) == {
            "phone:1555",
            f"telegram:{CHAT}",
        }
        assert contact_keys(ContactInfo(telegram_chat_id="  ")) == frozenset()
        assert lookup_keys(telegram_chat_id=CHAT) == {f"telegram:{CHAT}"}

    def test_a_phone_number_is_never_a_chat(self):
        assert not contact_keys(ContactInfo(phone=CHAT)) & contact_keys(
            ContactInfo(telegram_chat_id=CHAT)
        )


# ---- a CRM prospect and its chat --------------------------------------------------------------


class TestACrmProspect:
    def test_the_chat_is_kept_by_revansa_and_joined_to_the_crms_record(self, env):
        link(env)
        found = env.repo.get_prospect(public("1"))
        assert found.contact.telegram_chat_id == CHAT and found.contact.phone == PHONE
        assert env.store.get_states([public("1")])[public("1")].telegram_chat_id == CHAT

    def test_the_crm_is_never_written_to(self, env):
        before = env.source.records if hasattr(env.source, "records") else None
        link(env)
        assert env.source.get_prospect("1").contact.telegram_chat_id is None
        assert before is None or env.source.records == before

    def test_it_is_found_by_chat_listed_and_still_found_by_phone(self, env):
        link(env)
        assert env.repo.find_prospect_by_contact(telegram_chat_id=CHAT).prospect_id == public("1")
        assert env.repo.find_prospect_by_contact(telegram_chat_id=OTHER_CHAT) is None
        assert env.repo.find_prospect_by_contact(phone=PHONE).contact.telegram_chat_id == CHAT
        (listed,) = env.repo.list_prospects()
        assert listed.contact.telegram_chat_id == CHAT

    def test_a_prospect_not_linked_yet_is_found_by_nothing_but_its_phone(self, env):
        assert env.repo.find_prospect_by_contact(telegram_chat_id=CHAT) is None
        assert env.repo.get_prospect(public("1")).contact.telegram_chat_id is None

    def test_a_lookup_by_nothing_finds_nothing(self, env):
        link(env)
        assert env.repo.find_prospect_by_contact() is None
        assert env.repo.find_prospect_by_contact(telegram_chat_id="") is None

    def test_what_else_the_state_holds_is_untouched_by_the_link(self, env):
        current = env.repo.get_prospect(public("1"))
        env.repo.update_prospects([replace(current, stage="warm", status=S.ACTIVE)])
        link(env)
        found = env.repo.get_prospect(public("1"))
        assert found.stage == "warm" and found.status is S.ACTIVE

    def test_a_later_update_that_does_not_mention_the_chat_keeps_it(self, env):
        link(env)
        current = env.repo.get_prospect(public("1"))
        env.repo.update_prospects([replace(current, stage="hot")])
        assert env.repo.get_prospect(public("1")).contact.telegram_chat_id == CHAT

    def test_the_source_cannot_forget_it_a_record_that_changes_keeps_its_chat(self, env):
        link(env)
        env.source.human_edit(person("1", "+216 99 000 111"))  # the CRM's phone was corrected
        assert env.repo.get_prospect(public("1")).contact.telegram_chat_id == CHAT

    def test_the_chat_of_one_prospect_is_not_another_prospects(self, env):
        env.source.add_record(person("2", "+216 22 111 222"), raw_status="lead")
        link(env, "1", CHAT)
        link(env, "2", OTHER_CHAT)
        assert env.repo.find_prospect_by_contact(telegram_chat_id=CHAT).prospect_id == public("1")
        assert env.repo.find_prospect_by_contact(telegram_chat_id=OTHER_CHAT).prospect_id == public(
            "2"
        )

    def test_a_state_written_before_chats_existed_reads_back(self, env):
        env.store.save_states(
            [ProspectState(client_id=CLIENT, prospect_id=public("1"), status=S.ACTIVE)]
        )
        assert env.repo.get_prospect(public("1")).contact.telegram_chat_id is None


# ---- a person who wrote first -----------------------------------------------------------------


class TestAPersonWhoWroteFirst:
    def created(self, chat=OTHER_CHAT, pid="inbound-2026-01-15-tg515151"):
        return person(pid, None, chat, status=S.ACTIVE, schedule=Scheduling(last_inbound_at=None))

    def test_they_are_kept_locally_with_their_chat_and_found_by_it(self, env):
        env.repo.create_prospects([self.created()])
        found = env.repo.find_prospect_by_contact(telegram_chat_id=OTHER_CHAT)
        assert found.prospect_id == "inbound-2026-01-15-tg515151"
        assert found.contact.telegram_chat_id == OTHER_CHAT and found.contact.phone is None

    def test_two_people_cannot_be_the_same_chat(self, env):
        env.repo.create_prospects([self.created()])
        with pytest.raises(ValueError):
            env.repo.create_prospects([self.created(pid="inbound-2026-01-16-tg515151")])

    def test_a_local_prospect_can_be_linked_later_too(self, env):
        env.repo.create_prospects([person("inbound-2026-01-15-tg1", None, None, status=S.ACTIVE)])
        current = env.repo.get_prospect(ProspectId("inbound-2026-01-15-tg1"))
        env.repo.update_prospects(
            [replace(current, contact=replace(current.contact, telegram_chat_id="1"))]
        )
        assert (
            env.repo.find_prospect_by_contact(telegram_chat_id="1").prospect_id
            == current.prospect_id
        )


# ---- consent: an opt-out on Telegram binds the person everywhere ------------------------------


class TestConsent:
    def opt_out(self, env, pid):
        env.repo.append_audit_events(
            [
                AuditEvent(
                    timestamp=env.world.clock(),
                    client_id=CLIENT,
                    event_type=KEYWORD_OPT_OUT_EVENT,
                    actor="core",
                    prospect_id=pid,
                )
            ]
        )

    def test_a_person_who_stops_on_telegram_is_blocked_on_every_identity_they_have(self, env):
        link(env)
        self.opt_out(env, public("1"))
        snapshot = env.world.registry.snapshot()
        assert snapshot.blocks(ContactInfo(telegram_chat_id=CHAT))
        assert snapshot.blocks(ContactInfo(phone=PHONE))  # the CRM's number, too
        assert env.repo.get_prospect(public("1")).status is S.OPTED_OUT

    def test_the_block_follows_them_to_another_record_with_the_same_phone(self, env):
        link(env)
        self.opt_out(env, public("1"))
        env.source.add_record(person("2", PHONE), raw_status="lead")
        assert env.repo.get_prospect(public("2")).status is S.OPTED_OUT

    def test_a_local_person_who_stops_is_blocked_by_their_chat(self, env):
        env.repo.create_prospects(
            [person("inbound-2026-01-15-tg515151", None, OTHER_CHAT, status=S.ACTIVE)]
        )
        self.opt_out(env, ProspectId("inbound-2026-01-15-tg515151"))
        assert env.world.registry.snapshot().blocks(ContactInfo(telegram_chat_id=OTHER_CHAT))
        again = env.repo.find_prospect_by_contact(telegram_chat_id=OTHER_CHAT)
        assert again.status is S.OPTED_OUT

    def test_someone_who_never_stopped_is_not_blocked_by_anothers_chat(self, env):
        link(env)
        self.opt_out(env, public("1"))
        assert not env.world.registry.snapshot().blocks(ContactInfo(telegram_chat_id=OTHER_CHAT))
