"""The SQL operational store beyond the shared contract: persistence, isolation between clients,
what the database itself enforces, the migration, and the store composed with a source.

The port's own contract (`test_sourcing_contracts.py`) already runs against this store; these tests
cover what only a real database can get wrong.
"""

import threading
from datetime import UTC, datetime

import pytest
from factories import CLIENT, NOW, make_action, make_message, make_prospect
from sql_store_support import SqlStoreUnderTest
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.persistence.schema import current_revision, head_revision
from sales_app.persistence.tables import (
    metadata,
    op_local_contacts,
    op_local_prospects,
    op_prospect_states,
)
from sales_app.sourcing import (
    ProspectState,
    StateConflictError,
    StoreUnavailableError,
)
from sales_app.sourcing.errors import StoreError
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_core.contracts import (
    ActionId,
    AuditEvent,
    Channel,
    ContactInfo,
    Fact,
    FactEvidence,
    FactSource,
    FactStatus,
    MessageDraft,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
)

OTHER = "client-b"
OP_TABLES = {
    "op_actions",
    "op_messages",
    "op_audit",
    "op_facts",
    "op_prospect_states",
    "op_local_prospects",
    "op_local_contacts",
}


def local(pid="inbound-2026-01-15-21622000001", phone="+216 22 000 001", **kw):
    return make_prospect(
        prospect_id=ProspectId(pid),
        display_name="Walk In",
        contact=ContactInfo(phone=phone, preferred_channel=Channel.WHATSAPP),
        **({"status": ProspectStatus.ACTIVE} | kw),
    )


@pytest.fixture
def file_engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'ops.db'}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


def everything(store):
    """One record of every kind, so a restart has something of each to find again."""
    store.save_actions(
        [
            make_action(
                action_id=ActionId("a1"),
                draft=MessageDraft(text="Hello", language="fr", template_params={"k": "v"}),
                next_stage="warm",
                next_action_type=SalesActionType.FOLLOW_UP,
                next_action_due_at=NOW,
            )
        ]
    )
    store.append_messages([make_message()])
    store.append_audit_events(
        [
            AuditEvent(
                timestamp=NOW,
                client_id=CLIENT,
                event_type="action.sent",
                actor="core",
                prospect_id=ProspectId("p-1"),
                action_id=ActionId("a1"),
                details={"why": "test"},
            )
        ]
    )
    store.append_facts(
        ProspectId("p-1"),
        [
            Fact(
                key="budget",
                value="10",
                status=FactStatus.CONFIRMED,
                confidence=0.5,
                evidence=FactEvidence(source=FactSource.PROSPECT, excerpt="ten", observed_at=NOW),
                recorded_at=NOW,
            )
        ],
    )
    store.save_states(
        [
            ProspectState(
                client_id=CLIENT,
                prospect_id=ProspectId("fake_crm:1"),
                status=ProspectStatus.PAUSED,
                stage="s",
                schedule=Scheduling(
                    next_action_type=SalesActionType.FOLLOW_UP,
                    next_action_due_at=NOW,
                    follow_up_count=2,
                ),
                status_reason="asked",
                status_changed_at=NOW,
            )
        ]
    )
    store.create_local_prospects([local()])


class TestPersistence:
    def test_everything_is_found_again_by_a_new_store_on_the_same_database(self, file_engine):
        first = SqlStoreUnderTest(file_engine, CLIENT)
        everything(first)
        before = (
            first.list_actions(),
            first.get_conversation(ProspectId("p-1")),
            first.audit_events,
            first.get_facts(ProspectId("p-1")),
            first.list_states(),
            first.list_local_prospects(),
        )
        file_engine.dispose()  # as after a restart: no connection, no cached state
        second = SqlStoreUnderTest(file_engine, CLIENT)
        after = (
            second.list_actions(),
            second.get_conversation(ProspectId("p-1")),
            second.audit_events,
            second.get_facts(ProspectId("p-1")),
            second.list_states(),
            second.list_local_prospects(),
        )
        assert after == before
        assert all(len(part) for part in after[:1] + after[2:])
        assert second.list_states()[ProspectId("fake_crm:1")].revision == 1

    def test_a_timestamp_keeps_its_instant_and_its_zone(self, file_engine):
        store = SqlStoreUnderTest(file_engine, CLIENT)
        store.save_actions([make_action(due_at=datetime(2026, 3, 1, 10, 30, tzinfo=UTC))])
        assert store.list_actions()[0].due_at == datetime(2026, 3, 1, 10, 30, tzinfo=UTC)
        assert store.list_actions()[0].due_at.tzinfo is not None

    def test_the_first_write_registers_the_client(self, sql_engine):
        SqlStoreUnderTest(sql_engine, "brand-new").save_actions(
            [make_action(client_id="brand-new", idempotency_key="k")]
        )
        with sql_engine.connect() as connection:
            known = connection.execute(text("select client_id from clients")).scalars().all()
        assert "brand-new" in known

    def test_a_read_registers_nothing(self, sql_engine):
        SqlStoreUnderTest(sql_engine, "reader").list_actions()
        with sql_engine.connect() as connection:
            known = connection.execute(text("select client_id from clients")).scalars().all()
        assert "reader" not in known


