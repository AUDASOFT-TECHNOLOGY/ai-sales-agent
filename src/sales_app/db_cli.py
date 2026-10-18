"""`sales-agent db ...`: create and check the database, and move the file data into it.

    upgrade                create the schema, or bring it up to the latest revision
    status                 show the schema revision and what the database holds
    import [--dry-run]     copy accounts, client profiles and settings versions from the JSON files
    clients [--apply]      fill each client's database record (name, status) from its client.yaml;
                           without --apply it only reports what would change
    legacy-check           for each client that still has a client.yaml: could it run with the file
                           removed, from the database and the credential store alone?

`import` never modifies or deletes the files, runs in one transaction, and is safe to repeat. A
`--dry-run` does everything but commit, so it also verifies a past import: a complete one reports
every record as unchanged. Sessions are not imported (people simply log in again).
Exit codes: 0 ok, 1 problem with the data or the database, 2 invalid input (no database configured).
"""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

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
_PLATFORM_ENV = ("GOOGLE_APPLICATION_CREDENTIALS",)


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

    clients = actions.add_parser(
        "clients", help="report, then fill, the clients' records from their client.yaml"
    )
    clients.add_argument(
        "--apply",
        action="store_true",
        help="write the planned fills (without it, nothing is written)",
    )

    actions.add_parser(
        "legacy-check",
        help="check that every client with a client.yaml could run without it (reads only)",
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


def _clients(settings, engine, args) -> int:
    from sales_app.client_records.backfill import apply_backfill, plan_backfill
    from sales_app.persistence.client_record_rows import SqlClientRecordStore

    if current_revision(engine) != head_revision():
        print(
            "error: the schema is not up to date: run `sales-agent db upgrade` first",
            file=sys.stderr,
        )
        return EXIT_PROBLEM
    records = SqlClientRecordStore(engine)
    report = plan_backfill(settings.clients_dir, records)
    for change in report.changes:
        detail = f" ({change.note})" if change.note else ""
        shown = (
            f"{change.old!r} -> {change.new!r}"
            if change.action != "unchanged"
            else repr(change.new)
        )
        print(f"{change.client_id}: {change.field}: {change.action} {shown}{detail}")
    for message in report.problems:
        print(f"problem: {message}", file=sys.stderr)
    if not args.apply:
        print(f"report only: {len(report.fills())} field(s) would be filled; nothing was written")
        return EXIT_PROBLEM if report.problems else EXIT_OK
    written = apply_backfill(records, report, at=datetime.now(UTC))
    print(f"{written} field(s) filled")
    return EXIT_PROBLEM if report.problems else EXIT_OK


def _legacy_check(settings, engine, args) -> int:
    """Resolves each legacy client as if its folder and the environment did not exist. Reads only."""
    from tempfile import TemporaryDirectory

    from sales_app.client_records.backfill import plan_backfill
    from sales_app.config import resolve_client
    from sales_app.persistence.client_record_rows import SqlClientRecordStore
    from sales_app.persistence.sql_config_store import SqlConfigStore
    from sales_app.secret_wiring import open_secrets
    from sales_core.approval import ApprovalMode

    if current_revision(engine) != head_revision():
        print(
            "error: the schema is not up to date: run `sales-agent db upgrade` first",
            file=sys.stderr,
        )
        return EXIT_PROBLEM
    records = SqlClientRecordStore(engine)
    secrets, _audit = open_secrets(settings, engine)
    pending = {c.client_id for c in plan_backfill(settings.clients_dir, records).fills()}
    ids = sorted(plan_ids(settings.clients_dir))
    blocked = 0
    # Only what belongs to the platform, not to a client: the path of the Sheets service account.
    platform_env = {k: os.environ[k] for k in _PLATFORM_ENV if k in os.environ}
    with TemporaryDirectory() as bare:  # an empty folder: no client.yaml, no environment variable
        for client_id in ids:
            if client_id in pending:
                print(
                    f"{client_id}: BLOCKED, its record is not filled: run `sales-agent db clients --apply`"
                )
                blocked += 1
                continue
            resolution = resolve_client(
                Path(bare) / client_id,
                records=records,
                env=platform_env,
                mode_ceiling=ApprovalMode.APPROVAL,
                store=SqlConfigStore(engine),
                secret_store=secrets,
            )
            with_file = resolve_client(
                settings.clients_dir / client_id,
                records=records,
                env=dict(os.environ),
                mode_ceiling=ApprovalMode.APPROVAL,
                store=SqlConfigStore(engine),
                secret_store=secrets,
            )
            if resolution.errors:
                print(f"{client_id}: BLOCKED, {'; '.join(resolution.errors)}")
                blocked += 1
            elif (
                resolution.disabled
                and with_file.config is not None
                and _legacy_factory(settings.clients_dir, client_id)
            ):
                print(
                    f"{client_id}: BLOCKED, its Brain is a legacy `brain.factory` that only the file "
                    "can hold: name the generic engine instead (`brain.agent: consultative`)"
                )
                blocked += 1
            elif resolution.disabled and with_file.config is not None:
                print(
                    f"{client_id}: BLOCKED, its settings are only in the file: publish them with "
                    f"`sales-agent config import {client_id}`"
                )
                blocked += 1
            elif resolution.disabled:
                print(f"{client_id}: ok, loaded but awaiting its own configuration (no agent yet)")
            elif resolution.config is not None and not resolution.config.ready:
                missing = "; ".join(resolution.config.missing_credentials)
                print(f"{client_id}: BLOCKED, missing: {missing}")
                blocked += 1
            else:
                print(f"{client_id}: ok, ready without its client.yaml")
    print(
        "safe to remove the files" if not blocked else f"{blocked} client(s) still need their file"
    )
    return EXIT_PROBLEM if blocked else EXIT_OK


def _legacy_factory(clients_dir, client_id) -> bool:
    from sales_app.config import read_wiring

    raw, _ = read_wiring(clients_dir / client_id)
    brain = raw.get("brain") if raw else None
    return isinstance(brain, dict) and not brain.get("agent")


def plan_ids(clients_dir):
    from sales_app.persistence.clients import client_directories

    return client_directories(clients_dir)


_HANDLERS = {
    "upgrade": _upgrade,
    "status": _status,
    "import": _import,
    "clients": _clients,
    "legacy-check": _legacy_check,
}
