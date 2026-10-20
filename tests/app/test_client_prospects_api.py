"""`/client/v1/prospects` and `/client/v1/conversations/{id}`: a client reads its own CRM, read-only.

SIMULATED here: the messaging channel, the Brain, the model and the client login. REAL here: the
application (registry, runtime lease, session cookie), the routes, the Engine, and the repository
behind it: the in-memory implementation of the `SalesRepository` port for the API tests, and, in the
last class, the SQL operational store (a SQLite file) behind the real `CompositeRepository`.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from consent_support import make_world
from engine_harness import ScriptedBrain, phone_for, send
from factories import NOW, make_message, make_prospect
from sourcing_fakes import FakeProspectSource, default_capabilities
from test_client_agent_api import World

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.sourcing.composite import CompositeRepository
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    Channel,
    ClientContext,
    ContactInfo,
    ConversationId,
    Direction,
    MessageId,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)
from sales_core.engine import Engine, EngineConfig
from sales_integrations.fakes import FakeClock, FakeMessagingChannel

PROSPECTS = "/client/v1/prospects"


def conversation(pid):
    return f"/client/v1/conversations/{pid}"


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.application.close()


def add(world, cid, *names, **overrides):
    """Prospects `p1`, `p2`... named as given, for `cid`."""
    world.made.repositories[cid].add_prospects(
        *(
            make_prospect(
                client_id=cid, prospect_id=ProspectId(f"p{i}"), display_name=name, **overrides
            )
            for i, name in enumerate(names, start=1)
        )
    )


def say(world, cid, pid, *texts, direction=Direction.OUTBOUND, start=1):
    world.made.repositories[cid].append_messages(
        [
            make_message(
                client_id=cid,
                prospect_id=ProspectId(pid),
                conversation_id=ConversationId(f"conv-{pid}"),
                message_id=MessageId(f"{cid}-{pid}-m{i}"),
                direction=direction,
                text=text,
                timestamp=NOW + timedelta(minutes=i),
            )
            for i, text in enumerate(texts, start=start)
        ]
    )


class TestProspects:
    def test_it_needs_a_client_session(self, world):
        assert world.anonymous().get(PROSPECTS).status_code == 401
        assert world.anonymous().get(conversation("p1")).status_code == 401

    def test_a_client_without_prospects_gets_an_empty_page(self, world):
        response = world.browser("alpha").get(PROSPECTS)
        assert response.status_code == 200
        assert response.json() == {"total": 0, "limit": 50, "offset": 0, "prospects": []}
        assert response.headers["cache-control"] == "no-store"

    def test_it_returns_what_the_repository_holds_and_nothing_invented(self, world):
        due = NOW + timedelta(days=2)
        schedule = Scheduling(
            next_action_type=SalesActionType.FOLLOW_UP,
            next_action_due_at=due,
            last_contacted_at=NOW - timedelta(days=1),
            follow_up_count=1,
        )
        world.made.repositories["alpha"].add_prospects(
            make_prospect(
                client_id="alpha",
                prospect_id=ProspectId("p1"),
                display_name="Amel Ben Salah",
                status=ProspectStatus.ACTIVE,
                stage="qualified",
                locale="fr",
                contact=ContactInfo(
                    phone="+21620000001", email="amel@example.com", preferred_channel=Channel.EMAIL
                ),
                schedule=schedule,
                attributes={"free_column": "kept in the source"},
            )
        )
        response = world.browser("alpha").get(PROSPECTS)
        (found,) = response.json()["prospects"]
        assert found == {
            "id": "p1",
            "display_name": "Amel Ben Salah",
            "status": "active",
            "stage": "qualified",
            "locale": "fr",
            "lane_id": None,
            "phone": "+21620000001",
            "email": "amel@example.com",
            "preferred_channel": "email",
            "next_action_type": "follow_up",
            "next_action_due_at": iso(due),
            "last_contacted_at": iso(NOW - timedelta(days=1)),
            "last_inbound_at": None,
            "follow_up_count": 1,
            # added with the read model
            "source_id": None,
            "last_activity_at": iso(NOW - timedelta(days=1)),
            "needs_human": False,
            "status_reason": None,
            "has_error": None,
        }
        assert "free_column" not in response.text

    def test_it_follows_the_repository_when_it_changes(self, world):
        add(world, "alpha", "Amel")
        browser = world.browser("alpha")
        assert browser.get(PROSPECTS).json()["prospects"][0]["status"] == "new"
        repository = world.made.repositories["alpha"]
        repository.update_prospects(
            [replace(repository.get_prospect(ProspectId("p1")), status=ProspectStatus.OPTED_OUT)]
        )
        assert browser.get(PROSPECTS).json()["prospects"][0]["status"] == "opted_out"

    def test_pages_are_stable_ordered_by_name_and_cover_everyone_once(self, world):
        add(world, "alpha", "Dora", "bea", "Amel", "Chedly", "Emna")  # case-insensitive order
        browser = world.browser("alpha")
        pages = [
            browser.get(PROSPECTS, params={"limit": 2, "offset": offset}).json()
            for offset in (0, 2, 4)
        ]
        assert [page["total"] for page in pages] == [5, 5, 5]
        assert [(page["limit"], page["offset"]) for page in pages] == [(2, 0), (2, 2), (2, 4)]
        assert [len(page["prospects"]) for page in pages] == [2, 2, 1]
        names = [p["display_name"] for page in pages for p in page["prospects"]]
        assert names == ["Amel", "bea", "Chedly", "Dora", "Emna"]

    def test_a_page_past_the_end_is_empty_but_still_gives_the_total(self, world):
        add(world, "alpha", "Amel")
        body = world.browser("alpha").get(PROSPECTS, params={"offset": 10}).json()
        assert body["prospects"] == [] and body["total"] == 1

    @pytest.mark.parametrize(
        "params", [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}, {"limit": "x"}]
    )
    def test_bad_pagination_is_refused(self, world, params):
        assert world.browser("alpha").get(PROSPECTS, params=params).status_code == 422

    def test_an_agent_that_takes_no_work_answers_a_conflict(self, world):
        assert world.browser("gamma").get(PROSPECTS).status_code == 409
        assert world.browser("gamma").get(conversation("p1")).status_code == 409

    def test_a_failing_store_answers_503_without_naming_the_cause(self, world):
        world.made.repositories["alpha"].fail_next("list_prospects", RuntimeError("db password"))
        response = world.browser("alpha").get(PROSPECTS)
        assert response.status_code == 503 and "db password" not in response.text


SUMMARY = "/client/v1/summary"


def schedule(kind, days):
    return Scheduling(next_action_type=kind, next_action_due_at=NOW + timedelta(days=days))


class TestSummary:
    def test_it_needs_a_client_session(self, world):
        assert world.anonymous().get(SUMMARY).status_code == 401

    def test_a_client_without_prospects_gets_real_zeros_and_no_next_date(self, world):
        response = world.browser("alpha").get(SUMMARY)
        assert response.status_code == 200
        body = response.json()
        assert body["total_prospects"] == 0
        assert body["by_status"] == {
            "new": 0,
            "active": 0,
            "paused": 0,
            "opted_out": 0,
            "closed": 0,
        }
        assert body["upcoming_actions"] == {"scheduled": 0, "by_type": {}, "next_due_at": None}
        assert response.headers["cache-control"] == "no-store"

    def test_it_counts_statuses_and_only_the_actions_that_can_still_happen(self, world):
        repository = world.made.repositories["alpha"]
        rows = [
            ("p1", ProspectStatus.NEW, schedule(SalesActionType.FOLLOW_UP, 3)),
            ("p2", ProspectStatus.ACTIVE, schedule(SalesActionType.FOLLOW_UP, 1)),
            ("p3", ProspectStatus.ACTIVE, schedule(SalesActionType.CALLBACK, 2)),
            ("p4", ProspectStatus.ACTIVE, Scheduling()),
            ("p5", ProspectStatus.PAUSED, schedule(SalesActionType.REMINDER, 1)),
            ("p6", ProspectStatus.OPTED_OUT, schedule(SalesActionType.FOLLOW_UP, 1)),
            ("p7", ProspectStatus.CLOSED, schedule(SalesActionType.FOLLOW_UP, 1)),
        ]
        repository.add_prospects(
            *(
                make_prospect(
                    client_id="alpha",
                    prospect_id=ProspectId(pid),
                    display_name=pid,
                    status=status,
                    schedule=scheduling,
                )
                for pid, status, scheduling in rows
            )
        )
        body = world.browser("alpha").get(SUMMARY).json()
        assert body["total_prospects"] == 7
        assert body["by_status"] == {
            "new": 1,
            "active": 3,
            "paused": 1,
            "opted_out": 1,
            "closed": 1,
        }
        assert body["upcoming_actions"] == {
            "scheduled": 3,
            "by_type": {"follow_up": 2, "callback": 1},
            "next_due_at": iso(NOW + timedelta(days=1)),
        }

    def test_it_agrees_with_the_prospects_list(self, world):
        add(world, "alpha", "Amel", "Bea", "Chedly")
        browser = world.browser("alpha")
        assert (
            browser.get(SUMMARY).json()["total_prospects"] == browser.get(PROSPECTS).json()["total"]
        )

    def test_it_follows_the_repository_when_it_changes(self, world):
        add(world, "alpha", "Amel")
        browser = world.browser("alpha")
        assert browser.get(SUMMARY).json()["by_status"]["new"] == 1
        repository = world.made.repositories["alpha"]
        repository.update_prospects(
            [replace(repository.get_prospect(ProspectId("p1")), status=ProspectStatus.CLOSED)]
        )
        by_status = browser.get(SUMMARY).json()["by_status"]
        assert by_status["new"] == 0 and by_status["closed"] == 1

    def test_each_client_counts_only_its_own_prospects(self, world):
        add(world, "alpha", "Alpha Person")
        add(world, "beta", "Beta One", "Beta Two", status=ProspectStatus.ACTIVE)
        alpha = world.browser("alpha").get(SUMMARY).json()
        beta = world.browser("beta").get(SUMMARY).json()
        assert alpha["total_prospects"] == 1 and alpha["by_status"]["new"] == 1
        assert beta["total_prospects"] == 2 and beta["by_status"]["active"] == 2

    def test_a_client_id_in_the_request_is_ignored(self, world):
        add(world, "beta", "Beta Person")
        body = world.browser("alpha").get(SUMMARY, params={"client_id": "beta"}).json()
        assert body["total_prospects"] == 0

    def test_an_agent_that_takes_no_work_answers_a_conflict_not_zeros(self, world):
        assert world.browser("gamma").get(SUMMARY).status_code == 409

    def test_a_failing_store_answers_503_without_naming_the_cause(self, world):
        world.made.repositories["alpha"].fail_next("list_prospects", RuntimeError("db password"))
        response = world.browser("alpha").get(SUMMARY)
        assert response.status_code == 503 and "db password" not in response.text
        assert "total_prospects" not in response.text


class TestConversations:
    def test_an_empty_history_is_an_empty_list_not_an_error(self, world):
        add(world, "alpha", "Amel")
        response = world.browser("alpha").get(conversation("p1"))
        assert response.status_code == 200
        body = response.json()
        assert body["prospect_id"] == "p1" and body["messages"] == []
        assert body["origin"] == "unknown" and body["conversation_id"]
        assert response.headers["cache-control"] == "no-store"

    def test_the_messages_come_back_oldest_first_with_only_the_documented_fields(self, world):
        add(world, "alpha", "Amel")
        say(world, "alpha", "p1", "Hello Amel", "Tell me more")
        say(world, "alpha", "p1", "I am interested", direction=Direction.INBOUND, start=3)
        body = world.browser("alpha").get(conversation("p1")).json()
        assert [m["text"] for m in body["messages"]] == [
            "Hello Amel",
            "Tell me more",
            "I am interested",
        ]
        first, _, last = body["messages"]
        assert set(first) == {"id", "direction", "channel", "text", "timestamp", "delivery"}
        assert first["direction"] == "outbound" and first["channel"] == "whatsapp"
        assert last["direction"] == "inbound"
        assert first["timestamp"] == iso(NOW + timedelta(minutes=1))

    def test_only_that_prospects_messages_are_returned(self, world):
        add(world, "alpha", "Amel", "Bea")
        say(world, "alpha", "p1", "for Amel")
        say(world, "alpha", "p2", "for Bea")
        body = world.browser("alpha").get(conversation("p1")).json()
        assert [m["text"] for m in body["messages"]] == ["for Amel"]

    def test_an_unknown_prospect_is_a_plain_not_found(self, world):
        response = world.browser("alpha").get(conversation("nope"))
        assert response.status_code == 404
        assert "nope" not in response.text


class TestClientsNeverMeet:
    def test_each_client_sees_only_its_own_prospects_even_with_the_same_ids(self, world):
        add(world, "alpha", "Alpha Person")
        add(world, "beta", "Beta Person", "Beta Two")
        alpha = world.browser("alpha").get(PROSPECTS).json()
        beta = world.browser("beta").get(PROSPECTS).json()
        assert [p["display_name"] for p in alpha["prospects"]] == ["Alpha Person"]
        assert alpha["total"] == 1 and beta["total"] == 2
        assert "Beta" not in str(alpha) and "Alpha" not in str(beta)

    def test_a_prospect_of_another_client_looks_exactly_like_one_that_does_not_exist(self, world):
        add(world, "beta", "Beta Person")
        say(world, "beta", "p1", "private to beta")
        alpha = world.browser("alpha")
        other, missing = alpha.get(conversation("p1")), alpha.get(conversation("nobody"))
        assert other.status_code == missing.status_code == 404
        assert other.json() == missing.json()
        assert "private" not in other.text

    def test_the_same_prospect_id_never_mixes_two_histories(self, world):
        add(world, "alpha", "Alpha Person")
        add(world, "beta", "Beta Person")
        say(world, "alpha", "p1", "alpha secret")
        say(world, "beta", "p1", "beta secret")
        mine = world.browser("alpha").get(conversation("p1")).json()
        theirs = world.browser("beta").get(conversation("p1")).json()
        assert [m["text"] for m in mine["messages"]] == ["alpha secret"]
        assert [m["text"] for m in theirs["messages"]] == ["beta secret"]

    def test_a_client_id_in_the_request_is_ignored(self, world):
        add(world, "beta", "Beta Person")
        body = world.browser("alpha").get(PROSPECTS, params={"client_id": "beta"}).json()
        assert body["total"] == 0


class TestOnTheSqlStore:
    """The real `CompositeRepository` (a fake CRM read + the SQL store) behind the Engine's reads."""

    CLIENT = "alpha"

    def test_prospects_and_history_come_from_the_operational_store(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'ops.db'}"
        database = create_database_engine(url)
        upgrade_database(database)
        try:
            source = FakeProspectSource(
                self.CLIENT, capabilities=default_capabilities(), allow_writes=True
            )
            for pid, name in (("2", "Zied"), ("1", "Amel")):
                source.add_record(
                    make_prospect(
                        prospect_id=ProspectId(pid),
                        client_id=self.CLIENT,
                        display_name=name,
                        contact=ContactInfo(phone=phone_for(pid), email=None),
                    )
                )
            registry = make_world(tmp_path, client_id=self.CLIENT).registry
            store = SqlOperationalStore(SqlOperationalRows(database, self.CLIENT))
            repository = CompositeRepository(source, store, registry)
            engine = Engine(
                client=ClientContext(
                    client_id=self.CLIENT,
                    display_name="Alpha",
                    timezone="Africa/Tunis",
                    settings={},
                ),
                repository=repository,
                brain=ScriptedBrain(lambda request: send("Hi")),
                channel=FakeMessagingChannel(clock=FakeClock(NOW)),
                clock=FakeClock(NOW),
                config=EngineConfig(mode=ApprovalMode.APPROVAL),
            )
            listed = engine.list_prospects()
            assert [p.display_name for p in listed] == ["Amel", "Zied"]
            amel = listed[0].prospect_id  # the id the composite repository gives it
            empty = engine.conversation_of(amel)
            assert empty is not None and empty.messages == ()
            repository.append_messages(
                [
                    make_message(
                        client_id=self.CLIENT,
                        prospect_id=amel,
                        conversation_id=empty.conversation_id,
                        message_id=MessageId("m-sql-1"),
                        text="stored in SQL",
                    )
                ]
            )
            found = engine.conversation_of(amel)
            assert found is not None and [m.text for m in found.messages] == ["stored in SQL"]
            assert engine.conversation_of(ProspectId("404")) is None
        finally:
            database.dispose()


