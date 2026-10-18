"""Credentials in the database: encrypted, isolated per client, used by a client that has no file.

SQLite stands in for PostgreSQL. The store's contract (get, set, unique, delete, entries) is checked
with the other stores in `test_secret_store.py`; this file covers what only the database adds.
"""

import argparse
from datetime import UTC, datetime

import pytest
import yaml
from app_support import (
    InlineExecutor,
    client_yaml,
    declare_models,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)
from kit_support import minimal
from sqlalchemy import text
from test_admin_clients_api import PASSWORD, owner
from test_client_agent_api import LOGIN, mark_source_tested
from test_client_without_folder import TEMP_PASSWORD, browser

from sales_app.api.admin_clients import publish_client_settings
from sales_app.bootstrap import build_application
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
from sales_app.db_cli import run_db
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.secret_rows import SqlSecretAudit, SqlSecretStore
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    AesGcmCipher,
    DuplicateSecretError,
    FileSecretStore,
    SecretAuditEvent,
    SecretDecryptError,
    client_scope,
    generate_master_key,
)
from sales_app.secret_wiring import open_secrets
from sales_app.secrets_cli import run_secrets
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
MARKER = "EAAG-plaintext-marker-789"


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    yield url, engine
    engine.dispose()


def settings_for(tmp_path, url, key=None, **kw):
    return make_settings(
        tmp_path / "clients",
        database_url=url,
        secrets_dir=tmp_path / "s",
        **({"secrets_key": key} if key else {}) | kw,
    )


class TestWhatTheDatabaseHolds:
    def test_only_ciphertext_is_stored_and_the_audit_never_has_a_value(self, db):
        _, engine = db
        store = SqlSecretStore(engine, AesGcmCipher.from_text(generate_master_key()))
        store.set(client_scope("acme"), WHATSAPP_ACCESS_TOKEN, MARKER, by="root")
        SqlSecretAudit(engine).record(
            SecretAuditEvent(
                NOW, "root", "set", client_scope("acme"), WHATSAPP_ACCESS_TOKEN, "stored"
            )
        )
        with engine.connect() as c:
            dump = " ".join(
                str(v)
                for table in ("secrets", "secret_audit")
                for row in c.execute(text(f"select * from {table}")).all()
                for v in row
            )
        assert MARKER not in dump and "acme" in dump

    def test_a_row_copied_to_another_client_cannot_be_opened(self, db):
        _, engine = db
        store = SqlSecretStore(engine, AesGcmCipher.from_text(generate_master_key()))
        store.set(client_scope("a"), WHATSAPP_ACCESS_TOKEN, MARKER, by="root")
        with engine.begin() as c:
            c.execute(
                text("update secrets set scope = :s where scope = :o"),
                {"s": client_scope("b"), "o": client_scope("a")},
            )
        with pytest.raises(SecretDecryptError):
            store.get(client_scope("b"), WHATSAPP_ACCESS_TOKEN)

    def test_a_phone_number_cannot_belong_to_two_clients(self, db):
        _, engine = db
        store = SqlSecretStore(engine, AesGcmCipher.from_text(generate_master_key()))
        store.set(client_scope("a"), WHATSAPP_PHONE_NUMBER_ID, "123", by="root", unique=True)
        with pytest.raises(DuplicateSecretError):
            store.set(client_scope("b"), WHATSAPP_PHONE_NUMBER_ID, "123", by="root", unique=True)

    def test_the_wrong_master_key_is_refused_at_startup(self, db, tmp_path):
        url, engine = db
        SqlSecretStore(engine, AesGcmCipher.from_text(generate_master_key())).set(
            GLOBAL_SCOPE, OPENAI_API_KEY, MARKER, by="root"
        )
        from sales_app.secret_store import InvalidMasterKeyError

        with pytest.raises(InvalidMasterKeyError):
            open_secrets(settings_for(tmp_path, url, generate_master_key()), engine)


