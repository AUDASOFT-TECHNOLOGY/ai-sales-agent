"""`sales-agent db ...`: create and check the database, and move the file data into it.

    upgrade                create the schema, or bring it up to the latest revision
    status                 show the schema revision and what the database holds
    import [--dry-run]     copy accounts, client profiles and settings versions from the JSON files

`import` never modifies or deletes the files, runs in one transaction, and is safe to repeat. A
`--dry-run` does everything but commit, so it also verifies a past import: a complete one reports
every record as unchanged. Sessions are not imported (people simply log in again).
Exit codes: 0 ok, 1 problem with the data or the database, 2 invalid input (no database configured).
"""

import argparse
import sys
from datetime import UTC, datetime

from sales_app.persistence import (
    create_database_engine,
    current_revision,
    head_revision,
    import_file_data,
    table_counts,
    upgrade_database,
)
from sales_app.settings import Settings

EXIT_OK, EXIT_PROBLEM, EXIT_INVALID = 0, 1, 2


def add_db_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    db = commands.add_parser("db", help="create the database schema and import the file data")
    actions = db.add_subparsers(dest="db_command", required=True)
    actions.add_parser("upgrade", help="create or upgrade the schema")
    actions.add_parser("status", help="show the schema revision and the row counts")
    importer = actions.add_parser(
        "import", help="copy the data kept in JSON files into the database"
    )
    importer.add_argument(
        "--dry-run",
        action="store_true",
        help="do everything except commit: shows what would change, or verifies a past import",
    )


def run_db(settings: Settings, args: argparse.Namespace) -> int:
    if settings.database_url is None:
        print("error: SALES_AGENT_DATABASE_URL is not set", file=sys.stderr)
        return EXIT_INVALID
    engine = create_database_engine(settings.database_url.get_secret_value())
    try:
        return _HANDLERS[args.db_command](settings, engine, args)
    except Exception as error:  # noqa: BLE001 - never print the URL, which carries a password
        print(f"error: {error.__class__.__name__}: {_safe(error)}", file=sys.stderr)
        return EXIT_PROBLEM
    finally:
        engine.dispose()


def _safe(error: Exception) -> str:
    return str(error).splitlines()[0] if str(error) else ""


def _upgrade(settings, engine, args) -> int:
    before = current_revision(engine)
    upgrade_database(engine)
    after = current_revision(engine)
    print(
        f"schema: {before or 'empty'} -> {after}"
        if before != after
        else f"schema is up to date ({after})"
    )
    return EXIT_OK


def _status(settings, engine, args) -> int:
    current, head = current_revision(engine), head_revision()
    print(f"schema revision: {current or 'none'} (latest: {head})")
    if current != head:
        print("run `sales-agent db upgrade`")
        return EXIT_PROBLEM
    for name, count in table_counts(engine).items():
        print(f"{name}: {count}")
    return EXIT_OK


def _import(settings, engine, args) -> int:
    report = import_file_data(
        engine,
        clients_dir=settings.clients_dir,
        admin_dir=settings.admin_dir,
        profiles_dir=settings.profiles_dir,
        config_dir=settings.config_dir,
        now=datetime.now(UTC),
        dry_run=args.dry_run,
    )
    for table, counts in report.counts.items():
        print(
            f"{table}: {counts['source']} in files, {counts['inserted']} "
            f"{'to insert' if report.dry_run else 'inserted'}, {counts['unchanged']} unchanged"
        )
    for message in report.errors:
        print(f"problem: {message}", file=sys.stderr)
    if not report.ok:
        print("nothing was imported" if not report.committed else "", file=sys.stderr)
        return EXIT_PROBLEM
    print("dry run: nothing was written" if report.dry_run else "import committed and verified")
    return EXIT_OK


_HANDLERS = {"upgrade": _upgrade, "status": _status, "import": _import}