class TestIsolation:
    def test_two_clients_keep_apart_everything_they_store(self, sql_engine):
        mine, theirs = SqlStoreUnderTest(sql_engine, CLIENT), SqlStoreUnderTest(sql_engine, OTHER)
        everything(mine)
        for name in ("list_actions", "list_states", "list_local_prospects"):
            assert not getattr(theirs, name)(), name
        assert theirs.audit_events == () and theirs.messages == ()
        assert theirs.get_facts(ProspectId("p-1")) == []
        assert theirs.get_conversation(ProspectId("p-1")).messages == ()
        assert theirs.find_action_by_idempotency_key(make_action().idempotency_key) is None
        assert theirs.get_local_prospect(ProspectId("inbound-2026-01-15-21622000001")) is None
        assert theirs.find_local_by_contact(phone="+216 22 000 001") is None

    def test_the_same_ids_can_be_used_by_two_clients(self, sql_engine):
        mine, theirs = SqlStoreUnderTest(sql_engine, CLIENT), SqlStoreUnderTest(sql_engine, OTHER)
        mine.save_actions([make_action()])
        theirs.save_actions([make_action(client_id=OTHER)])  # same action id, same key
        mine.create_local_prospects([local()])
        theirs.create_local_prospects([local(client_id=OTHER)])  # same id, same number
        assert len(mine.list_actions()) == len(theirs.list_actions()) == 1
        assert mine.list_actions()[0].client_id == CLIENT
        assert theirs.list_actions()[0].client_id == OTHER

    def test_a_state_revision_is_per_client(self, sql_engine):
        mine, theirs = SqlStoreUnderTest(sql_engine, CLIENT), SqlStoreUnderTest(sql_engine, OTHER)
        pid = ProspectId("fake_crm:1")
        mine.save_states([ProspectState(client_id=CLIENT, prospect_id=pid)])
        theirs.save_states([ProspectState(client_id=OTHER, prospect_id=pid)])  # revision 0 is fine
        assert theirs.list_states()[pid].revision == 1

    def test_a_client_never_reads_the_rows_another_one_wrote(self, sql_engine):
        mine = SqlStoreUnderTest(sql_engine, CLIENT)
        SqlStoreUnderTest(sql_engine, OTHER).create_local_prospects([local(client_id=OTHER)])
        assert mine.list_local_prospects() == []
        assert mine.list_states() == {}

    def test_every_statement_of_the_rows_layer_is_scoped_to_its_client(self, sql_engine):
        rows = SqlOperationalRows(sql_engine, CLIENT)
        assert rows.client_id == CLIENT
        with pytest.raises(ValueError):
            SqlOperationalRows(sql_engine, "")


class TestWhatTheDatabaseEnforces:
    def test_two_local_prospects_cannot_share_a_contact_even_around_the_store(self, sql_engine):
        SqlStoreUnderTest(sql_engine, CLIENT).create_local_prospects([local()])
        with pytest.raises(IntegrityError), sql_engine.begin() as connection:
            connection.execute(
                insert(op_local_contacts).values(
                    client_id=CLIENT, contact_key="phone:21622000001", prospect_id="inbound-other"
                )
            )

    def test_a_row_cannot_point_at_a_client_that_does_not_exist(self, sql_engine):
        with pytest.raises(IntegrityError), sql_engine.begin() as connection:
            connection.execute(
                insert(op_prospect_states).values(
                    client_id="ghost", prospect_id="x", revision=0, document={}
                )
            )

    def test_a_refused_batch_writes_nothing_anywhere(self, sql_engine):
        store = SqlStoreUnderTest(sql_engine, CLIENT)
        store.create_local_prospects([local()])
        with pytest.raises(ValueError):
            store.create_local_prospects(
                [
                    local("inbound-2026-01-15-21622000002", "+216 22 000 002"),
                    local("inbound-2026-01-15-21622000003", "+216 22 000 001"),  # taken
                ]
            )
        with sql_engine.connect() as connection:
            assert len(connection.execute(select(op_local_prospects)).all()) == 1
            assert len(connection.execute(select(op_local_contacts)).all()) == 1
            assert len(connection.execute(select(op_prospect_states)).all()) == 1
        assert [p.prospect_id for p in store.list_local_prospects()] == [
            "inbound-2026-01-15-21622000001"
        ]
        assert set(store.list_states()) == {ProspectId("inbound-2026-01-15-21622000001")}

    def test_two_writers_with_the_same_read_cannot_both_win_across_connections(self, file_engine):
        pid = ProspectId("fake_crm:1")
        SqlStoreUnderTest(file_engine, CLIENT).save_states(
            [ProspectState(client_id=CLIENT, prospect_id=pid)]
        )
        outcomes: list[str] = []
        barrier = threading.Barrier(4)

        def write(n: int) -> None:
            store = SqlStoreUnderTest(file_engine, CLIENT)
            barrier.wait()
            try:
                store.save_states(
                    [ProspectState(client_id=CLIENT, prospect_id=pid, stage=f"w{n}", revision=1)]
                )
                outcomes.append("won")
            except StateConflictError:
                outcomes.append("lost")
            except StoreUnavailableError:  # SQLite's own writer lock: the write did not happen
                outcomes.append("lost")

        threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert outcomes.count("won") == 1
        assert SqlStoreUnderTest(file_engine, CLIENT).list_states()[pid].revision == 2

    def test_a_fact_sent_twice_by_two_writers_is_recorded_once(self, file_engine):
        fact = Fact(key="k", value="v", status=FactStatus.DISCOVERED, recorded_at=NOW)
        store = SqlStoreUnderTest(file_engine, CLIENT)
        store.append_facts(ProspectId("p"), [fact, fact])
        SqlStoreUnderTest(file_engine, CLIENT).append_facts(ProspectId("p"), [fact])
        assert len(store.get_facts(ProspectId("p"))) == 1