class TestChoosingTheStore:
    def test_with_a_database_and_a_key_credentials_live_in_the_database(self, db, tmp_path):
        url, engine = db
        store, audit = open_secrets(settings_for(tmp_path, url, generate_master_key()), engine)
        assert isinstance(store, SqlSecretStore) and isinstance(audit, SqlSecretAudit)

    def test_a_leftover_file_is_never_ignored_silently(self, db, tmp_path):
        url, engine = db
        key = generate_master_key()
        FileSecretStore(tmp_path / "s" / "secrets.json", AesGcmCipher.from_text(key)).set(
            GLOBAL_SCOPE, OPENAI_API_KEY, MARKER, by="root"
        )
        with pytest.raises(Exception, match="migrate-to-db"):
            open_secrets(settings_for(tmp_path, url, key), engine)

    def test_without_a_database_the_file_remains(self, tmp_path):
        from sales_app.secret_store import UnavailableSecretStore

        settings = make_settings(tmp_path / "clients", secrets_dir=tmp_path / "s")
        store, _ = open_secrets(settings, None)
        assert isinstance(store, UnavailableSecretStore)


class TestMovingCredentialsIntoTheDatabase:
    def test_the_file_is_copied_report_first_and_repeatably(self, db, tmp_path, capsys):
        url, engine = db
        key = generate_master_key()
        source = FileSecretStore(tmp_path / "s" / "secrets.json", AesGcmCipher.from_text(key))
        source.set(GLOBAL_SCOPE, OPENAI_API_KEY, MARKER, by="root")
        source.set(client_scope("acme"), WHATSAPP_ACCESS_TOKEN, "tok", by="root")
        settings = settings_for(tmp_path, url, key)
        args = lambda apply: argparse.Namespace(secrets_command="migrate-to-db", apply=apply)

        assert run_secrets(args(False), settings) == 0
        assert SqlSecretStore(engine, AesGcmCipher.from_text(key)).count() == 0  # report only
        assert run_secrets(args(True), settings) == 0
        copy = SqlSecretStore(engine, AesGcmCipher.from_text(key))
        assert copy.get(GLOBAL_SCOPE, OPENAI_API_KEY) == MARKER
        assert copy.get(client_scope("acme"), WHATSAPP_ACCESS_TOKEN) == "tok"
        assert run_secrets(args(True), settings) == 0  # nothing new
        out = capsys.readouterr().out
        assert MARKER not in out and "0 to copy, 2 already identical" in out
        assert (tmp_path / "s" / "secrets.json").exists()  # the file is never removed

    def test_a_legacy_clients_environment_credentials_are_stored_in_its_own_scope(
        self, db, tmp_path, capsys, monkeypatch
    ):
        url, engine = db
        key = generate_master_key()
        write_client(tmp_path / "clients", "alpha")  # names ALPHA_TOKEN / ALPHA_PHONE_ID / ...
        for name, value in env_for("alpha").items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("ALPHA_PHONE_ID", "5550101")  # Meta's ids are numeric
        settings = settings_for(tmp_path, url, key)
        args = lambda apply: argparse.Namespace(
            secrets_command="import-env", client="alpha", apply=apply
        )
        assert run_secrets(args(False), settings) == 0
        assert SqlSecretStore(engine, AesGcmCipher.from_text(key)).count() == 0
        assert run_secrets(args(True), settings) == 0
        store = SqlSecretStore(engine, AesGcmCipher.from_text(key))
        assert store.get(client_scope("alpha"), WHATSAPP_ACCESS_TOKEN) == "token-alpha"
        assert store.get(client_scope("alpha"), WHATSAPP_PHONE_NUMBER_ID) == "5550101"
        assert store.get(GLOBAL_SCOPE, WHATSAPP_APP_SECRET) == "secret-alpha"
        assert store.get(client_scope("other"), WHATSAPP_ACCESS_TOKEN) is None  # isolated
        assert "token-alpha" not in capsys.readouterr().out
        assert run_secrets(args(True), settings) == 0  # already stored: kept