class TestFiltersThroughTheEngineView:
    """A client with no read model yet is listed through the Engine, with the same filters."""

    def test_search_status_and_sort_apply_in_memory(self, world):
        add(world, "alpha", "Amel Ben Salah", "Zied")
        got = world.browser("alpha").get(PROSPECTS, params={"q": "zied"}).json()
        assert [p["id"] for p in got["prospects"]] == ["p2"] and got["total"] == 1
        got = world.browser("alpha").get(PROSPECTS, params={"status": "closed"}).json()
        assert got["total"] == 0
        assert world.browser("alpha").get(PROSPECTS, params={"status": "nope"}).status_code == 422
        assert world.browser("alpha").get(PROSPECTS, params={"sort": "recent"}).status_code == 200

    def test_the_conversation_list_and_message_paging(self, world):
        add(world, "alpha", "Amel")
        say(world, "alpha", "p1", "a", "b", "c")
        params = {"limit": 2, "order": "desc"}
        body = world.browser("alpha").get(conversation("p1"), params=params).json()
        assert [m["text"] for m in body["messages"]] == ["c", "b"]
        assert body["total_messages"] == 3 and body["order"] == "desc"
        too_many = world.browser("alpha").get(conversation("p1"), params={"limit": 501})
        assert too_many.status_code == 422
        assert world.browser("alpha").get("/client/v1/conversations").status_code == 200


