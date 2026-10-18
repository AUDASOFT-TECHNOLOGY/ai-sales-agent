"""A client's identity, status and engine in the database; the historical `client.yaml` as the
fallback; filling the records without loss; and putting a status in force without a restart.

SQLite stands in for PostgreSQL. Nothing here touches a real database.
"""

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from app_support import (
    InlineExecutor,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)
from sqlalchemy import text

from sales_app.bootstrap import build_application
from sales_app.client_auth import is_client_active
from sales_app.client_records import DEFAULT_ENGINE, ClientRecord, InMemoryClientRecordStore
from sales_app.client_records.backfill import apply_backfill, plan_backfill
from sales_app.config import load_client_configs, resolve_client
from sales_app.config.client_wiring import known_client_ids, wiring_for
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.client_record_rows import SqlClientRecordStore
from sales_app.persistence.clients import ensure_client
from sales_core.approval import ApprovalMode
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
ENV = env_for("alpha", "beta")


@pytest.fixture
def engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


def tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in root.rglob("*.yaml")}


# ---- the database ---------------------------------------------------------------------------


class TestRecordStore:
    def test_a_client_known_before_the_migration_has_an_empty_record(self, engine):
        with engine.begin() as connection:
            ensure_client(connection, "naga", created_at=NOW, created_by="seed")
        assert SqlClientRecordStore(engine).get("naga") == ClientRecord("naga")

    def test_saving_fills_what_is_given_and_never_erases_the_rest(self, engine):
        store = SqlClientRecordStore(engine)
        store.save(ClientRecord("acme", "Acme", True, DEFAULT_ENGINE), at=NOW, by="admin:root")
        store.save(ClientRecord("acme", enabled=False), at=NOW, by="admin:root")
        assert store.get("acme") == ClientRecord("acme", "Acme", False, DEFAULT_ENGINE)
        assert [r.client_id for r in store.list()] == ["acme"]

    def test_the_new_columns_are_nullable_so_existing_rows_stay_valid(self, engine):
        with engine.connect() as connection:
            columns = connection.execute(text("pragma table_info(clients)")).all()
        nullable = {c[1]: not c[3] for c in columns}
        assert all(nullable[n] for n in ("display_name", "enabled", "engine", "updated_at"))


# ---- the generic engine ---------------------------------------------------------------------


def test_the_generic_engine_is_declared_as_an_installable_brain():
    from sales_brain_kit.generic import BRAIN
    from sales_brain_kit.spec import ConsultativeSalesSpec

    root = Path(__file__).resolve().parents[2]
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    entry = declared["project"]["entry-points"]["revansa.brains"]
    assert entry == {DEFAULT_ENGINE: "sales_brain_kit.generic:BRAIN"}
    assert BRAIN.key == DEFAULT_ENGINE
    assert isinstance(BRAIN.settings_spec, ConsultativeSalesSpec)


# ---- record first, file as the fallback ----------------------------------------------------