class TestLegacyCheck:
    def test_a_client_is_safe_to_unfile_only_once_record_and_credentials_are_in_the_database(
        self, db, tmp_path, capsys, monkeypatch
    ):
        url, engine = db
        key = generate_master_key()
        data = client_yaml("alpha", mode="approval")
        data["brain"] = {"agent": "consultative", "settings": minimal()}
        write_client(tmp_path / "clients", "alpha", raw=yaml.safe_dump(data))
        settings = settings_for(tmp_path, url, key)
        check = lambda: run_db(settings, argparse.Namespace(db_command="legacy-check"))

        with engine.begin() as c:
            c.execute(
                text(
                    "insert into clients (client_id, created_at, created_by) "
                    "values ('alpha', :t, 'seed')"
                ),
                {"t": NOW},
            )
        assert check() == 1  # record not filled yet
        run_db(settings, argparse.Namespace(db_command="clients", apply=True))
        assert check() == 1  # settings and credentials only in the file and the environment
        assert "only in the file" in capsys.readouterr().out

        for name, value in env_for("alpha").items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("ALPHA_PHONE_ID", "5550101")
        run_secrets(
            argparse.Namespace(secrets_command="import-env", client="alpha", apply=True), settings
        )
        # a published version supplies what the file held (model, Sheet), as in a real deployment
        from sales_app.config import import_document, read_wiring
        from sales_app.persistence.sql_config_store import SqlConfigStore

        raw, _ = read_wiring(tmp_path / "clients" / "alpha")
        doc = import_document("alpha", raw).document
        SqlConfigStore(engine).publish("alpha", doc, author="t", note="", expected_parent=None)
        monkeypatch.delenv("ALPHA_TOKEN")  # the environment is no longer needed
        assert check() == 0
        assert "safe to remove the files" in capsys.readouterr().out


def test_a_new_client_runs_on_credentials_stored_in_the_database_with_no_file_and_no_variable(
    tmp_path, monkeypatch
):
    url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    engine.dispose()
    write_client(tmp_path / "clients", "alpha")
    builders, _made = fake_builders(FakeClock(NOW))
    application = build_application(
        make_settings(
            tmp_path / "clients",
            database_url=url,
            secrets_dir=tmp_path / "s",
            secrets_key=generate_master_key(),
        ),
        env=env_for("alpha"),
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
        refresh_executor=InlineExecutor(),
    )
    try:
        assert isinstance(application.credentials._store, SqlSecretStore)
        declare_models(application.credentials, "gpt-4o")
        application.account_stores.admin_users.create(owner())
        admin = browser(application)
        admin.post("/admin/v1/auth/login", json={"username": "root", "password": PASSWORD})
        created = admin.post(
            "/admin/v1/clients",
            json={
                "display_name": "Acme Corp",
                "login": "acme.admin",
                "temporary_password": TEMP_PASSWORD,
                "llm_provider": "openai",
                "llm_model": "gpt-4o",
                "status": "active",
                "whatsapp_number": "+216 22 768 307",
            },
        )
        cid = created.json()["client_id"]
        client = browser(application)
        assert (
            client.post(LOGIN, json={"login": "acme.admin", "password": TEMP_PASSWORD}).status_code
            == 200
        )
        version = application.config_store.current(cid).version
        client.put(
            "/client/v1/settings",
            json={"expected_version": version, "settings": {"commercial": minimal()}},
        )
        publish_client_settings(
            application,
            cid,
            {"mode": "approval", "sheets": {"spreadsheet_id": "sheet-acme"}},
            expected_version=application.config_store.current(cid).version,
            author="admin:root",
            top_keys=("mode",),
            sections={"sheets": ("spreadsheet_id",)},
        )
        mark_source_tested(application, cid)

        # approval mode is live: without its WhatsApp credentials the client is not ready ...
        application.runtime_manager.refresh_client(cid).result(timeout=10)
        assert application.runtime_manager.status(cid).state == "not_ready"
        # ... and the Admin gives them once, in the database; no variable, no file
        scope = client_scope(cid)
        application.credentials.save(scope, WHATSAPP_ACCESS_TOKEN, MARKER, actor="admin:root")
        application.credentials.save(scope, WHATSAPP_PHONE_NUMBER_ID, "5550001", actor="admin:root")
        application.credentials.save(
            GLOBAL_SCOPE, WHATSAPP_APP_SECRET, "app-secret", actor="admin:root"
        )
        application.runtime_manager.refresh_client(cid).result(timeout=10)
        status = application.runtime_manager.status(cid)
        assert status.state == "applied", status
        assert (
            application.registry.get(cid) is not None
            and not application.registry.get(cid).suspended
        )
        assert application.registry.by_routing_key("whatsapp", "5550001").client_id == cid
        with application.account_stores.engine.connect() as c:
            stored = c.execute(
                text("select count(*) from secrets where scope = :s"), {"s": scope}
            ).scalar()
            leaked = c.execute(
                text("select count(*) from secrets where value like :m"), {"m": f"%{MARKER}%"}
            ).scalar()
        assert stored == 2 and leaked == 0
        assert not (tmp_path / "clients" / cid).exists()
    finally:
        application.close()


