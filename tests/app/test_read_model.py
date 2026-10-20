"""The read model (prospects and conversations by SQL) and the metrics, on a real migrated database.

SIMULATED: nothing. REAL: the Alembic migration (0022), the SQL store that fills the index, the SQL
adapters, the pure folding. SQLite stands in for PostgreSQL.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from factories import CLIENT, NOW, make_action, make_message, make_prospect
from sql_store_support import SqlStoreUnderTest
from sqlalchemy import text

from sales_app.metrics import MetricsService, NotTracked, Period, fold_by_product, lanes_selling
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.metrics_rows import SqlMetricsRows
from sales_app.persistence.read_model_rows import SqlProspectReadModel
from sales_app.persistence.tables import op_prospect_index
from sales_app.read_model import (
    MessageOrder,
    ProspectFilter,
    ProspectListQuery,
    ProspectSort,
    list_in_memory,
)
from sales_core.contracts import (
    ActionStatus,
    Channel,
    ContactInfo,
    Direction,
    MessageId,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)

OTHER = "client-b"


def person(pid, name, **overrides):
    return make_prospect(
        client_id=overrides.pop("client_id", CLIENT),
        prospect_id=ProspectId(pid),
        display_name=name,
        **overrides,
    )


@pytest.fixture
def world(sql_engine):
    store = SqlStoreUnderTest(sql_engine, CLIENT)
    model = SqlProspectReadModel(sql_engine, CLIENT)
    return store, model, sql_engine


def seed(store):
    store.index_prospects(
        [
            person(
                "p1",
                "Amel Ben Salah",
                status=ProspectStatus.ACTIVE,
                lane_id="sheet-a",
                contact=ContactInfo(
                    phone="+216 20 000 001",
                    email="amel@example.com",
                    preferred_channel=Channel.EMAIL,
                ),
                schedule=Scheduling(last_contacted_at=NOW - timedelta(days=2)),
            ),
            person(
                "p2",
                "Zied Trabelsi",
                status=ProspectStatus.PAUSED,
                lane_id="crm-b",
                contact=ContactInfo(phone="+21620000002", preferred_channel=Channel.WHATSAPP),
                schedule=Scheduling(last_inbound_at=NOW - timedelta(days=1)),
            ),
            person(
                "p3",
                "Bilel",
                lane_id="sheet-a",
                contact=ContactInfo(preferred_channel=Channel.SMS),
            ),
        ],
        {"p1": ("sheet", "p1"), "p2": ("crm", "p2"), "p3": ("sheet", "p3")},
    )


def ids(page):
    return [i.prospect_id for i in page.items]


class TestList:
    def test_empty_until_indexed(self, world):
        store, model, _ = world
        assert model.is_populated() is False
        seed(store)
        assert model.is_populated() is True

    def test_paging_total_and_sort(self, world):
        store, model, _ = world
        seed(store)
        page = model.list_prospects(ProspectListQuery(limit=2))
        assert (page.total, ids(page)) == (3, ["p1", "p3"])  # by name: Amel, Bilel
        assert ids(model.list_prospects(ProspectListQuery(limit=2, offset=2))) == ["p2"]
        recent = model.list_prospects(ProspectListQuery(sort=ProspectSort.RECENT))
        assert ids(recent) == ["p2", "p1", "p3"]  # latest activity first, none last

    def test_filters(self, world):
        store, model, _ = world

        def find(**f):
            return ids(model.list_prospects(ProspectListQuery(filter=ProspectFilter(**f))))

        seed(store)
        assert find(statuses=("paused",)) == ["p2"]
        assert find(lane_ids=("sheet-a",)) == ["p1", "p3"]
        assert find(lane_ids=()) == []
        assert find(channel="whatsapp") == ["p2"]
        assert find(source_id="crm") == ["p2"]
        assert find(needs_human=True) == ["p2"]
        assert find(active_since=NOW - timedelta(days=1, hours=1)) == ["p2"]
        assert find(active_until=NOW - timedelta(days=1, hours=1)) == ["p1"]

    def test_search_by_name_email_and_phone(self, world):
        store, model, _ = world
        seed(store)

        def find(q):
            return ids(model.list_prospects(ProspectListQuery(filter=ProspectFilter(search=q))))

        assert find("amel") == ["p1"]
        assert find("BEN salah") == ["p1"]
        assert find("example.com") == ["p1"]
        assert find("20000001") == ["p1"]  # digits of "+216 20 000 001"
        assert find("+21620000002") == ["p2"]
        assert find("100%") == []  # a wildcard is a character, not a wildcard

    def test_errors_come_from_failed_actions(self, world):
        store, model, _ = world
        seed(store)
        store.save_actions(
            [
                replace(
                    make_action(prospect_id=ProspectId("p1")),
                    status=ActionStatus.FAILED,
                    last_error="channel refused",
                )
            ]
        )
        page = model.list_prospects(ProspectListQuery(filter=ProspectFilter(has_error=True)))
        assert ids(page) == ["p1"] and page.items[0].has_error is True
        assert ids(
            model.list_prospects(ProspectListQuery(filter=ProspectFilter(has_error=False)))
        ) == ["p3", "p2"]

    def test_a_client_never_sees_another(self, world):
        store, _model, engine = world
        seed(store)
        other = SqlProspectReadModel(engine, OTHER)
        assert other.is_populated() is False
        assert other.list_prospects(ProspectListQuery()).total == 0
        assert other.conversation("p1", limit=10, offset=0, order=MessageOrder.ASC) is None

    def test_refresh_is_idempotent_and_updates(self, world):
        store, model, _ = world
        seed(store)
        same = person(
            "p3", "Bilel", lane_id="sheet-a", contact=ContactInfo(preferred_channel=Channel.SMS)
        )
        assert store.index_prospects([same]) == 0
        changed = store.index_prospects([replace(same, status=ProspectStatus.CLOSED)])
        assert changed == 1
        assert (
            model.list_prospects(
                ProspectListQuery(filter=ProspectFilter(statuses=("closed",)))
            ).total
            == 1
        )

    def test_one_person_once_per_lane_and_external_id(self, world):
        store, model, _ = world
        store.index_prospects(
            [person("a:1", "One", lane_id="L"), person("b:1", "Two", lane_id="L")],
            {"a:1": ("a", "1"), "b:1": ("b", "1")},
        )
        # the database refused the second one (same lane, same external id): it was skipped
        assert model.list_prospects(ProspectListQuery()).total == 1


class TestConversations:
    def talk(self, store):
        seed(store)
        store.append_messages(
            [
                make_message(
                    message_id=MessageId(f"m{i}"),
                    prospect_id=ProspectId("p1"),
                    conversation_id="conv-p1",
                    direction=Direction.OUTBOUND if i % 2 else Direction.INBOUND,
                    timestamp=NOW + timedelta(minutes=i),
                    text=f"t{i}",
                )
                for i in range(1, 6)
            ]
        )
        store.append_messages(
            [
                make_message(
                    message_id=MessageId("z1"),
                    prospect_id=ProspectId("p2"),
                    conversation_id="conv-p2",
                    direction=Direction.INBOUND,
                    timestamp=NOW + timedelta(hours=1),
                    channel=Channel.WHATSAPP,
                )
            ]
        )

    def test_list_only_people_with_messages_latest_first(self, world):
        store, model, _ = world
        self.talk(store)
        page = model.list_conversations(ProspectListQuery(sort=ProspectSort.RECENT))
        assert page.total == 2
        first, second = page.items
        assert first.prospect.prospect_id == "p2" and first.message_count == 1
        assert first.last_message_direction == "inbound"
        assert second.message_count == 5 and second.last_message_at == NOW + timedelta(minutes=5)

    def test_detail_pages_and_orders_messages(self, world):
        store, model, _ = world
        self.talk(store)
        d = model.conversation("p1", limit=2, offset=0, order=MessageOrder.ASC)
        assert [m.text for m in d.messages] == ["t1", "t2"] and d.total_messages == 5
        d = model.conversation("p1", limit=2, offset=0, order=MessageOrder.DESC)
        assert [m.text for m in d.messages] == ["t5", "t4"]
        d = model.conversation("p1", limit=2, offset=4, order=MessageOrder.ASC)
        assert [m.text for m in d.messages] == ["t5"]
        assert d.conversation_id == "conv-p1"

    def test_detail_reports_failed_actions(self, world):
        store, model, _ = world
        self.talk(store)
        store.save_actions(
            [
                replace(
                    make_action(
                        prospect_id=ProspectId("p1"), action_type=SalesActionType.FOLLOW_UP
                    ),
                    status=ActionStatus.FAILED,
                    last_error="x" * 500,
                    attempts=2,
                )
            ]
        )
        (error,) = model.conversation("p1", limit=5, offset=0, order=MessageOrder.ASC).errors
        assert error.action_type == "follow_up" and error.attempts == 2
        assert len(error.reason) == 300


class TestMemoryFallbackMatchesSql:
    def test_same_answer(self, world):
        store, model, _ = world
        seed(store)
        items = list(model.list_prospects(ProspectListQuery()).items)
        for f in (
            ProspectFilter(search="amel"),
            ProspectFilter(statuses=("paused",)),
            ProspectFilter(lane_ids=("sheet-a",)),
            ProspectFilter(active_since=NOW - timedelta(days=1, hours=1)),
        ):
            q = ProspectListQuery(filter=f, sort=ProspectSort.RECENT)
            assert ids(list_in_memory(items, q)) == ids(model.list_prospects(q))


class TestMigrationBackfill:
    def test_columns_and_index_are_filled_from_documents(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine, "0018")
        doc_msg = (
            '{"message_id":"m1","client_id":"c","prospect_id":"p1","conversation_id":"k",'
            '"direction":"inbound","channel":"email","text":"hi",'
            '"timestamp":"2026-01-15T09:00:00+00:00","external_id":null,"metadata":{}}'
        )
        state = (
            '{"status":"paused","stage":null,"schedule":{"next_action_type":null,'
            '"next_action_due_at":null,"last_contacted_at":null,'
            '"last_inbound_at":"2026-01-15T09:00:00+00:00","follow_up_count":0},'
            '"conversation_id":"k","telegram_chat_id":null,"lane_id":"L",'
            '"status_reason":null,"status_changed_at":null}'
        )
        with engine.begin() as c:
            c.execute(text("insert into clients(client_id, created_at) values ('c','2026-01-01')"))
            c.execute(
                text(
                    "insert into op_messages(client_id,message_id,prospect_id,conversation_id,document)"
                    " values ('c','m1','p1','k',:d)"
                ),
                {"d": doc_msg},
            )
            c.execute(
                text(
                    "insert into op_prospect_states(client_id,prospect_id,revision,conversation_id,document)"
                    " values ('c','p1',1,'k',:d)"
                ),
                {"d": state},
            )
            c.execute(
                text(
                    "insert into op_messages(client_id,message_id,prospect_id,conversation_id,document)"
                    " values ('c','bad','p9','k','{}')"
                )
            )
        upgrade_database(engine)
        with engine.connect() as c:
            row = c.execute(
                text("select direction, channel, sent_at from op_messages where message_id='m1'")
            ).one()
            assert row[0] == "inbound" and row[1] == "email" and "2026-01-15" in str(row[2])
            bad = c.execute(text("select direction from op_messages where message_id='bad'")).one()
            assert bad[0] is None  # an unreadable document is left empty, not guessed
            idx = c.execute(
                text("select status, lane_id, needs_human, last_activity_at from op_prospect_index")
            ).one()
            assert idx[0] == "paused" and idx[1] == "L" and idx[2] in (1, True) and idx[3]
        assert op_prospect_index.name == "op_prospect_index"


class FakeReader:
    def __init__(self, **kw):
        self.kw = kw

    def prospect_counts(self, by, *, since=None, until=None):
        return self.kw["counts"][by]

    def prospect_total(self):
        return sum(self.kw["counts"]["status"].values())

    def needs_human(self):
        return 1

    def message_counts(self, *, since, until):
        return {("outbound", "email"): 4, ("inbound", "email"): 1}

    def distinct_prospects_by_direction(self, *, since, until):
        return {"outbound": 0}

    def distinct_prospects_with_messages(self, *, since, until):
        return 0

    def messages_by_day(self, *, since, until):
        return {}

    def prospects_with_failed_actions(self, *, since, until):
        return 0

    def fact_documents(self, keys, *, since, until):
        return []


class TestMetrics:
    def test_pure_folding(self):
        lanes = {"a": ["P1", "P2"], "b": [], "c": ["P1"]}
        assert fold_by_product({"a": 3, "b": 2, "c": 1, None: 4, "gone": 1}, lanes) == {
            "P1": 4,
            "unassigned": 5,
            "ALL": 2,
            "P2": 3,
        }
        assert lanes_selling("P2", lanes) == ("a", "b")

    def test_nothing_invented(self):
        counts = {"status": {"new": 2}, "lane": {None: 2}, "channel": {}, "source": {}}
        m = MetricsService(FakeReader(counts=counts)).client_metrics(Period())
        assert isinstance(m.reply_rate_percent, NotTracked)  # nobody contacted: no rate
        assert isinstance(m.conversions, NotTracked)
        assert m.payments.declared == 0 and m.payments.confirmed == 0
        assert m.to_json()["conversions"]["tracked"] is False
        assert m.by_lane == {"unassigned": 2}

    def test_sql_metrics_end_to_end(self, world):
        store, _, engine = world
        TestConversations().talk(store)
        service = MetricsService(SqlMetricsRows(engine, CLIENT), lambda: {"sheet-a": ["P1"]})
        m = service.client_metrics(Period())
        assert m.prospects_total == 3
        assert m.by_status["paused"] == 1
        assert m.messages_sent == 3 and m.replies_received == 3
        assert m.conversations == 2 and m.prospects_replied == 2 and m.prospects_contacted == 1
        assert m.reply_rate_percent == 200.0  # replied / contacted, inbound-first people included
        assert m.needs_human == 1
        narrow = service.client_metrics(
            Period(since=NOW + timedelta(minutes=30), until=NOW + timedelta(hours=2))
        )
        assert narrow.messages_sent == 0 and narrow.replies_received == 1
        other = MetricsService(SqlMetricsRows(engine, OTHER)).client_metrics(Period())
        assert other.prospects_total == 0 and other.messages_sent == 0
        assert m.messages_by_day["2026-01-15"] == {"inbound": 3, "outbound": 3}
