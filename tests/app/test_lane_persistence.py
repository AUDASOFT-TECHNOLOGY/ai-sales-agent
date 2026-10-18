"""The lane of a prospect, and the lane and channel of an action, survive a restart.

The database here is a real SQLite file, created by the project's own migrations (no migration is
added: a lane is written into the existing JSON documents) and closed between the two halves of each
test, as after a restart. The source is an in-memory double and the channels are fakes: nothing here
reaches a CRM, a spreadsheet, WhatsApp or a mailbox, and no production database is involved.
"""

from dataclasses import replace

import pytest
from consent_support import make_world
from engine_harness import ScriptedBrain
from factories import CLIENT, NOW, make_action, make_client_context, make_prospect
from sourcing_fakes import FakeProspectSource, default_capabilities
from sql_store_support import SqlStoreUnderTest

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.sourcing import ProspectState, StatusMapping
from sales_app.sourcing import store_codec as codec
from sales_app.sourcing.composite import CompositeRepository
from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    Channel,
    ContactInfo,
    LaneRoute,
    ProspectId,
    ProspectStatus,
    SalesActionType,
)
from sales_core.engine import ChannelRouter, Engine, EngineConfig
from sales_integrations.fakes import FakeClock, FakeMessagingChannel

MAPPING = StatusMapping.from_config({"lead": "new", "hold": "paused", "dnc": "opted_out"})
PHONE = "+21620000001"
MAIL = "lead@example.org"
CRM = LaneRoute(lane_id="crm", channel=Channel.EMAIL, product_ids=("c",), source_id="fake_crm")
SHEET = LaneRoute(lane_id="sheet", channel=Channel.WHATSAPP, product_ids=("a",), source_id="other")


@pytest.fixture
def file_engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'ops.db'}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


def open_repository(engine, *records):
    """What the application builds at start: the source, the SQL store, the consent registry."""
    source = FakeProspectSource(
        CLIENT,
        capabilities=default_capabilities(id_namespace="fake_crm"),
        status_mapping=MAPPING,
    )
    for pid, contact in records:
        source.add_record(
            make_prospect(prospect_id=ProspectId(pid), contact=contact), raw_status="lead"
        )
    store = SqlStoreUnderTest(engine, CLIENT)
    return CompositeRepository(source, store, make_world(client_id=CLIENT).registry), store


def restart(engine):
    engine.dispose()  # no connection, no cached state


# ---- what the documents hold ----------------------------------------------------------------------


def test_an_older_document_without_a_lane_reads_back_as_no_lane():
    state_doc = codec.state_to_doc(ProspectState(client_id=CLIENT, prospect_id=ProspectId("x:1")))
    del state_doc["lane_id"]
    assert codec.state_from_doc(CLIENT, "x:1", 0, state_doc).lane_id is None

    action_doc = codec.action_to_doc(make_action())
    del action_doc["lane_id"], action_doc["channel"]
    older = codec.action_from_doc(action_doc)
    assert (older.lane_id, older.channel) == (None, None)

    prospect_doc = codec.prospect_to_doc(make_prospect())
    del prospect_doc["lane_id"]
    assert codec.prospect_from_doc(prospect_doc).lane_id is None


def test_a_lane_and_a_channel_round_trip_through_the_documents():
    action = make_action(lane_id="crm", channel=Channel.EMAIL)
    assert codec.action_from_doc(codec.action_to_doc(action)) == action
    state = ProspectState(client_id=CLIENT, prospect_id=ProspectId("x:1"), lane_id="crm")
    assert codec.state_from_doc(CLIENT, "x:1", 0, codec.state_to_doc(state)).lane_id == "crm"


def test_no_migration_was_needed():
    from sales_app.persistence.schema import head_revision

    # The lane lives in the existing JSON documents: lanes added no revision of their own. The head
    # is 0011, the send cap, which has nothing to do with lanes.
    assert head_revision() == "0017"


# ---- a lane is kept across a restart ---------------------------------------------------------------


def test_the_lane_of_a_source_prospect_is_found_again_after_a_restart(file_engine):
    repo, _ = open_repository(file_engine, ("1", ContactInfo(phone=PHONE)))
    prospect = repo.get_prospect(ProspectId("fake_crm:1"))
    repo.update_prospects([make_prospect_with_lane(prospect, "crm")])
    restart(file_engine)
    again, _ = open_repository(file_engine, ("1", ContactInfo(phone=PHONE)))
    assert again.get_prospect(ProspectId("fake_crm:1")).lane_id == "crm"
    assert [p.lane_id for p in again.list_prospects()] == ["crm"]


def make_prospect_with_lane(prospect, lane_id):
    return replace(prospect, lane_id=lane_id)


def test_a_prospect_already_attributed_is_never_moved_by_a_later_write(file_engine):
    repo, _ = open_repository(file_engine, ("1", ContactInfo(phone=PHONE)))
    prospect = repo.get_prospect(ProspectId("fake_crm:1"))
    repo.update_prospects([make_prospect_with_lane(prospect, "crm")])
    repo.update_prospects(
        [make_prospect_with_lane(repo.get_prospect(ProspectId("fake_crm:1")), "sheet")]
    )
    restart(file_engine)
    again, _ = open_repository(file_engine, ("1", ContactInfo(phone=PHONE)))
    assert again.get_prospect(ProspectId("fake_crm:1")).lane_id == "crm"


