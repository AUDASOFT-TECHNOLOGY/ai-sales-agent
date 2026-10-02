"""Command line: `sales-agent validate | serve | run | config | admin | secrets | db`.

Exit codes: 0 success, 1 a job reported failure, 2 invalid configuration,
3 another instance is already running.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from sales_app.admin_cli import add_admin_parser, run_admin
from sales_app.bootstrap import Application, build_application, describe_modes
from sales_app.config_cli import add_config_parser, run_config
from sales_app.db_cli import add_db_parser, run_db
from sales_app.errors import ConfigurationError
from sales_app.lockfile import InstanceAlreadyRunningError, InstanceLock
from sales_app.logs import configure_logging
from sales_app.scheduler import Job
from sales_app.secrets_cli import add_secrets_parser, run_secrets
from sales_app.settings import Settings
from sales_core.approval import ApprovalMode

log = logging.getLogger("sales_app")

EXIT_OK, EXIT_JOB_FAILED, EXIT_CONFIG, EXIT_RUNNING = 0, 1, 2, 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    load_dotenv()  # secrets may live in a local .env; real environment variables win
    settings = Settings()
    configure_logging(settings.log_level)
    try:
        if args.command == "validate":
            return _validate(settings)
        if args.command == "serve":
            return _serve(settings)
        if args.command == "config":
            return run_config(settings, args)
        if args.command == "admin":
            return run_admin(settings, args)
        if args.command == "db":
            return run_db(settings, args)
        if args.command == "secrets":
            return run_secrets(args)
        return _run(settings, args)
    except ConfigurationError as error:
        _print_errors(error)
        return EXIT_CONFIG


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sales-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="check every client's configuration, start nothing")
    commands.add_parser("serve", help="run the scheduler and the webhook server")
    add_config_parser(commands)
    add_admin_parser(commands)
    add_secrets_parser(commands)
    add_db_parser(commands)
    run = commands.add_parser("run", help="run one job for one client, once")
    run.add_argument("--client", required=True)
    run.add_argument("--job", required=True, choices=[j.value for j in Job])
    run.add_argument("--dry-run", action="store_true", help="force dry_run for this run")
    return parser


def _validate(settings: Settings) -> int:
    application = build_application(settings, require_clients=False)
    modes = describe_modes(application)
    entries = {e["client_id"]: e for e in application.registry.health()}
    ready = 0
    for entry in modes:
        health = entries[entry["client_id"]]
        if health.get("status") == "not_ready":
            missing = ", ".join(str(m) for m in health.get("missing_credentials", []))  # type: ignore[attr-defined]
            print(f"NOT READY  {entry['client_id']}: missing {missing}")
            continue
        ready += 1
        note = f" (configured {entry['configured_mode']}, clamped)" if entry["mode_clamped"] else ""
        version = health.get("config_version")
        source = f" [config v{version}]" if version is not None else ""
        print(f"ok  {entry['client_id']}: mode {entry['mode']}{note}{source}")
    skipped = [e["client_id"] for e in entries.values() if e["status"] == "disabled"]
    if skipped:
        print(f"skipped (enabled: false): {', '.join(str(c) for c in skipped)}")
    waiting = len(modes) - ready
    suffix = f", {waiting} not ready (missing credentials)" if waiting else ""
    print(f"{ready} client(s) valid{suffix}")
    return EXIT_OK


def _serve(settings: Settings) -> int:
    import uvicorn

    from sales_app.api import create_app

    try:
        with InstanceLock(settings.lock_file):
            application = build_application(settings)
            _announce(application)
            # One worker on purpose: the Engine's single-writer guarantee is in-process.
            uvicorn.run(
                create_app(application),
                host=settings.host,
                port=settings.port,
                workers=1,
                log_config=None,
            )
    except InstanceAlreadyRunningError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_RUNNING
    return EXIT_OK


def _run(settings: Settings, args: argparse.Namespace) -> int:
    ceiling = ApprovalMode.DRY_RUN if args.dry_run else None
    try:
        with InstanceLock(settings.lock_file):
            application = build_application(settings, mode_ceiling=ceiling)
            _announce(application)
            runtime = application.registry.get(args.client)
            if runtime is None or runtime.suspended:
                missing = application.registry.missing(args.client)
                reason = f" (not ready, missing: {'; '.join(missing)})" if missing else ""
                print(f"error: no enabled client {args.client!r}{reason}", file=sys.stderr)
                return EXIT_CONFIG
            result = application.scheduler.run_job_now(runtime, Job(args.job))
    except InstanceAlreadyRunningError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_RUNNING
    for message in result.errors:
        print(f"problem: {message}", file=sys.stderr)
    print(f"{result.client_id} {result.job.value}: {'ok' if result.ok else 'FAILED'}")
    return EXIT_OK if result.ok else EXIT_JOB_FAILED


def _announce(application: Application) -> None:
    for entry in describe_modes(application):
        missing = application.registry.missing(str(entry["client_id"]))
        if missing:
            log.warning("client %s is not ready: %s", entry["client_id"], "; ".join(missing))
            continue
        log.info(
            "client %s runs in %s mode%s",
            entry["client_id"],
            entry["mode"],
            " (clamped by the deployment ceiling)" if entry["mode_clamped"] else "",
        )


def _print_errors(error: ConfigurationError) -> None:
    print(f"configuration invalid ({len(error.errors)} problem(s)):", file=sys.stderr)
    for message in error.errors:
        print(f"  - {message}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