def test_the_cli_tools_know_a_client_that_exists_only_in_the_database(db, tmp_path):
    from sales_app.admin_cli import _client_known
    from sales_app.client_records import ClientRecord
    from sales_app.config_cli import _wiring
    from sales_app.persistence.client_record_rows import SqlClientRecordStore

    url, engine = db
    settings = settings_for(tmp_path, url)
    SqlClientRecordStore(engine).save(
        ClientRecord("gamma", "Gamma", True, "consultative"), at=NOW, by="admin:root"
    )
    assert _client_known(settings, "gamma") and not _client_known(settings, "nobody")
    assert _wiring(settings, "gamma")["brain"]["agent"] == "consultative"


def test_a_deployment_with_client_files_switched_off_starts_empty_and_runs_a_client(tmp_path):
    """No client folder at all, a stray file that must be ignored, an empty database: the first client
    is created from the Admin and runs on the database and the credential store alone."""
    url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    engine.dispose()
    clients = tmp_path / "no-such-folder"
    stray = tmp_path / "stray"  # a leftover legacy file in another place must change nothing
    write_client(stray, "ghost")
    builders, _made = fake_builders(FakeClock(NOW))
    application = build_application(
        make_settings(
            clients,
            database_url=url,
            secrets_dir=tmp_path / "s",
            secrets_key=generate_master_key(),
            legacy_client_files=False,
        ),
        env=env_for(),
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
        refresh_executor=InlineExecutor(),
    )
    try:
        assert application.registry.runtimes() == [] and not clients.exists()
        declare_models(application.credentials, "gpt-4o")
        application.account_stores.admin_users.create(owner())
        admin = browser(application)
        admin.post("/admin/v1/auth/login", json={"username": "root", "password": PASSWORD})
        created = admin.post(
            "/admin/v1/clients",
            json={
                "display_name": "Acme Corp",
                "login": "acme.admin",
                "temporary_password": TEMP_PASSWORD,
                "llm_provider": "openai",
                "llm_model": "gpt-4o",
                "status": "active",
                "whatsapp_number": "+216 22 768 307",
            },
        )
        assert created.status_code == 201, created.text
        cid = created.json()["client_id"]
        assert (
            admin.patch(f"/admin/v1/clients/{cid}", json={"daily_action_limit": 30}).status_code
            == 200
        )
        client = browser(application)
        login = client.post(LOGIN, json={"login": "acme.admin", "password": TEMP_PASSWORD})
        assert login.status_code == 200, login.text
        saved = client.put(
            "/client/v1/settings",
            json={
                "expected_version": application.config_store.current(cid).version,
                "settings": {"commercial": minimal()},
            },
        )
        assert saved.status_code == 200, saved.text
        publish_client_settings(
            application,
            cid,
            {"sheets": {"spreadsheet_id": "sheet-acme"}},
            expected_version=application.config_store.current(cid).version,
            author="client:acme",
            top_keys=(),
            sections={"sheets": ("spreadsheet_id",)},
        )
        mark_source_tested(application, cid)
        application.runtime_manager.refresh_client(cid).result(timeout=10)
        assert application.runtime_manager.status(cid).state == "applied"
        assert client.post("/client/v1/agent/start").status_code == 200
        assert application.agent_may_work(cid) is True
        assert not clients.exists()
        # the stray file's client does not exist for this deployment
        assert application.client_wiring("ghost")[0] is None
    finally:
        application.close()
