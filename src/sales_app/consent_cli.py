"""`sales-agent consent ...`: the consent registry, for an operator.

    init          --client C                       set the registry up (explicit, safe to repeat)
    status        --client C                       health of the registry and its counts
    verify-chain  --client C                       check every event and the anchor
    check         --client C                       is a contact blocked? (asks for it, hidden)
    record        --client C --reason R            record a refusal you were told of
    release       --client C --username U ...      end a block (owner account, password, evidence)

A contact is never an argument and never printed: it is asked for with a hidden prompt, turned into
a keyed digest at once, and forgotten. Nothing here shows a key or a digest either.

`init`, `record` and `release` write, so they refuse to run while the application is running (the
same rule as `run`): two processes must not write the credential store at once. `status`,
`verify-chain` and `check` only read and may run at any time.

Releasing a block changes no prospect and reactivates no one: see `sales_app.sourcing.consent.admin`.
What this registry does and does not protect against: see `sales_app.sourcing.consent.events`: it
does not protect against someone able to restore the log and the credential store together.

Exit codes: 0 ok, 1 a problem (not set up, store or log unavailable), 2 invalid input or refused,
3 the application is running, 4 the registry failed its integrity check.
"""

import argparse
import getpass
import re
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sales_app.admin_auth import AdminUser, AdminUserStore
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.lockfile import InstanceAlreadyRunningError, InstanceLock
from sales_app.persistence import build_account_stores
from sales_app.secret_store import (
    AUDIT_FILENAME,
    FileSecretAudit,
    SecretStore,
    SecretStoreError,
    build_secret_store,
)
from sales_app.settings import Settings
from sales_app.sourcing.consent import (
    CONFIRMATION_PHRASE,
    CheckResult,
    ConsentAdmin,
    ConsentError,
    ConsentNotAllowedError,
    ConsentRefusedError,
    ConsentRegistry,
    ConsentSecrets,
    ConsentUnavailableError,
    FileConsentLog,
    Health,
    InitResult,
    ReasonCode,
    Report,
)
from sales_app.sourcing.consent.events import ACTOR, evidence_problem
from sales_core.contracts import ContactInfo

EXIT_OK, EXIT_PROBLEM, EXIT_INVALID, EXIT_RUNNING, EXIT_INTEGRITY = 0, 1, 2, 3, 4
CONSENT_DIRNAME = "consent"
PROTECTION_NOTE = (
    "Note: the registry reveals edits to its log by anyone without the key. It does not protect "
    "against someone able to restore the log and the credential store together."
)
_RECORD_REASONS = [ReasonCode.OPERATOR_REQUEST, ReasonCode.OTHER]
_RELEASE_REASONS = [ReasonCode.WRITTEN_CONSENT, ReasonCode.REGISTRATION_ERROR, ReasonCode.OTHER]


