"""The whole application on a Google Sheets client, from the sheet to the channel and back.

What is real here: the `Application` (configuration loading, registry, scheduler, agent switch), the
HTTP webhook route and its signature check, the Engine, the Brain package loading, and the
`GoogleSheetsRepository` reading and writing the tabs. What is simulated, because it would need a
real account: the Google Sheets API (an in-memory grid), the messaging channel (records what it was
asked to send) and the model provider. Nothing here proves a real Google, Meta or OpenAI call works.

A restart is a restart: the application is closed and built again over the same sheets, with a new
adapter that remembers nothing of earlier reads.
"""

import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from app_support import (
    InlineExecutor,
    app_secret,
    env_for,
    fake_builders,
    make_settings,
    phone_id,
    write_client,
)
from engine_harness import phone_for
from factories import NOW
from fastapi.testclient import TestClient
from sheets_support import SID, TUNIS, FakeSheetsApi
from test_app_api import payload, post

from sales_app.agent_state import InMemoryAgentStateStore
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_core.contracts import ClientId
from sales_integrations.fakes import FakeClock
from sales_integrations.google_sheets import GoogleSheetsRepository

BRAIN = "app_test_brains:follow_up_brain"


class Deployment:
    """Clients on their own spreadsheets, behind one application that can be restarted."""

    def __init__(self, tmp_path, *client_ids, running=True):
        self.root = tmp_path
        self.client_ids = client_ids
        self.clock = FakeClock(NOW)  # 10:00 on Thursday 15 January 2026, in Tunis
        self.sheets = {cid: FakeSheetsApi() for cid in client_ids}
        for api in self.sheets.values():
            api.create_layout()
        for cid in client_ids:
            write_client(tmp_path, cid, brain={"factory": BRAIN, "settings": {"tone": cid}})
        builders, self.made = fake_builders(self.clock)
        self.builders = replace(builders, repositories={"google_sheets": self._repository})
        self.agent_state = InMemoryAgentStateStore()
        self.application = None
        self.http = None
        self.start()
        if running:
            for cid in client_ids:
                self.start_agent(cid)

    def _repository(self, config):
        return GoogleSheetsRepository(
            self.sheets[config.client_id],
            client_id=ClientId(config.client_id),
            spreadsheet_id=SID,
            timezone=TUNIS,
        )

    def start(self):
        self.application = build_application(
            make_settings(self.root),
            env=env_for(*self.client_ids),
            builders=self.builders,
            clock=self.clock,
            executor=InlineExecutor(),
            agent_state_store=self.agent_state,
        )
        self.http = TestClient(create_app(self.application, run_scheduler=False))

    def restart(self):
        self.application.close()
        self.start()

    def start_agent(self, cid):
        self.agent_state.set(cid, running=True, by="tests", at=self.clock.now())

    # ---- time and work -------------------------------------------------------------------

    def tick(self, advance=timedelta(minutes=10)):
        """Let time pass, then let the scheduler start whatever is due. Returns the job results."""
        self.clock.advance(advance)
        futures = self.application.scheduler.tick(self.clock.now())
        return [result for future in futures for result in future.result()]

    # ---- the people: the client's team in the sheet, a prospect on the phone ---------------

    def add_prospect(self, cid, pid="p1", **cells):
        values = {
            "prospect_id": pid,
            "display_name": "Alex",
            "phone": phone_for(pid),
            "status": "new",
        }
        self.sheets[cid].human_add_row("Prospects", **(values | cells))

    def approve(self, cid, pid="p1", *, nth=-1):
        """The client's team approves a proposal in the Actions tab (the latest by default)."""
        api = self.sheets[cid]
        rows = [i for i, r in enumerate(api.tabs["Actions"][1:], start=2) if pid in r]
        api.human_set("Actions", rows[nth], "status", "approved")

    def prospect_says(self, cid, text, *, wamid="wamid.1", pid="p1"):
        body = payload(phone_id(cid), phone_for(pid).lstrip("+"), text, wamid)
        return post(self.http, body, secret=app_secret(cid))

    # ---- what the sheet and the channel hold ----------------------------------------------

    def actions(self, cid):
        return self.sheets[cid].rows("Actions")

    def messages(self, cid, direction=None):
        rows = self.sheets[cid].rows("Messages")
        return [r for r in rows if direction is None or r["direction"] == direction]

    def prospect(self, cid, pid="p1"):
        api = self.sheets[cid]
        row = api.find_row("Prospects", "prospect_id", pid)
        return {h: api.cell("Prospects", row, h) for h in api.headers("Prospects")}

    def attempts(self, cid):
        channel = self.made.channels.get(cid)
        return () if channel is None else channel.attempts


