"""`/client/v1/approvals`: a client approves or rejects what its agent proposes.

SIMULATED here: the messaging channel, the Brain, the model, the CRM source (a fake) and the client
login. REAL here: the application (registry, runtime lease, session cookie), the routes, the Engine,
and, in the last class, the SQL operational store (a SQLite file) behind the real
`CompositeRepository` and consent registry, which is where a HubSpot client's proposals live.
Nothing here proves a real HubSpot, Meta or OpenAI call.
"""

from dataclasses import replace

import pytest
from consent_support import make_world
from engine_harness import ScriptedBrain, phone_for, prospect, send
from factories import NOW, make_prospect
from sourcing_fakes import FakeProspectSource, default_capabilities
from test_client_agent_api import World

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.sourcing.composite import CompositeRepository
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_core.approval import ApprovalMode
from sales_core.contracts import ClientContext, ContactInfo, ProspectId, ProspectStatus
from sales_core.engine import Engine, EngineConfig
from sales_integrations.fakes import FakeClock, FakeMessagingChannel

LIST = "/client/v1/approvals"


def approve(action_id):
    return f"{LIST}/{action_id}/approve"


def reject(action_id):
    return f"{LIST}/{action_id}/reject"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.application.close()


def propose(world, cid, *pids):
    """Give `cid` prospects and let its Engine write its proposals (approval mode)."""
    repository = world.made.repositories[cid]
    repository.add_prospects(
        *(prospect(pid, client_id=cid, display_name=f"P {pid}") for pid in pids)
    )
    with world.application.registry.get(cid).lease() as engine:
        engine.prepare()


def dispatch(world, cid):
    with world.application.registry.get(cid).lease() as engine:
        engine.dispatch()


def sent(world, cid):
    return world.made.channels[cid].attempts


def waiting(browser):
    return browser.get(LIST).json()["proposals"]


class TestTheRoutes:
    def test_they_need_a_client_session(self, world):
        anonymous = world.anonymous()
        assert anonymous.get(LIST).status_code == 401
        assert anonymous.post(approve("a1")).status_code == 401
        assert anonymous.post(reject("a1")).status_code == 401

    def test_nothing_is_listed_before_the_agent_proposes(self, world):
        response = world.browser("alpha").get(LIST)
        assert response.status_code == 200 and response.json() == {"proposals": []}
        assert response.headers["cache-control"] == "no-store"

    def test_the_client_sees_what_waits_for_it(self, world):
        propose(world, "alpha", "p1")
        (proposal,) = waiting(world.browser("alpha"))
        assert proposal["prospect_name"] == "P p1" and "hello" in proposal["text"]

    def test_approving_releases_the_message_and_the_dispatch_sends_it_once(self, world):
        propose(world, "alpha", "p1")
        browser = world.browser("alpha")
        (proposal,) = waiting(browser)
        response = browser.post(approve(proposal["id"]))
        assert response.status_code == 200
        assert response.json() == {"id": proposal["id"], "decision": "approved"}
        assert sent(world, "alpha") == ()  # approving does not send by itself
        assert waiting(browser) == []
        dispatch(world, "alpha")
        dispatch(world, "alpha")
        assert len(sent(world, "alpha")) == 1

    def test_rejecting_never_sends(self, world):
        propose(world, "alpha", "p1")
        browser = world.browser("alpha")
        (proposal,) = waiting(browser)
        assert browser.post(reject(proposal["id"])).json()["decision"] == "rejected"
        dispatch(world, "alpha")
        assert sent(world, "alpha") == ()
        assert waiting(browser) == []

    def test_a_decision_cannot_be_taken_twice(self, world):
        propose(world, "alpha", "p1")
        browser = world.browser("alpha")
        (proposal,) = waiting(browser)
        assert browser.post(approve(proposal["id"])).status_code == 200
        assert browser.post(approve(proposal["id"])).status_code == 404
        assert browser.post(reject(proposal["id"])).status_code == 404

    def test_an_unknown_id_is_a_plain_not_found(self, world):
        response = world.browser("alpha").post(approve("nope"))
        assert response.status_code == 404
        assert "nope" not in response.text

    def test_a_prospect_who_opted_out_since_cannot_be_approved_but_can_be_rejected(self, world):
        propose(world, "alpha", "p1")
        repository = world.made.repositories["alpha"]
        gone = repository.get_prospect(ProspectId("p1"))
        repository.update_prospects([replace(gone, status=ProspectStatus.OPTED_OUT)])
        browser = world.browser("alpha")
        (proposal,) = waiting(browser)
        assert browser.post(approve(proposal["id"])).status_code == 409
        assert browser.post(reject(proposal["id"])).status_code == 200

    def test_an_agent_that_takes_no_work_answers_a_conflict(self, world):
        # gamma lacks its platform credentials: it is not ready, so it has nothing to decide on
        assert world.browser("gamma").get(LIST).status_code == 409


