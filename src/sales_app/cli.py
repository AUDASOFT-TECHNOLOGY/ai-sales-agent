"""Command line: `sales-agent validate | serve | run`.

Exit codes: 0 success, 1 a job reported failure, 2 invalid configuration,
3 another instance is already running.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from sales_app.bootstrap import Application, build_application, describe_modes
from sales_app.errors import ConfigurationError
from sales_app.lockfile import InstanceAlreadyRunningError, InstanceLock
from sales_app.logs import configure_logging
from sales_app.scheduler import Job
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
        return _run(settings, args)
    except ConfigurationError as error:
        _print_errors(error)
        return EXIT_CONFIG


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sales-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="check every client's configuration, start nothing")
    commands.add_parser("serve", help="run the scheduler and the webhook server")
    run = commands.add_parser("run", help="run one job for one client, once")
    run.add_argument("--client", required=True)
    run.add_argument("--job", required=True, choices=[j.value for j in Job])
    run.add_argument("--dry-run", action="store_true", help="force dry_run for this run")
    return parser


def _validate(settings: Settings) -> int:
    application = build_application(settings, require_clients=False)
    modes = describe_modes(application)
    for entry in modes:
        note = f" (configured {entry['configured_mode']}, clamped)" if entry["mode_clamped"] else ""
        print(f"ok  {entry['client_id']}: mode {entry['mode']}{note}")
    skipped = [e["client_id"] for e in application.registry.health() if e["status"] == "disabled"]
    if skipped:
        print(f"skipped (enabled: false): {', '.join(str(c) for c in skipped)}")
    print(f"{len(modes)} client(s) valid")
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
            if runtime is None:
                print(f"error: no enabled client {args.client!r}", file=sys.stderr)
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
