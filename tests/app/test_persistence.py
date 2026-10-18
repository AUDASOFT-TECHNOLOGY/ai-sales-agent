"""SQL persistence beyond the shared port contracts: the migrations, what only a database can
guarantee (foreign keys, uniqueness, append-only history, concurrency), the file-to-database import,
and the application running on the database end to end.

SQLite stands in for PostgreSQL in these tests; nothing asserted here depends on it.
"""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app_support import (
    InlineExecutor,
    declare_models,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from sales_app.admin_auth import AdminRole, AdminUser, FileAdminUserStore
from sales_app.admin_auth.passwords import hash_password
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.client_profiles import ClientProfile, FileClientProfileStore
from sales_app.config_store import ConfigConflictError, ConfigCorruptError, FileConfigStore
from sales_app.errors import ConfigurationError
from sales_app.persistence import (
    SchemaNotReadyError,
    SqlAdminUserStore,
    SqlClientProfileStore,
    SqlConfigStore,
    create_database_engine,
    current_revision,
    ensure_schema_current,
    head_revision,
    import_file_data,
    seed_clients,
    upgrade_database,
)
from sales_app.persistence.tables import (
    admin_user_clients,
    client_profiles,
    clients,
    config_versions,
)
from sales_app.secret_store import generate_master_key
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"
DOC = {"display_name": "Alpha", "daily_action_limit": 20}


def account(username="alice", client_ids=frozenset({"alpha"}), role=AdminRole.EDITOR):
    return AdminUser(
        id=f"id-{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
        role=role,
        client_ids=client_ids,
        created_at=NOW,
    )


def profile(client_id="acme", login="acme.admin", **kw):
    return ClientProfile(
        client_id=client_id, login=login, password_hash="$2b$hash", created_at=NOW, **kw
    )


@pytest.fixture
def file_engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


# ---- migrations -----------------------------------------------------------------------------


class TestMigrations:
    def test_an_empty_database_is_not_ready_and_says_how_to_fix_it(self):
        engine = create_database_engine("sqlite://")
        with pytest.raises(SchemaNotReadyError, match="sales-agent db upgrade"):
            ensure_schema_current(engine)

    def test_upgrade_reaches_the_latest_revision_and_is_repeatable(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine)
        upgrade_database(engine)
        assert current_revision(engine) == head_revision()
        ensure_schema_current(engine)

    def test_the_migration_creates_exactly_the_modelled_tables(self, sql_engine):
        from sales_app.persistence.tables import metadata

        with sql_engine.connect() as connection:
            rows = connection.execute(
                text("select name from sqlite_master where type = 'table'")
            ).all()
        created = {r[0] for r in rows} - {"alembic_version"}
        assert created == set(metadata.tables)

    def test_postgresql_urls_use_the_installed_psycopg_driver(self):
        from sales_app.persistence.database import normalize_url

        assert normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
        assert normalize_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
        assert normalize_url("sqlite://") == "sqlite://"


# ---- what only the database can guarantee -------------------------------------------------------


class TestIntegrity:
    def test_an_admin_scoped_to_a_client_that_does_not_exist_is_refused(self, sql_engine):
        store = SqlAdminUserStore(sql_engine)
        with pytest.raises(ValueError, match="unknown client"):
            store.create(account(client_ids=frozenset({"alpha", "ghost"})))
        assert store.list() == ()  # nothing half-written

    def test_updating_a_scope_to_an_unknown_client_is_refused_and_keeps_the_old_scope(
        self, sql_engine
    ):
        store = SqlAdminUserStore(sql_engine)
        store.create(account())
        with pytest.raises(ValueError, match="unknown client"):
            store.update(account(client_ids=frozenset({"ghost"})))
        assert store.get("id-alice").client_ids == frozenset({"alpha"})

    def test_the_foreign_key_itself_refuses_an_unknown_client(self, sql_engine):
        """Not only the store's pre-check: the database enforces it."""
        SqlAdminUserStore(sql_engine).create(account())
        with pytest.raises(IntegrityError), sql_engine.begin() as connection:
            connection.execute(
                admin_user_clients.insert().values(admin_id="id-alice", client_id="ghost")
            )

    def test_a_scope_survives_a_new_store_instance(self, sql_engine):
        SqlAdminUserStore(sql_engine).create(account(client_ids=frozenset({"alpha", "beta"})))
        assert SqlAdminUserStore(sql_engine).get("id-alice").client_ids == {"alpha", "beta"}

    def test_a_whatsapp_number_is_unique_whatever_its_formatting(self, sql_engine):
        store = SqlClientProfileStore(sql_engine)
        store.create(profile("acme", "acme.login", whatsapp_number="+216 22 768 307"))
        with pytest.raises(ValueError, match="WhatsApp number"):
            store.create(profile("globex", "globex.login", whatsapp_number="21622768307"))

    def test_the_database_itself_refuses_a_duplicate_login_or_number(self, sql_engine):
        store = SqlClientProfileStore(sql_engine)
        store.create(profile("acme", "acme.login", whatsapp_number="+216 22 768 307"))
        store.create(profile("globex", "globex.login"))
        with pytest.raises(IntegrityError), sql_engine.begin() as connection:
            connection.execute(
                client_profiles.update()
                .where(client_profiles.c.client_id == "globex")
                .values(login="acme.login")
            )
        with pytest.raises(IntegrityError), sql_engine.begin() as connection:
            connection.execute(
                client_profiles.update()
                .where(client_profiles.c.client_id == "globex")
                .values(whatsapp_digits="21622768307")
            )

    def test_a_refused_profile_leaves_no_client_behind(self, sql_engine):
        store = SqlClientProfileStore(sql_engine)
        store.create(profile("acme", "acme.login"))
        with pytest.raises(ValueError):
            store.create(profile("globex", "acme.login"))  # login taken
        with sql_engine.connect() as connection:
            ids = {
                r[0]
                for r in connection.execute(clients.select().with_only_columns(clients.c.client_id))
            }
        assert "globex" not in ids  # the client and its profile appear together or not at all

    def test_two_profiles_without_a_whatsapp_number_do_not_collide(self, sql_engine):
        store = SqlClientProfileStore(sql_engine)
        store.create(profile("acme", "acme.login"))
        store.create(profile("globex", "globex.login"))
        assert len(store.list()) == 2

    def test_an_expired_or_deleted_account_takes_its_sessions_with_it(self, sql_engine):
        from sales_app.admin_auth import AdminSession
        from sales_app.persistence import SqlAdminSessionStore
        from sales_app.persistence.tables import admin_users

        SqlAdminUserStore(sql_engine).create(account())
        sessions = SqlAdminSessionStore(sql_engine)
        sessions.create(
            AdminSession(
                token_hash="a" * 64,
                admin_id="id-alice",
                created_at=NOW,
                expires_at=datetime(2026, 5, 2, tzinfo=UTC),
            )
        )
        with sql_engine.begin() as connection:
            connection.execute(admin_users.delete().where(admin_users.c.id == "id-alice"))
        assert sessions.get("a" * 64) is None

    def test_a_session_for_an_unknown_owner_is_refused(self, sql_engine):
        from sales_app.admin_auth import AdminSession
        from sales_app.persistence import SqlClientSessionStore

        with pytest.raises(IntegrityError):
            SqlClientSessionStore(sql_engine).create(
                AdminSession(
                    token_hash="b" * 64,
                    admin_id="ghost",
                    created_at=NOW,
                    expires_at=datetime(2026, 5, 2, tzinfo=UTC),
                )
            )

    def test_timestamps_come_back_timezone_aware_and_equal(self, sql_engine):
        SqlAdminUserStore(sql_engine).create(account())
        stored = SqlAdminUserStore(sql_engine).get("id-alice")
        assert stored.created_at == NOW and stored.created_at.tzinfo is not None


class TestConfigHistory:
    def test_a_published_version_cannot_be_changed_or_removed(self, sql_engine):
        store = SqlConfigStore(sql_engine)
        store.publish("alpha", DOC, author="t", expected_parent=None)
        with pytest.raises(DBAPIError, match="append-only"), sql_engine.begin() as connection:
            connection.execute(config_versions.update().values(note="rewritten"))
        with pytest.raises(DBAPIError, match="append-only"), sql_engine.begin() as connection:
            connection.execute(config_versions.delete())
        assert store.current("alpha").note == ""

    def test_a_document_edited_behind_the_stores_back_is_reported_as_corrupt(self, sql_engine):
        store = SqlConfigStore(sql_engine)
        store.publish("alpha", DOC, author="t", expected_parent=None)
        with sql_engine.begin() as connection:  # bypass the trigger, as a hand edit would
            connection.execute(text("drop trigger config_versions_no_update"))
            connection.execute(
                config_versions.update().values(document={"display_name": "Tampered"})
            )
        with pytest.raises(ConfigCorruptError, match="checksum"):
            store.current("alpha")
        with pytest.raises(ConfigCorruptError):  # and nothing is appended to it
            store.publish("alpha", DOC, author="t", expected_parent=1)

    def test_the_first_publish_registers_the_client(self, sql_engine):
        SqlConfigStore(sql_engine).publish("newcomer", DOC, author="t", expected_parent=None)
        with sql_engine.connect() as connection:
            assert connection.execute(
                clients.select().where(clients.c.client_id == "newcomer")
            ).first()

    def test_histories_of_two_clients_never_mix(self, sql_engine):
        store = SqlConfigStore(sql_engine)
        store.publish("alpha", {"display_name": "A"}, author="t", expected_parent=None)
        store.publish("beta", {"display_name": "B"}, author="t", expected_parent=None)
        store.publish("beta", {"display_name": "B2"}, author="t", expected_parent=1)
        assert [v.document["display_name"] for v in store.history("alpha")] == ["A"]
        assert [v.document["display_name"] for v in store.history("beta")] == ["B", "B2"]

    def test_concurrent_publishers_cannot_both_win(self, file_engine):
        store = SqlConfigStore(file_engine)
        store.publish("alpha", DOC, author="t", expected_parent=None)
        outcomes: list[str] = []
        barrier = threading.Barrier(8)

        def publish(n: int) -> None:
            barrier.wait()
            try:
                store.publish("alpha", {**DOC, "n": n}, author=f"t{n}", expected_parent=1)
                outcomes.append("won")
            except ConfigConflictError:
                outcomes.append("conflict")

        threads = [threading.Thread(target=publish, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert outcomes.count("won") == 1 and outcomes.count("conflict") == 7
        assert [v.version for v in store.history("alpha")] == [1, 2]


# ---- the file -> database import ---------------------------------------------------------------


class Source:
    """A set of file-backed stores, as a deployment running on files has them."""

    def __init__(self, root: Path) -> None:
        self.clients_dir = root
        self.admin_dir = root / "_admin"
        self.profiles_dir = root / "_profiles"
        self.config_dir = root / "_config"
        for client_id in ("alpha", "beta"):
            write_client(root, client_id)
        FileAdminUserStore(self.admin_dir / "users.json").create(
            account("root", frozenset(), AdminRole.OWNER)
        )
        FileAdminUserStore(self.admin_dir / "users.json").create(
            account("alice", frozenset({"alpha", "beta"}))
        )
        profiles = FileClientProfileStore(self.profiles_dir / "profiles.json")
        profiles.create(
            profile("alpha", "alpha.login", whatsapp_number="+216 22 768 307", logo="data:x")
        )
        profiles.create(profile("gamma", "gamma.login"))  # a client with a profile but no directory
        config = FileConfigStore(self.config_dir, now=lambda: NOW)
        config.publish("alpha", DOC, author="t", note="first", expected_parent=None)
        config.publish("alpha", {**DOC, "daily_action_limit": 30}, author="t", expected_parent=1)

    def run(self, engine, **kw):
        return import_file_data(
            engine,
            clients_dir=self.clients_dir,
            admin_dir=self.admin_dir,
            profiles_dir=self.profiles_dir,
            config_dir=self.config_dir,
            now=NOW,
            **kw,
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p): p.read_bytes()
            for base in (self.admin_dir, self.profiles_dir, self.config_dir)
            for p in base.rglob("*")
            if p.is_file()
        }


@pytest.fixture
def empty_engine():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    yield engine
    engine.dispose()


def rows(engine, table) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"select count(*) from {table}")).scalar()