class TestOnTheReadModel:
    """With a database the lists come from SQL, even when the agent is not loaded."""

    def test_lists_and_detail_do_not_need_the_engine(self, world, sql_engine, monkeypatch):
        from sales_app.persistence.clients import ensure_client
        from sales_app.persistence.read_model_rows import SqlProspectReadModel

        with sql_engine.begin() as connection:
            ensure_client(connection, "alpha", created_at=NOW, created_by="test")
        store = SqlOperationalStore(SqlOperationalRows(sql_engine, "alpha"))
        store.index_prospects(
            [
                make_prospect(
                    client_id="alpha",
                    prospect_id=ProspectId("p1"),
                    display_name="Amel",
                    status=ProspectStatus.PAUSED,
                    lane_id="L1",
                ),
                make_prospect(
                    client_id="alpha",
                    prospect_id=ProspectId("p2"),
                    display_name="Zied",
                    lane_id="L2",
                ),
            ]
        )
        store.append_messages(
            [
                make_message(
                    client_id="alpha",
                    prospect_id=ProspectId("p1"),
                    conversation_id=ConversationId("conv-p1"),
                    message_id=MessageId("m1"),
                    text="hello",
                )
            ]
        )
        world.application.read_models = lambda cid: SqlProspectReadModel(sql_engine, cid)
        monkeypatch.setattr(world.application.registry, "get", lambda cid: None)  # no agent loaded
        alpha = world.browser("alpha")
        got = alpha.get(PROSPECTS, params={"needs_human": "true"}).json()
        assert [p["id"] for p in got["prospects"]] == ["p1"] and got["prospects"][0]["needs_human"]
        assert alpha.get(PROSPECTS, params={"lane": "L2"}).json()["total"] == 1
        listed = alpha.get("/client/v1/conversations").json()
        assert listed["total"] == 1 and listed["conversations"][0]["message_count"] == 1
        detail = alpha.get(conversation("p1")).json()
        assert [m["text"] for m in detail["messages"]] == ["hello"]
        assert detail["prospect"]["status"] == "paused" and detail["errors"] == []