class TestWiring:
    def test_without_a_record_the_file_is_read_exactly_as_before(self, tmp_path):
        write_client(tmp_path, "alpha")
        from sales_app.config import read_wiring

        assert wiring_for(tmp_path, "alpha", None) == read_wiring(tmp_path / "alpha")
        assert wiring_for(tmp_path, "alpha", InMemoryClientRecordStore()) == read_wiring(
            tmp_path / "alpha"
        )

    def test_the_record_wins_over_the_file_for_status_and_name(self, tmp_path):
        write_client(tmp_path, "alpha", enabled=True)
        records = InMemoryClientRecordStore([ClientRecord("alpha", "Alpha SARL", False)])
        raw, _ = wiring_for(tmp_path, "alpha", records)
        assert raw["enabled"] is False and raw["display_name"] == "Alpha SARL"
        assert is_client_active(tmp_path, "alpha", records) is False
        assert is_client_active(tmp_path, "alpha") is True  # the file alone, as before

    def test_a_brain_the_file_names_is_never_replaced_by_the_engine(self, tmp_path):
        write_client(tmp_path, "alpha")
        records = InMemoryClientRecordStore([ClientRecord("alpha", engine=DEFAULT_ENGINE)])
        raw, _ = wiring_for(tmp_path, "alpha", records)
        assert raw["brain"]["factory"] == "app_test_brains:good_brain"
        assert "agent" not in raw["brain"]

    def test_a_brain_still_a_placeholder_takes_the_engine_and_keeps_its_settings(self, tmp_path):
        write_client(tmp_path, "alpha", brain={"factory": "TODO", "settings": {"tone": "warm"}})
        records = InMemoryClientRecordStore([ClientRecord("alpha", engine=DEFAULT_ENGINE)])
        raw, _ = wiring_for(tmp_path, "alpha", records)
        assert raw["brain"] == {"agent": DEFAULT_ENGINE, "settings": {"tone": "warm"}}

    def test_a_client_with_a_record_and_no_folder_exists(self, tmp_path):
        records = InMemoryClientRecordStore(
            [ClientRecord("gamma", "Gamma", True, DEFAULT_ENGINE), ClientRecord("half", "Half")]
        )
        raw, problem = wiring_for(tmp_path, "gamma", records)
        assert problem is None
        assert raw["client_id"] == "gamma" and raw["enabled"] is True
        assert raw["brain"] == {"agent": DEFAULT_ENGINE, "settings": {}}
        assert not (tmp_path / "gamma").exists()  # nothing was written to disk
        assert known_client_ids(tmp_path, records) == ["gamma"]  # "half" has no status yet

    def test_such_a_client_is_loaded_and_waits_for_its_sheet_and_model(self, tmp_path):
        write_client(tmp_path, "alpha")
        records = InMemoryClientRecordStore([ClientRecord("gamma", "Gamma", True, DEFAULT_ENGINE)])
        loaded = load_client_configs(
            tmp_path, env=ENV, mode_ceiling=ApprovalMode.APPROVAL, records=records
        )
        assert [c.client_id for c in loaded.clients] == ["alpha"]
        assert "gamma" in loaded.disabled  # placeholders: awaiting configuration, not an error
        assert loaded.errors == ()
        resolution = resolve_client(
            tmp_path / "gamma", records=records, env=ENV, mode_ceiling=ApprovalMode.APPROVAL
        )
        assert resolution.disabled and not resolution.errors

    def test_a_record_that_disables_a_file_client_leaves_it_out(self, tmp_path):
        write_client(tmp_path, "alpha")
        write_client(tmp_path, "beta")
        records = InMemoryClientRecordStore([ClientRecord("beta", enabled=False)])
        loaded = load_client_configs(
            tmp_path, env=ENV, mode_ceiling=ApprovalMode.APPROVAL, records=records
        )
        assert [c.client_id for c in loaded.clients] == ["alpha"]
        assert loaded.disabled == ("beta",)


# ---- filling the records from the files ------------------------------------------------------


class TestBackfill:
    def test_the_report_changes_nothing_and_the_apply_is_exact_and_idempotent(self, tmp_path):
        write_client(tmp_path, "alpha")
        write_client(tmp_path, "beta", enabled=False)
        write_client(tmp_path, "_template", enabled=False)
        before = tree(tmp_path)
        records = InMemoryClientRecordStore()

        report = plan_backfill(tmp_path, records)
        assert records.list() == []  # a report writes nothing
        assert {(c.client_id, c.field, c.new) for c in report.fills()} == {
            ("alpha", "enabled", True),
            ("alpha", "display_name", "Alpha"),
            ("beta", "enabled", False),
            ("beta", "display_name", "Beta"),
        }

        assert apply_backfill(records, report, at=NOW) == 4
        assert records.get("beta") == ClientRecord("beta", "Beta", False, None)  # engine untouched
        again = plan_backfill(tmp_path, records)
        assert again.fills() == [] and apply_backfill(records, again, at=NOW) == 0
        assert tree(tmp_path) == before  # the files were never modified

    def test_a_value_chosen_in_the_database_is_reported_and_kept(self, tmp_path):
        write_client(tmp_path, "alpha", enabled=True)
        records = InMemoryClientRecordStore([ClientRecord("alpha", "Alpha Pro", False)])
        report = plan_backfill(tmp_path, records)
        conflicts = {c.field: c for c in report.changes if c.action == "conflict"}
        assert set(conflicts) == {"enabled", "display_name"}
        assert apply_backfill(records, report, at=NOW) == 0
        assert records.get("alpha") == ClientRecord("alpha", "Alpha Pro", False)

    def test_a_legacy_brain_binding_is_reported_as_kept(self, tmp_path):
        write_client(tmp_path, "alpha")
        report = plan_backfill(tmp_path, InMemoryClientRecordStore())
        engine = [c for c in report.changes if c.field == "engine"]
        assert len(engine) == 1 and "app_test_brains:good_brain" in engine[0].note

    def test_an_unreadable_file_is_a_problem_not_a_crash(self, tmp_path):
        write_client(tmp_path, "alpha", raw="- not\n- a mapping\n")
        report = plan_backfill(tmp_path, InMemoryClientRecordStore())
        assert report.problems and report.changes == []


