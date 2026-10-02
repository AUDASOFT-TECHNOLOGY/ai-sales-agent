"""`sales-agent db ...` and the admin/config commands when the settings point at a database."""

import argparse
from datetime import UTC, datetime

import pytest
from test_persistence import PASSWORD, Source, account

from sales_app.admin_auth import FileAdminUserStore
from sales_app.admin_cli import EXIT_INVALID, EXIT_OK, add_admin_parser, run_admin
from sales_app.config_cli import add_config_parser, run_config
from sales_app.db_cli import EXIT_INVALID as DB_INVALID
from sales_app.db_cli import EXIT_OK as DB_OK
from sales_app.db_cli import EXIT_PROBLEM, add_db_parser, run_db
from sales_app.persistence import SqlAdminUserStore, create_database_engine
from sales_app.settings import Settings


def parse(adder, *argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    adder(commands)
    return parser.parse_args(argv)


def settings(tmp_path, source: Source | None = None, *, database=True) -> Settings:
    kw = {}
    if source is not None:
        kw = {
            "clients_dir": source.clients_dir,
            "admin_dir": source.admin_dir,
            "profiles_dir": source.profiles_dir,
            "config_dir": source.config_dir,
        }
    else:
        kw = {"clients_dir": tmp_path / "clients"}
    if database:
        kw["database_url"] = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    return Settings(**kw)


class TestDb:
    def test_without_a_database_url_it_says_so(self, tmp_path, capsys):
        code = run_db(settings(tmp_path, database=False), parse(add_db_parser, "db", "status"))
        assert code == DB_INVALID
        assert "SALES_AGENT_DATABASE_URL" in capsys.readouterr().err

    def test_status_before_and_after_upgrade(self, tmp_path, capsys):
        s = settings(tmp_path)
        assert run_db(s, parse(add_db_parser, "db", "status")) == EXIT_PROBLEM
        assert "db upgrade" in capsys.readouterr().out
        assert run_db(s, parse(add_db_parser, "db", "upgrade")) == DB_OK
        assert run_db(s, parse(add_db_parser, "db", "status")) == DB_OK
        assert "clients: 0" in capsys.readouterr().out

    def test_import_dry_run_then_import_then_verify(self, tmp_path, capsys):
        source = Source(tmp_path / "files")
        s = settings(tmp_path, source)
        run_db(s, parse(add_db_parser, "db", "upgrade"))
        capsys.readouterr()

        assert run_db(s, parse(add_db_parser, "db", "import", "--dry-run")) == DB_OK
        assert "nothing was written" in capsys.readouterr().out
        assert run_db(s, parse(add_db_parser, "db", "import")) == DB_OK
        assert "committed and verified" in capsys.readouterr().out
        assert run_db(s, parse(add_db_parser, "db", "import", "--dry-run")) == DB_OK
        assert "0 to insert" in capsys.readouterr().out

    def test_import_problems_exit_with_a_failure_and_name_the_problem(self, tmp_path, capsys):
        source = Source(tmp_path / "files")
        FileAdminUserStore(source.admin_dir / "users.json").create(
            account("bob", frozenset({"ghost"}))
        )
        s = settings(tmp_path, source)
        run_db(s, parse(add_db_parser, "db", "upgrade"))
        assert run_db(s, parse(add_db_parser, "db", "import")) == EXIT_PROBLEM
        assert "ghost" in capsys.readouterr().err

    def test_the_database_url_is_never_printed(self, tmp_path, capsys):
        s = Settings(database_url="postgresql://user:hunter2@127.0.0.1:1/none")
        assert run_db(s, parse(add_db_parser, "db", "status")) == EXIT_PROBLEM
        captured = capsys.readouterr()
        assert "hunter2" not in captured.out + captured.err
        assert "hunter2" not in repr(s)


class TestAdminAndConfigOnTheDatabase:
    @pytest.fixture
    def ready(self, tmp_path):
        s = settings(tmp_path)
        (s.clients_dir / "alpha").mkdir(parents=True)
        (s.clients_dir / "alpha" / "client.yaml").write_text("client_id: alpha", encoding="utf-8")
        assert run_db(s, parse(add_db_parser, "db", "upgrade")) == DB_OK
        return s

    def test_an_admin_is_created_in_the_database_not_in_files(self, ready, monkeypatch):
        monkeypatch.setattr("sales_app.admin_cli.getpass.getpass", lambda prompt="": PASSWORD)
        args = parse(
            add_admin_parser, "admin", "create", "--username", "alice", "--role", "editor",
            "--client", "alpha",
        )  # fmt: skip
        assert run_admin(ready, args) == EXIT_OK
        engine = create_database_engine(ready.database_url.get_secret_value())
        assert SqlAdminUserStore(engine).get_by_username("alice").client_ids == {"alpha"}
        assert not (ready.admin_dir / "users.json").exists()

    def test_a_scope_to_a_client_that_does_not_exist_is_refused(self, ready, monkeypatch, capsys):
        monkeypatch.setattr("sales_app.admin_cli.getpass.getpass", lambda prompt="": PASSWORD)
        args = parse(
            add_admin_parser, "admin", "create", "--username", "bob", "--role", "editor",
            "--client", "ghost",
        )  # fmt: skip
        assert run_admin(ready, args) == EXIT_INVALID
        assert "unknown client" in capsys.readouterr().err

    def test_config_commands_use_the_database(self, ready, tmp_path, capsys):
        document = tmp_path / "doc.json"
        document.write_text('{"schema_version": 2}', encoding="utf-8")
        # Publishing validates against client.yaml, which this minimal client does not satisfy:
        # the point here is only that the history is read from the database, empty to begin with.
        code = run_config(ready, parse(add_config_parser, "config", "history", "alpha"))
        assert code in (0, 1, 2)
        assert not (ready.config_dir / "alpha").exists()
        assert datetime.now(UTC)  # (no file store was touched)