class TestClientsNeverMeet:
    def test_one_client_never_sees_or_decides_another_clients_proposals(self, world):
        propose(world, "alpha", "p1")
        propose(world, "beta", "p1")
        alpha, beta = world.browser("alpha"), world.browser("beta")
        (mine,) = waiting(alpha)
        (theirs,) = waiting(beta)
        assert mine["id"] != theirs["id"]
        assert beta.post(approve(mine["id"])).status_code == 404
        assert alpha.post(reject(theirs["id"])).status_code == 404
        assert [p["id"] for p in waiting(alpha)] == [mine["id"]]
        assert [p["id"] for p in waiting(beta)] == [theirs["id"]]
        dispatch(world, "alpha")
        dispatch(world, "beta")
        assert sent(world, "alpha") == () and sent(world, "beta") == ()


class TestOnTheSqlStoreOfAClientWithoutASheet:
    """A source that is only read (a fake CRM here) plus the SQL store: where approval had no door."""

    CLIENT = "alpha"

    def engine(self, tmp_path, url, source, channel):
        registry = make_world(tmp_path, client_id=self.CLIENT).registry
        database = create_database_engine(url)
        self.databases.append(database)
        store = SqlOperationalStore(SqlOperationalRows(database, self.CLIENT))
        return Engine(
            client=ClientContext(
                client_id=self.CLIENT, display_name="Alpha", timezone="Africa/Tunis", settings={}
            ),
            repository=CompositeRepository(source, store, registry),
            brain=ScriptedBrain(lambda request: send("Hi from the CRM!")),
            channel=channel,
            clock=FakeClock(NOW),
            config=EngineConfig(mode=ApprovalMode.APPROVAL),
        )

    def test_from_a_crm_contact_to_a_sent_message_through_a_person_across_a_restart(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'ops.db'}"
        upgrade_database(create_database_engine(url))
        self.databases = []
        source = FakeProspectSource(
            self.CLIENT, capabilities=default_capabilities(), allow_writes=True
        )
        source.add_record(
            make_prospect(
                prospect_id=ProspectId("1"),
                client_id=self.CLIENT,
                display_name="Crm Person",
                status=ProspectStatus.NEW,
                contact=ContactInfo(phone=phone_for("1"), email=None),
            )
        )
        channel = FakeMessagingChannel(clock=FakeClock(NOW))
        try:
            engine = self.engine(tmp_path, url, source, channel)
            engine.prepare()
            (proposal,) = engine.pending_proposals()
            assert proposal.prospect_name == "Crm Person"
            assert channel.attempts == ()

            # the process restarts before anyone looks: the proposal is still there, only once
            engine = self.engine(tmp_path, url, source, channel)
            engine.prepare()
            (proposal,) = engine.pending_proposals()

            engine.approve_proposal(proposal.action_id, actor="client:alpha")
            restarted = self.engine(tmp_path, url, source, channel)
            restarted.dispatch()
            restarted.dispatch()
            assert len(channel.attempts) == 1
            assert channel.attempts[0].draft.text == "Hi from the CRM!"
            assert restarted.pending_proposals() == ()
        finally:
            for database in self.databases:
                database.dispose()