class TestImport:
    def test_everything_is_copied_and_reads_back_identically(self, tmp_path, empty_engine):
        source = Source(tmp_path)
        report = source.run(empty_engine)
        assert report.ok and report.committed, report.errors
        assert report.counts["clients"] == {"source": 3, "inserted": 3, "unchanged": 0}
        assert report.counts["admin_users"]["inserted"] == 2
        assert report.counts["client_profiles"]["inserted"] == 2
        assert report.counts["config_versions"]["inserted"] == 2

        users = FileAdminUserStore(source.admin_dir / "users.json").list()
        assert SqlAdminUserStore(empty_engine).list() == users
        assert (
            SqlClientProfileStore(empty_engine).list()
            == FileClientProfileStore(source.profiles_dir / "profiles.json").list()
        )
        assert SqlConfigStore(empty_engine).history("alpha") == FileConfigStore(
            source.config_dir
        ).history("alpha")

    def test_source_files_are_never_modified_or_removed(self, tmp_path, empty_engine):
        source = Source(tmp_path)
        before = source.snapshot()
        source.run(empty_engine)
        source.run(empty_engine)
        assert source.snapshot() == before

    def test_importing_twice_changes_nothing(self, tmp_path, empty_engine):
        source = Source(tmp_path)
        source.run(empty_engine)
        counts = {t: rows(empty_engine, t) for t in ("clients", "admin_users", "config_versions")}
        again = source.run(empty_engine)
        assert again.ok
        assert all(c["inserted"] == 0 for c in again.counts.values())
        assert again.counts["config_versions"]["unchanged"] == 2
        assert counts == {t: rows(empty_engine, t) for t in counts}

    def test_a_dry_run_writes_nothing_and_a_later_dry_run_verifies_the_import(
        self, tmp_path, empty_engine
    ):
        source = Source(tmp_path)
        preview = source.run(empty_engine, dry_run=True)
        assert preview.ok and not preview.committed
        assert preview.counts["admin_users"]["inserted"] == 2
        assert rows(empty_engine, "admin_users") == 0 and rows(empty_engine, "clients") == 0
        source.run(empty_engine)
        verified = source.run(empty_engine, dry_run=True)
        assert verified.ok and all(c["inserted"] == 0 for c in verified.counts.values())

    def test_sessions_are_not_imported(self, tmp_path, empty_engine):
        source = Source(tmp_path)
        (source.admin_dir / "sessions").mkdir(parents=True)
        (source.admin_dir / "sessions" / f"{'a' * 64}.json").write_text("{}", encoding="utf-8")
        assert source.run(empty_engine).ok
        assert rows(empty_engine, "admin_sessions") == 0

    def test_duplicate_whatsapp_numbers_are_reported_and_nothing_is_written(
        self, tmp_path, empty_engine
    ):
        source = Source(tmp_path)
        path = source.profiles_dir / "profiles.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["profiles"][1]["whatsapp_number"] = "21622768307"  # same digits as alpha's number
        path.write_text(json.dumps(data), encoding="utf-8")
        report = source.run(empty_engine)
        assert not report.ok and any("WhatsApp number" in e for e in report.errors)
        assert rows(empty_engine, "clients") == 0 and rows(empty_engine, "admin_users") == 0

    def test_duplicate_logins_are_reported(self, tmp_path, empty_engine):
        source = Source(tmp_path)
        path = source.profiles_dir / "profiles.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["profiles"][1]["login"] = "alpha.login"
        path.write_text(json.dumps(data), encoding="utf-8")
        report = source.run(empty_engine)
        assert not report.ok and any("login" in e for e in report.errors)

    def test_a_scope_pointing_at_an_unknown_client_is_reported_not_dropped(
        self, tmp_path, empty_engine
    ):
        source = Source(tmp_path)
        FileAdminUserStore(source.admin_dir / "users.json").create(
            account("bob", frozenset({"ghost"}))
        )
        report = source.run(empty_engine)
        assert not report.ok and any("ghost" in e for e in report.errors)
        assert rows(empty_engine, "admin_users") == 0

    def test_a_settings_file_edited_after_publishing_is_reported(self, tmp_path, empty_engine):
        source = Source(tmp_path)
        path = source.config_dir / "alpha" / "versions" / "000001.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["document"]["daily_action_limit"] = 999
        path.write_text(json.dumps(envelope), encoding="utf-8")
        report = source.run(empty_engine)
        assert not report.ok and any("checksum" in e for e in report.errors)
        assert rows(empty_engine, "config_versions") == 0

    def test_a_record_that_differs_in_the_database_is_a_conflict_never_overwritten(
        self, tmp_path, empty_engine
    ):
        source = Source(tmp_path)
        source.run(empty_engine)
        store = SqlAdminUserStore(empty_engine)
        from dataclasses import replace

        store.update(replace(store.get("id-alice"), active=False))
        report = source.run(empty_engine)
        assert not report.ok and any("conflict" in e for e in report.errors)
        assert store.get("id-alice").active is False  # left exactly as found

    def test_an_import_that_fails_midway_rolls_everything_back(self, tmp_path, empty_engine):
        source = Source(tmp_path)
        # A profile whose login is already taken in the database by another client.
        SqlClientProfileStore(empty_engine).create(profile("zeta", "alpha.login"))
        report = source.run(empty_engine)
        assert not report.ok
        assert rows(empty_engine, "admin_users") == 0 and rows(empty_engine, "config_versions") == 0

    def test_seeding_registers_every_client_directory_once(self, tmp_path, empty_engine):
        write_client(tmp_path, "alpha")
        write_client(tmp_path, "_template", enabled=False)
        assert seed_clients(empty_engine, tmp_path, now=NOW) == ["alpha"]
        assert seed_clients(empty_engine, tmp_path, now=NOW) == []