@pytest.fixture
def deployment(tmp_path):
    made = Deployment(tmp_path, "alpha")
    yield made
    made.application.close()


@pytest.fixture
def pair(tmp_path):
    made = Deployment(tmp_path, "alpha", "beta")
    yield made
    made.application.close()


# ---- the path: sheet -> proposal -> approval -> send -> sheet, then a reply and a follow-up ------


def test_a_prospect_in_the_sheet_is_proposed_approved_sent_and_followed_up(deployment):
    d = deployment
    d.add_prospect("alpha")

    results = d.tick()
    assert results and all(r.ok for r in results)
    (action,) = d.actions("alpha")
    assert action["status"] == "proposed" and action["draft_text"] == "[alpha] hello Alex"
    assert d.attempts("alpha") == ()  # approval mode: nothing leaves before a person says so

    d.approve("alpha")
    assert all(r.ok for r in d.tick())
    (action,) = d.actions("alpha")
    assert action["status"] == "sent"
    (attempt,) = d.attempts("alpha")
    assert attempt.recipient == phone_for("p1") and attempt.draft.text == "[alpha] hello Alex"
    (outbound,) = d.messages("alpha", "outbound")
    assert outbound["text"] == "[alpha] hello Alex"
    prospect = d.prospect("alpha")
    assert prospect["status"] == "active" and prospect["stage"] == "warm"
    assert prospect["next_action_type"] == "follow_up" and prospect["last_contacted_at"]

    for _ in range(3):  # time passing changes nothing: no second proposal, no second send
        d.tick()
    assert len(d.actions("alpha")) == 1 and len(d.attempts("alpha")) == 1

    d.tick(timedelta(days=4))  # Monday: the follow-up is due and the agent proposes it
    follow_up = [a for a in d.actions("alpha") if a["action_type"] == "follow_up"]
    assert len(follow_up) == 1 and follow_up[0]["status"] == "proposed"
    d.approve("alpha")
    d.tick()
    assert len(d.attempts("alpha")) == 2
    assert [a["status"] for a in d.actions("alpha")] == ["sent", "sent"]
    assert d.prospect("alpha")["follow_up_count"] in (1, "1")


def test_a_reply_is_stored_in_the_sheet_and_answered_with_a_proposal(deployment):
    d = deployment
    d.add_prospect("alpha")
    d.tick()
    d.approve("alpha")
    d.tick()

    response = d.prospect_says("alpha", "Tell me more")
    assert response.status_code == 200 and response.json() == {"received": 1}
    (inbound,) = d.messages("alpha", "inbound")
    assert inbound["text"] == "Tell me more"
    assert d.prospect("alpha")["last_inbound_at"]
    reply = [a for a in d.actions("alpha") if a["action_type"] == "reply"]
    assert len(reply) == 1 and reply[0]["status"] == "proposed"

    d.approve("alpha")
    d.tick()
    assert len(d.attempts("alpha")) == 2
    assert [m["direction"] for m in d.messages("alpha")] == ["outbound", "inbound", "outbound"]


def test_a_webhook_delivered_twice_is_stored_and_answered_once(deployment):
    d = deployment
    d.add_prospect("alpha")
    d.prospect_says("alpha", "Hello?", wamid="wamid.same")
    d.prospect_says("alpha", "Hello?", wamid="wamid.same")
    assert len(d.messages("alpha", "inbound")) == 1
    assert len([a for a in d.actions("alpha") if a["action_type"] == "reply"]) == 1


# ---- consent -------------------------------------------------------------------------------


def test_an_opt_out_reaches_the_sheet_and_nothing_more_is_ever_sent(deployment):
    d = deployment
    d.add_prospect("alpha")
    d.tick()
    d.approve("alpha")  # approved, not yet sent
    d.prospect_says("alpha", "STOP")
    assert d.prospect("alpha")["status"] == "opted_out"

    d.tick()
    d.tick(timedelta(days=4))
    assert d.attempts("alpha") == ()
    assert all(a["status"] != "sent" for a in d.actions("alpha"))