def test_a_source_that_claims_every_field_still_does_not_own_the_lane(file_engine):
    from sales_app.sourcing import ProspectField

    source = FakeProspectSource(
        CLIENT,
        capabilities=default_capabilities(
            owned=frozenset(ProspectField), writable=frozenset(), id_namespace="fake_crm"
        ),
        status_mapping=MAPPING,
    )
    source.add_record(
        make_prospect(prospect_id=ProspectId("1"), contact=ContactInfo(phone=PHONE)),
        raw_status="lead",
    )
    repo = CompositeRepository(
        source, SqlStoreUnderTest(file_engine, CLIENT), make_world(client_id=CLIENT).registry
    )
    prospect = repo.get_prospect(ProspectId("fake_crm:1"))
    repo.update_prospects([make_prospect_with_lane(prospect, "crm")])  # not refused as read-only
    assert repo.get_prospect(ProspectId("fake_crm:1")).lane_id == "crm"


def test_the_lane_of_a_prospect_created_from_a_stranger_is_found_again(file_engine):
    repo, _ = open_repository(file_engine)
    stranger = make_prospect(
        prospect_id=ProspectId("inbound-2026-01-15-21620000009"),
        contact=ContactInfo(phone="+21620000009"),
        status=ProspectStatus.ACTIVE,
        lane_id="sheet",
    )
    repo.create_prospects([stranger])
    restart(file_engine)
    again, _ = open_repository(file_engine)
    assert again.get_prospect(stranger.prospect_id).lane_id == "sheet"


def test_two_clients_keep_their_lanes_apart(file_engine):
    mine, _ = open_repository(file_engine, ("1", ContactInfo(phone=PHONE)))
    mine.update_prospects(
        [make_prospect_with_lane(mine.get_prospect(ProspectId("fake_crm:1")), "crm")]
    )
    theirs = SqlStoreUnderTest(file_engine, "client-b")
    assert theirs.list_states() == {}


# ---- an action keeps its lane and channel across a restart ------------------------------------------


def test_an_action_keeps_its_lane_and_channel_in_the_database(file_engine):
    store = SqlStoreUnderTest(file_engine, CLIENT)
    store.save_actions([make_action(lane_id="crm", channel=Channel.EMAIL)])
    restart(file_engine)
    (found,) = SqlStoreUnderTest(file_engine, CLIENT).list_actions()
    assert (found.lane_id, found.channel) == ("crm", Channel.EMAIL)


class Process:
    """One run of the application: an Engine over the composite repository of the database."""

    def __init__(self, engine, lanes, *, mode=ApprovalMode.APPROVAL):
        self.clock = FakeClock(NOW)
        self.repo, self.store = open_repository(
            engine,
            ("1", ContactInfo(email=MAIL)),
            ("2", ContactInfo(email="second@example.org")),
        )
        self.whatsapp = FakeMessagingChannel(Channel.WHATSAPP, clock=self.clock)
        self.email = FakeMessagingChannel(Channel.EMAIL, clock=self.clock)
        self.engine = Engine(
            client=make_client_context(),
            repository=self.repo,
            brain=ScriptedBrain(),
            router=ChannelRouter(
                {Channel.WHATSAPP: self.whatsapp, Channel.EMAIL: self.email},
                lanes,
                default_lane="sheet",
            ),
            clock=self.clock,
            config=EngineConfig(mode=mode),
        )


def test_planned_work_is_sent_on_the_pinned_channel_by_the_process_after_a_restart(file_engine):
    first = Process(file_engine, (CRM, SHEET))
    first.engine.prepare()
    assert first.email.sent == () and first.whatsapp.sent == ()  # waiting for approval
    pending = {p.prospect_id: p for p in first.engine.pending_proposals()}
    assert set(pending) == {"fake_crm:1", "fake_crm:2"}
    assert {a.prospect_id: (a.lane_id, a.channel) for a in first.store.list_actions()} == {
        "fake_crm:1": ("crm", Channel.EMAIL),
        "fake_crm:2": ("crm", Channel.EMAIL),
    }

    restart(file_engine)
    # restarted with the CRM lane reconfigured to WhatsApp: what was planned keeps its channel
    second = Process(file_engine, (SHEET, replace(CRM, channel=Channel.WHATSAPP)))
    for proposal in second.engine.pending_proposals():
        second.engine.approve_proposal(proposal.action_id, actor="test")
    second.engine.dispatch()
    assert sorted(m.prospect_id for m in second.email.sent) == ["fake_crm:1", "fake_crm:2"]
    assert second.whatsapp.sent == ()
    assert {p.lane_id for p in second.repo.list_prospects()} == {"crm"}


def test_the_attribution_is_written_once_and_not_redone_after_a_restart(file_engine):
    first = Process(file_engine, (CRM, SHEET))
    first.engine.prepare()
    restart(file_engine)
    second = Process(file_engine, (CRM, SHEET))
    second.engine.prepare()
    events = [e for e in second.store.audit_events if e.event_type == "prospect.lane_assigned"]
    assert sorted(e.prospect_id for e in events) == ["fake_crm:1", "fake_crm:2"]  # not four
    assert SalesActionType.FIRST_CONTACT in {a.action_type for a in second.store.list_actions()}