# ---- the application on the database, end to end -----------------------------------------------


def database_settings(tmp_path):
    url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    return url


def boot(tmp_path, *client_ids, **settings):
    for cid in client_ids:
        write_client(tmp_path, cid)
    builders, _made = fake_builders(FakeClock(NOW))
    return build_application(
        make_settings(tmp_path, database_url=database_settings(tmp_path), **settings),
        env=env_for(*client_ids),
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
    )


class TestApplicationOnTheDatabase:
    def test_startup_refuses_a_database_without_schema_and_says_what_to_run(self, tmp_path):
        with pytest.raises(ConfigurationError, match="sales-agent db upgrade"):
            boot(tmp_path, "alpha")

    def test_startup_registers_the_client_directories(self, tmp_path):
        engine = create_database_engine(database_settings(tmp_path))
        upgrade_database(engine)
        engine.dispose()
        application = boot(tmp_path, "alpha", "beta")
        try:
            assert application.account_stores.engine is not None
            assert rows(application.account_stores.engine, "clients") == 2
        finally:
            application.close()

    def test_without_a_database_url_the_files_remain_the_fallback(self, tmp_path):
        write_client(tmp_path, "alpha")
        builders, _made = fake_builders(FakeClock(NOW))
        application = build_application(
            make_settings(
                tmp_path,
                admin_dir=tmp_path / "_admin",
                profiles_dir=tmp_path / "_profiles",
                config_dir=tmp_path / "_config",
            ),
            env=env_for("alpha"),
            builders=builders,
            clock=FakeClock(NOW),
            executor=InlineExecutor(),
        )
        try:
            assert application.account_stores.engine is None
        finally:
            application.close()

    def test_client_creation_login_and_uniqueness_through_the_api(self, tmp_path):
        engine = create_database_engine(database_settings(tmp_path))
        upgrade_database(engine)
        engine.dispose()
        # A model is assigned only if Settings holds it, which needs the credential store.
        application = boot(
            tmp_path,
            "alpha",
            secrets_dir=tmp_path / "_secrets",
            secrets_key=generate_master_key(),
        )
        declare_models(application.credentials, "gpt-4o")
        try:
            application.account_stores.admin_users.create(
                account("root", frozenset(), AdminRole.OWNER)
            )
            http = TestClient(
                create_app(application, run_scheduler=False), base_url="https://testserver"
            )
            assert (
                http.post(
                    "/admin/v1/auth/login", json={"username": "root", "password": PASSWORD}
                ).status_code
                == 200
            )
            body = {
                "display_name": "Acme Corp",
                "login": "Acme.Admin",
                "temporary_password": "temp-pass-123",
                "llm_provider": "openai",
                "llm_model": "gpt-4o",
                "status": "inactive",
                "whatsapp_number": "+216 22 768 307",
            }
            created = http.post("/admin/v1/clients", json=body)
            assert created.status_code == 201
            client_id = created.json()["client_id"]
            same_login = http.post(
                "/admin/v1/clients", json=body | {"whatsapp_number": "+216 99 697 335"}
            )
            same_number = http.post("/admin/v1/clients", json=body | {"login": "other.login"})
            assert (same_login.status_code, same_number.status_code) == (409, 409)
            # The refused attempts left no row behind.
            assert rows(application.account_stores.engine, "client_profiles") == 1
            # A new client lives in the database alone: no folder, no `client.yaml`.
            assert sorted(p.name for p in tmp_path.iterdir() if (p / "client.yaml").exists()) == [
                "alpha"
            ]

            # The new client signs in on its own, separate session space.
            other = TestClient(
                create_app(application, run_scheduler=False), base_url="https://testserver"
            )
            assert client_id == "acme-corp"
            login = other.post(
                "/client/v1/auth/login", json={"login": "acme.admin", "password": "temp-pass-123"}
            )
            assert login.status_code == 403  # created inactive: right password, inactive account
            assert other.get("/admin/v1/clients").status_code == 401  # no Admin access
        finally:
            application.close()