class TestFailuresStaySafe:
    def test_a_database_that_cannot_be_reached_is_a_fixed_sentence(self, tmp_path):
        engine = create_database_engine(f"sqlite:///{tmp_path / 'missing' / 'ops.db'}")
        store = SqlOperationalStore(SqlOperationalRows(engine, CLIENT))
        with pytest.raises(StoreUnavailableError) as caught:
            store.list_actions()
        assert str(caught.value) == "the operational store is unavailable"
        assert "missing" not in str(caught.value) and caught.value.__cause__ is None
        assert store.verify() == ["the operational store is unavailable"]

    def test_a_schema_that_was_never_created_is_reported_by_verify(self):
        engine = create_database_engine("sqlite://")
        store = SqlOperationalStore(SqlOperationalRows(engine, CLIENT))
        assert store.verify() == ["the operational store is unavailable"]

    def test_a_ready_store_verifies_clean(self, sql_engine):
        assert SqlOperationalStore(SqlOperationalRows(sql_engine, CLIENT)).verify() == []

    def test_a_stored_record_that_cannot_be_read_is_reported_without_its_content(self, sql_engine):
        store = SqlStoreUnderTest(sql_engine, CLIENT)
        store.save_actions([make_action()])
        with sql_engine.begin() as connection:
            connection.execute(
                text("update op_actions set document = :doc"), {"doc": '{"secret_value": 1}'}
            )
        with pytest.raises(StoreError) as caught:
            store.list_actions()
        assert str(caught.value) == "a stored record could not be read"
        assert "secret_value" not in str(caught.value)

    def test_a_refusal_never_repeats_an_id_a_number_or_an_address(self, sql_engine):
        store = SqlStoreUnderTest(sql_engine, CLIENT)
        store.create_local_prospects([local()])
        for attempt in (
            lambda: store.create_local_prospects([local()]),
            lambda: store.create_local_prospects(
                [local("inbound-2026-01-15-21622000009", "+216 22 000 001")]
            ),
            lambda: store.append_messages([make_message(), make_message()]),
            lambda: store.save_actions([make_action(client_id=OTHER)]),
        ):
            with pytest.raises(ValueError) as caught:
                attempt()
            message = str(caught.value)
            assert "216" not in message and "inbound" not in message and "p-1" not in message

    def test_the_repr_names_the_client_and_nothing_else(self, sql_engine):
        assert (
            repr(SqlStoreUnderTest(sql_engine, CLIENT)) == f"SqlOperationalStore(client='{CLIENT}')"
        )


class TestMigration:
    def test_revision_0004_creates_exactly_the_operational_tables(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine, "0003")
        before = set(inspect(engine).get_table_names())
        assert not before & OP_TABLES
        upgrade_database(engine, "0004")
        assert current_revision(engine) == "0004"
        assert head_revision() == "0005"  # the write-back ledger comes after them
        assert OP_TABLES <= set(inspect(engine).get_table_names())
        assert OP_TABLES <= set(metadata.tables)

    def test_the_upgrade_keeps_what_was_there(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine, "0003")
        with engine.begin() as connection:
            connection.execute(
                text("insert into clients(client_id, created_at) values ('old', '2026-01-01')")
            )
        upgrade_database(engine)
        with engine.connect() as connection:
            assert connection.execute(text("select client_id from clients")).scalars().all() == [
                "old"
            ]

    def test_every_operational_table_is_tied_to_a_client(self):
        for name in OP_TABLES:
            table = metadata.tables[name]
            assert "client_id" in table.c, name
            targets = {fk.target_fullname for fk in table.c.client_id.foreign_keys}
            assert targets == {"clients.client_id"}, name

    def test_the_downgrade_removes_only_the_operational_tables(self):
        from alembic import command

        from sales_app.persistence.schema import _config

        engine = create_database_engine("sqlite://")
        upgrade_database(engine)
        with engine.begin() as connection:
            config = _config()
            config.attributes["connection"] = connection
            command.downgrade(config, "0003")
        names = set(inspect(engine).get_table_names())
        assert not names & OP_TABLES and "clients" in names and "client_source_check" in names