# ---- the application: status from the database, put in force without a restart ----------------


def boot(tmp_path, engine):
    write_client(tmp_path, "alpha")
    write_client(tmp_path, "beta", enabled=False)
    builders, _made = fake_builders(FakeClock(NOW))
    return build_application(
        make_settings(tmp_path, database_url=str(engine.url)),
        env=ENV,
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
        refresh_executor=InlineExecutor(),
    )


class TestApplication:
    def test_a_database_status_puts_a_client_in_force_without_a_restart(self, tmp_path, engine):
        application = boot(tmp_path, engine)
        try:
            assert application.registry.get("beta") is None
            assert application.client_enabled("beta") is False
            # The decision is made in the database only: the file still says `enabled: false`.
            application.client_records.set_enabled("beta", True, at=NOW, by="admin:root")
            assert application.client_enabled("beta") is True
            assert (
                yaml.safe_load((tmp_path / "beta" / "client.yaml").read_text())["enabled"] is False
            )

            application.runtime_manager.refresh_client("beta").result(timeout=10)

            assert application.registry.get("beta") is not None
            assert application.runtime_manager.status("beta").state == "applied"
            assert "beta" not in [
                e["client_id"]
                for e in application.registry.health()
                if e.get("status") == "disabled"
            ]
        finally:
            application.close()

    def test_the_admin_switch_writes_the_database_and_keeps_the_file_in_step(
        self, tmp_path, engine
    ):
        application = boot(tmp_path, engine)
        try:
            application.set_client_enabled("beta", True, by="admin:root")
            assert application.client_records.get("beta").enabled is True
            assert (
                yaml.safe_load((tmp_path / "beta" / "client.yaml").read_text())["enabled"] is True
            )
            application.set_client_enabled("beta", False, by="admin:root")
            assert application.client_records.get("beta").enabled is False
            assert (
                yaml.safe_load((tmp_path / "beta" / "client.yaml").read_text())["enabled"] is False
            )
        finally:
            application.close()

    def test_a_client_created_without_a_folder_is_seen_by_the_application(self, tmp_path, engine):
        application = boot(tmp_path, engine)
        try:
            application.client_records.save(
                ClientRecord("gamma", "Gamma", True, DEFAULT_ENGINE), at=NOW, by="admin:root"
            )
            raw, problem = application.client_wiring("gamma")
            assert problem is None and raw["brain"]["agent"] == DEFAULT_ENGINE
            assert application.client_enabled("gamma") is True
            assert not (tmp_path / "gamma").exists()
            application.runtime_manager.refresh_client("gamma").result(timeout=10)
            assert application.runtime_manager.status("gamma").state == "idle"  # awaiting its Sheet
        finally:
            application.close()


class TestDbClientsCommand:
    def run(self, tmp_path, engine, *, apply: bool):
        import argparse

        from sales_app.db_cli import run_db

        settings = make_settings(tmp_path, database_url=str(engine.url))
        return run_db(settings, argparse.Namespace(db_command="clients", apply=apply))

    def test_it_reports_first_and_writes_only_when_asked(self, tmp_path, engine, capsys):
        write_client(tmp_path, "alpha")
        with engine.begin() as connection:
            ensure_client(connection, "alpha", created_at=NOW, created_by="seed")
        assert self.run(tmp_path, engine, apply=False) == 0
        assert "report only: 2 field(s) would be filled" in capsys.readouterr().out
        assert SqlClientRecordStore(engine).get("alpha") == ClientRecord("alpha")

        assert self.run(tmp_path, engine, apply=True) == 0
        assert SqlClientRecordStore(engine).get("alpha") == ClientRecord("alpha", "Alpha", True)
        assert self.run(tmp_path, engine, apply=True) == 0  # idempotent
        assert "0 field(s) filled" in capsys.readouterr().out