def test_a_person_who_pauses_a_prospect_in_the_sheet_stops_the_send(deployment):
    d = deployment
    d.add_prospect("alpha")
    d.tick()
    d.approve("alpha")
    api = d.sheets["alpha"]
    api.human_set("Prospects", api.find_row("Prospects", "prospect_id", "p1"), "status", "paused")
    d.tick()
    assert d.attempts("alpha") == () and d.prospect("alpha")["status"] == "paused"


# ---- restart and recovery ------------------------------------------------------------------


def test_restarts_never_duplicate_a_proposal_or_a_send(deployment):
    d = deployment
    d.add_prospect("alpha")
    d.tick()
    d.restart()
    d.tick()
    assert len(d.actions("alpha")) == 1  # the proposal was found again, not made again

    d.approve("alpha")
    d.restart()
    d.tick()
    assert len(d.attempts("alpha")) == 1 and d.actions("alpha")[0]["status"] == "sent"

    d.restart()
    d.tick()
    d.tick(timedelta(hours=2))
    assert len(d.attempts("alpha")) == 1 and len(d.messages("alpha", "outbound")) == 1
    assert d.prospect("alpha")["status"] == "active"


def test_a_reply_that_arrived_while_the_agent_was_paused_is_answered_once_it_starts(tmp_path):
    d = Deployment(tmp_path, "alpha", running=False)
    try:
        d.add_prospect("alpha")
        d.prospect_says("alpha", "Anyone there?")
        assert len(d.messages("alpha", "inbound")) == 1 and d.actions("alpha") == []
        assert d.tick() == []  # nothing runs for an agent its client has not started

        d.start_agent("alpha")
        d.tick()
        assert len([a for a in d.actions("alpha") if a["action_type"] == "reply"]) == 1
    finally:
        d.application.close()


def test_a_sheet_that_fails_for_a_moment_costs_a_delay_not_a_duplicate(deployment):
    d = deployment
    d.add_prospect("alpha")
    d.sheets["alpha"].fail_next("read", times=1)
    failed = d.tick()
    assert failed and not all(r.ok for r in failed)
    assert d.actions("alpha") == []

    d.tick(timedelta(minutes=2))  # past the backoff
    d.tick()
    assert len(d.actions("alpha")) == 1
    health = d.application.registry.get("alpha").health.snapshot()
    assert health["consecutive_failures"] == 0


# ---- isolation -----------------------------------------------------------------------------


def test_two_clients_with_the_same_prospect_id_and_number_never_meet(pair):
    d = pair
    for cid in ("alpha", "beta"):
        d.add_prospect(cid)  # the same id, the same phone number, two spreadsheets
    d.tick()
    assert [a["draft_text"] for a in d.actions("alpha")] == ["[alpha] hello Alex"]
    assert [a["draft_text"] for a in d.actions("beta")] == ["[beta] hello Alex"]

    d.approve("alpha")  # only alpha's team approved
    d.tick()
    assert len(d.attempts("alpha")) == 1 and d.attempts("beta") == ()

    d.prospect_says("beta", "STOP")  # beta's prospect refuses; alpha's is another person
    assert d.prospect("beta")["status"] == "opted_out"
    assert d.prospect("alpha")["status"] == "active"
    assert d.messages("alpha", "inbound") == []

    d.prospect_says("alpha", "Interested", wamid="wamid.a")
    assert len(d.messages("alpha", "inbound")) == 1 and len(d.messages("beta", "inbound")) == 1


def test_a_person_editing_a_prospect_while_the_agent_writes_is_never_overwritten(deployment):
    d = deployment
    d.add_prospect("alpha")
    d.tick()
    d.approve("alpha")
    api = d.sheets["alpha"]
    row = api.find_row("Prospects", "prospect_id", "p1")
    # The team closes the prospect in the very instant the agent is about to record its send.
    api.before("write", lambda: api.human_set("Prospects", row, "status", "closed"))
    results = d.tick()
    # The send had already been decided: it is recorded as sent. Writing the prospect's new state
    # is what the person's edit refused, and the refusal is reported rather than hidden.
    assert len(d.attempts("alpha")) == 1 and d.actions("alpha")[0]["status"] == "sent"
    assert any("changed to 'closed' since it was read" in e for r in results for e in r.errors)
    assert d.prospect("alpha")["status"] == "closed"  # the person's edit stands
    for _ in range(3):  # and the agent never contacts a closed prospect again
        d.tick(timedelta(hours=1))
    assert d.prospect("alpha")["status"] == "closed" and len(d.attempts("alpha")) == 1
    assert len(d.actions("alpha")) == 1