def add_consent_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    consent = commands.add_parser("consent", help="manage a client's consent (opt-out) registry")
    actions = consent.add_subparsers(dest="consent_command", required=True)

    def client(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--client", required=True)

    client(actions.add_parser("init", help="set the registry up (explicit, safe to repeat)"))
    client(actions.add_parser("status", help="health and counts of the registry"))
    client(actions.add_parser("verify-chain", help="check every event and the anchor"))
    client(actions.add_parser("check", help="is a contact blocked? (asked for, hidden)"))

    record = actions.add_parser("record", help="record a refusal (the contact is asked for)")
    client(record)
    record.add_argument("--reason", choices=[r.value for r in _RECORD_REASONS], required=True)
    record.add_argument("--evidence", default="", help="a reference, never personal data")

    release = actions.add_parser(
        "release",
        help="end a block (owner password, reason and evidence; reactivates no prospect)",
    )
    client(release)
    release.add_argument("--username", required=True, help="an active owner account")
    release.add_argument("--reason", choices=[r.value for r in _RELEASE_REASONS], required=True)
    release.add_argument(
        "--evidence", required=True, help="a reference to the proof, never personal data"
    )


def run_consent(settings: Settings, args: argparse.Namespace) -> int:
    client_id = args.client
    if not CLIENT_ID_PATTERN.fullmatch(client_id):
        return _fail("invalid client id", EXIT_INVALID)
    if not (settings.clients_dir / client_id / "client.yaml").is_file():
        return _fail("unknown client", EXIT_INVALID)
    try:
        store = build_secret_store(
            settings.secrets_dir,
            settings.secrets_key.get_secret_value() if settings.secrets_key else None,
        )
    except SecretStoreError:
        return _fail("the credential store cannot be opened (check the master key)", EXIT_PROBLEM)

    command = args.consent_command
    needs_exclusive_access = command in {"init", "record", "release"}
    try:
        with _exclusive(settings) if needs_exclusive_access else _nothing():
            if command == "release":
                return _release(settings, store, args)
            admin = _admin(settings, store, client_id, _NoAccounts())
            return _HANDLERS[command](admin, args)
    except InstanceAlreadyRunningError:
        return _fail(
            "the application is running: stop it first, this command writes (status, "
            "verify-chain and check can run at any time)",
            EXIT_RUNNING,
        )
    except ConsentUnavailableError as error:
        return _unhealthy(error.health, error.reason)
    except ConsentRefusedError as error:
        return _fail(f"{error} (reason: {error.reason})", EXIT_INTEGRITY)
    except ConsentNotAllowedError as error:
        return _fail(str(error), EXIT_INVALID)
    except ConsentError as error:
        return _fail(str(error), EXIT_PROBLEM)


# ---- commands ----------------------------------------------------------------------------------


def _init(admin: ConsentAdmin, args: argparse.Namespace) -> int:
    result = admin.initialise(actor=_operator())
    print(
        {
            InitResult.CREATED: "consent registry created",
            InitResult.COMPLETED: "an interrupted set-up was completed (no new key was made)",
            InitResult.ALREADY_INITIALISED: "the consent registry is already set up; nothing changed",
        }[result]
    )
    return EXIT_OK


def _status(admin: ConsentAdmin, args: argparse.Namespace) -> int:
    report = admin.status()
    _print_report(report)
    print(PROTECTION_NOTE)
    return _exit_for(report)


def _check(admin: ConsentAdmin, args: argparse.Namespace) -> int:
    contact = _ask_contact()
    if contact is None:
        return _fail("give a phone number, an e-mail address or both", EXIT_INVALID)
    result = admin.check(contact)
    _print_check(result)
    return _exit_for(result.report)


def _record(admin: ConsentAdmin, args: argparse.Namespace) -> int:
    problem = evidence_problem(args.evidence)
    if problem is not None:
        return _fail(f"--evidence: {problem}", EXIT_INVALID)
    contact = _ask_contact()
    if contact is None:
        return _fail("give a phone number, an e-mail address or both", EXIT_INVALID)
    recorded = admin.record(
        contact,
        actor=_operator(),
        reason=ReasonCode(args.reason),
        evidence_ref=args.evidence,
    )
    print(f"{recorded} identity(ies) newly blocked" if recorded else "already blocked: no change")
    return EXIT_OK


def _release(settings: Settings, store: SecretStore, args: argparse.Namespace) -> int:
    contact = _ask_contact()
    if contact is None:
        return _fail("give a phone number, an e-mail address or both", EXIT_INVALID)
    again = _ask_contact("again, to be sure")
    if again != contact:
        return _fail("the two entries differ; nothing was changed", EXIT_INVALID)
    password = getpass.getpass(f"Password of {args.username}: ")
    print(f'To end this block, type exactly: "{CONFIRMATION_PHRASE}"')
    confirmation = input("> ")
    stores = build_account_stores(settings)
    try:
        admin = _admin(settings, store, args.client, stores.admin_users)
        result = admin.release(
            contact,
            username=args.username,
            password=password,
            reason=ReasonCode(args.reason),
            evidence_ref=args.evidence,
            confirmation=confirmation,
        )
    finally:
        stores.close()
    print(f"{result.released} identity(ies) released" if result.released else "nothing to release")
    print(result.notice)
    return EXIT_OK


_HANDLERS: dict[str, Callable[[ConsentAdmin, argparse.Namespace], int]] = {
    "init": _init,
    "status": _status,
    "verify-chain": _status,
    "check": _check,
    "record": _record,
}


# ---- helpers -----------------------------------------------------------------------------------


@dataclass
class _NoAccounts:
    """Stands in for the account store in commands that never authenticate anyone."""

    def get_by_username(self, username: str) -> AdminUser | None:
        return None


def _admin(
    settings: Settings, store: SecretStore, client_id: str, users: "AdminUserStore | _NoAccounts"
) -> ConsentAdmin:
    audit = FileSecretAudit(settings.secrets_dir / AUDIT_FILENAME)
    log = FileConsentLog(settings.secrets_dir / CONSENT_DIRNAME / f"{client_id}.jsonl")
    registry = ConsentRegistry(client_id, log, ConsentSecrets(store, audit, client_id))
    return ConsentAdmin(registry, users, audit)  # type: ignore[arg-type]


@contextmanager
def _exclusive(settings: Settings):
    with InstanceLock(settings.lock_file):
        yield


@contextmanager
def _nothing():
    yield


def _ask_contact(label: str = "") -> ContactInfo | None:
    suffix = f" ({label})" if label else ""
    phone = getpass.getpass(f"Phone number{suffix} [hidden, empty for none]: ").strip()
    email = getpass.getpass(f"E-mail address{suffix} [hidden, empty for none]: ").strip()
    if not phone and not email:
        return None
    return ContactInfo(phone=phone or None, email=email or None)


def _operator() -> str:
    try:
        name = re.sub(r"[^A-Za-z0-9_.@-]", "_", getpass.getuser())[:58]
    except Exception:  # noqa: BLE001 - no user name available (some containers)
        name = ""
    actor = f"cli:{name}" if name else "cli"
    return actor if ACTOR.fullmatch(actor) else "cli"


def _print_report(report: Report) -> None:
    print(f"health: {report.health.value}" + (f" ({report.reason})" if report.reason else ""))
    if report.health is Health.OK:
        print(
            f"events: {report.events}, identities blocked: {report.blocked}, "
            f"releases: {report.releases}"
        )
        if report.anchor_behind:
            print("warning: the anchor trails valid events; it catches up at the next write")
        if report.torn_tail:
            print("warning: an unfinished last line was ignored; it is removed at the next write")
    else:
        print("every contact is blocked until the registry is healthy again")


def _print_check(result: CheckResult) -> None:
    _print_report(result.report)
    if result.report.health is not Health.OK:
        return
    for identity in result.identities:
        if identity.blocked:
            print(
                f"{identity.kind}: BLOCKED since {identity.since:%Y-%m-%d %H:%M} UTC "
                f"(origin: {identity.origin.value}, reason: {identity.reason.value}, "  # type: ignore[union-attr]
                f"events: {identity.events})"
            )
        else:
            print(f"{identity.kind}: not blocked ({identity.events} event(s))")


def _exit_for(report: Report) -> int:
    if report.health is Health.OK:
        return EXIT_OK
    return EXIT_INTEGRITY if report.health is Health.CORRUPT else EXIT_PROBLEM


def _unhealthy(health: str, reason: str) -> int:
    print(
        f"error: the registry is not healthy ({health}: {reason}); every contact is blocked",
        file=sys.stderr,
    )
    return EXIT_INTEGRITY if health == Health.CORRUPT.value else EXIT_PROBLEM


def _fail(message: str, code: int) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


__all__: list[Any] = ["add_consent_parser", "run_consent"]
