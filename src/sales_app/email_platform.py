"""The servers of the mailboxes Revansa provides, and the check that they can be reached.

The Admin describes them once, in Settings (`email.platform_*`, global scope, none of them secret).
Nothing here assigns a mailbox to a client or reads a client's configuration: it only knows the
servers, and `check_platform_servers` says whether each answers over an encrypted connection.

**`check_platform_servers` never logs in and never sends or reads a message.** The login checks
(`MailLoginProbe`, `run_login_check`) are separate: they authenticate with a stored login, still
without sending or reading anything, and are the only thing that may be called "authenticated".
It opens the connection the way the
channel does (`open_smtp` / `open_imap`: same port limits, same refusal of hosts that are not public,
same certificate checks), then closes it. A success proves the servers can be reached from here at
that moment and nothing more: not that a login works, not that a message is delivered.

Messages are fixed sentences: never an address, never what a server answered.
"""

import contextlib
import hashlib
import smtplib
import threading
from collections.abc import Callable
from dataclasses import dataclass

from sales_app.config.secrets import (
    EMAIL_PLATFORM_IMAP_HOST,
    EMAIL_PLATFORM_IMAP_PORT,
    EMAIL_PLATFORM_IMAP_SECURITY,
    EMAIL_PLATFORM_SMTP_HOST,
    EMAIL_PLATFORM_SMTP_PORT,
    EMAIL_PLATFORM_SMTP_SECURITY,
)
from sales_app.connection_tests import ConnectionResult, MailProbe, MailProbeFailure
from sales_integrations.mail import (
    ImapMailbox,
    ImapSettings,
    MailAuthError,
    MailError,
    MailHostRefusedError,
    SmtpSettings,
    open_imap,
    open_smtp,
)

SMTP_PORTS = (25, 465, 587, 2525)
IMAP_PORTS = (143, 993)
SECURITIES = ("starttls", "ssl")

# The servers a profile needs, by the label the Admin sees. The domain is not needed to reach them.
_REQUIRED = (
    (EMAIL_PLATFORM_SMTP_HOST, "SMTP host"),
    (EMAIL_PLATFORM_SMTP_PORT, "SMTP port"),
    (EMAIL_PLATFORM_SMTP_SECURITY, "SMTP security"),
    (EMAIL_PLATFORM_IMAP_HOST, "IMAP host"),
    (EMAIL_PLATFORM_IMAP_PORT, "IMAP port"),
    (EMAIL_PLATFORM_IMAP_SECURITY, "IMAP security"),
)


@dataclass(frozen=True, slots=True)
class PlatformMailProfile:
    smtp_host: str
    smtp_port: int
    smtp_security: str
    imap_host: str
    imap_port: int
    imap_security: str


def read_profile(get: Callable[[str], str | None]) -> tuple[PlatformMailProfile | None, list[str]]:
    """The profile stored, or None and the labels of what is missing or unusable. `get` reads one
    global setting by key. The store already refuses bad values; this reads defensively anyway."""
    values = {key: (get(key) or "").strip() for key, _ in _REQUIRED}
    problems = [label for key, label in _REQUIRED if not values[key]]
    try:
        smtp_port = int(values[EMAIL_PLATFORM_SMTP_PORT])
        imap_port = int(values[EMAIL_PLATFORM_IMAP_PORT])
    except ValueError:
        smtp_port = imap_port = 0
    smtp_security = values[EMAIL_PLATFORM_SMTP_SECURITY].lower()
    imap_security = values[EMAIL_PLATFORM_IMAP_SECURITY].lower()
    unusable = [
        label
        for label, bad in (
            ("SMTP port", smtp_port not in SMTP_PORTS),
            ("IMAP port", imap_port not in IMAP_PORTS),
            ("SMTP security", smtp_security not in SECURITIES),
            ("IMAP security", imap_security not in SECURITIES),
        )
        if bad and label not in problems
    ]
    problems.extend(unusable)
    if problems:
        return None, problems
    return (
        PlatformMailProfile(
            smtp_host=values[EMAIL_PLATFORM_SMTP_HOST].lower(),
            smtp_port=smtp_port,
            smtp_security=smtp_security,
            imap_host=values[EMAIL_PLATFORM_IMAP_HOST].lower(),
            imap_port=imap_port,
            imap_security=imap_security,
        ),
        [],
    )


class PlatformMailProbe:
    """Opens an encrypted connection to each server and closes it. No login, no message."""

    def __init__(
        self, profile: PlatformMailProfile, *, smtp_factory=open_smtp, imap_factory=open_imap
    ):
        self._profile = profile
        self._smtp_factory = smtp_factory
        self._imap_factory = imap_factory

    def check_smtp(self) -> None:
        p = self._profile
        connection = None
        try:
            # The login and sender fields are required by the settings type and never used here.
            connection = self._smtp_factory(
                SmtpSettings(
                    host=p.smtp_host,
                    port=p.smtp_port,
                    security=p.smtp_security,
                    username="",
                    password="",
                    from_address="",
                )
            )
        except MailHostRefusedError:
            raise MailProbeFailure("refused") from None
        except MailError:
            raise MailProbeFailure("unreachable") from None
        finally:
            _quietly(connection, "quit")

    def check_imap(self) -> None:
        p = self._profile
        connection = None
        try:
            connection = self._imap_factory(
                ImapSettings(
                    host=p.imap_host,
                    port=p.imap_port,
                    security=p.imap_security,
                    username="",
                    password="",
                )
            )
        except MailHostRefusedError:
            raise MailProbeFailure("refused") from None
        except MailError:
            raise MailProbeFailure("unreachable") from None
        finally:
            _quietly(connection, "logout")


PlatformMailProbeFactory = Callable[[PlatformMailProfile], MailProbe]


class MailLoginProbe:
    """Logs in to the sending server and to the mailbox it is given, and does nothing else: no
    message is sent, none is read or marked. `smtp` / `imap` are the settings of one login (the
    platform's test login, or the mailbox assigned to a client)."""

    def __init__(
        self,
        smtp: SmtpSettings | None,
        imap: ImapSettings | None,
        *,
        smtp_factory=open_smtp,
        imap_factory=open_imap,
    ) -> None:
        self._smtp = smtp
        self._imap = imap
        self._smtp_factory = smtp_factory
        self._imap_factory = imap_factory

    def check_smtp(self) -> None:
        if self._smtp is None:
            raise MailProbeFailure("unreachable")
        connection = None
        try:
            connection = self._smtp_factory(self._smtp)
            connection.login(self._smtp.username, self._smtp.password)
        except MailHostRefusedError:
            raise MailProbeFailure("refused") from None
        except MailError:
            raise MailProbeFailure("unreachable") from None
        except smtplib.SMTPAuthenticationError:
            raise MailProbeFailure("auth") from None
        except (OSError, smtplib.SMTPException):
            raise MailProbeFailure("unreachable") from None
        finally:
            _quietly(connection, "quit")

    def check_imap(self) -> None:
        if self._imap is None:
            raise MailProbeFailure("unreachable")
        mailbox = ImapMailbox(self._imap, factory=self._imap_factory)
        try:
            mailbox.fetch(0)  # logs in and opens the folder; reads nothing
        except MailAuthError:
            raise MailProbeFailure("auth") from None
        except MailHostRefusedError:
            raise MailProbeFailure("refused") from None
        except MailError:
            raise MailProbeFailure("unreachable") from None
        finally:
            mailbox.close()


# Builds the probe of one login. Replaced by doubles in tests, as the connection factories are.
MailLoginProbeFactory = Callable[[SmtpSettings | None, ImapSettings | None], MailProbe]


# ---- what has been proven ---------------------------------------------------------------------

NOT_CONFIGURED = "not_configured"
NOT_TESTED = "not_tested"
AUTHENTICATED = "authenticated"
FAILED = "failed"


def fingerprint(*parts: str) -> str:
    """A digest of the settings a test ran on, so that a result stops counting the moment any of
    them (server, login or password) changes. Never shown, never stored."""
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class AuthLedger:
    """The last authentication test of each subject (`platform:smtp`, `client:alpha:imap`...) and
    the settings it ran on. Kept in memory: after a restart nothing is proven until it is tested
    again, which is the safe direction."""

    def __init__(self) -> None:
        self._results: dict[str, tuple[str, bool, str]] = {}
        self._lock = threading.Lock()

    def record(self, subject: str, print_: str, ok: bool, kind: str) -> None:
        with self._lock:
            self._results[subject] = (print_, ok, kind)

    def state(self, subject: str, print_: str) -> str:
        with self._lock:
            found = self._results.get(subject)
        if found is None or found[0] != print_:
            return NOT_TESTED
        return AUTHENTICATED if found[1] else FAILED

    def failure(self, subject: str, print_: str) -> str | None:
        with self._lock:
            found = self._results.get(subject)
        return found[2] if found and found[0] == print_ and not found[1] else None


def run_login_check(
    protocol: str, probe: MailProbe, ledger: AuthLedger, subject: str, print_: str, label: str
) -> ConnectionResult:
    """Run the login check of one protocol (`smtp` or `imap`), say how it went in a fixed sentence
    and remember it."""
    check = probe.check_smtp if protocol == "smtp" else probe.check_imap
    try:
        check()
    except MailProbeFailure as failure:
        ledger.record(subject, print_, False, failure.kind)
        return ConnectionResult(
            False,
            f"{label}: {_LOGIN_PROBLEMS.get(failure.kind, 'it failed')}.",
            {protocol: failure.kind},
        )
    ledger.record(subject, print_, True, "ok")
    return ConnectionResult(
        True,
        f"{label}: the server accepted the login. No message was sent and none was read.",
        {protocol: "ok"},
    )


_LOGIN_PROBLEMS = {
    "auth": "the server refused the login: check the username and the password",
    "refused": "this server cannot be used (it is not a public mail server)",
    "unreachable": "the server could not be reached over an encrypted connection",
}


def login_problem(kind: str) -> str:
    """The fixed sentence that explains a failed login check of this kind."""
    return _LOGIN_PROBLEMS.get(kind, "it failed")


def _quietly(connection, method: str) -> None:
    if connection is None:
        return
    with contextlib.suppress(Exception):  # closing a connection must never mask the result
        getattr(connection, method)()


_PROBLEMS = {
    "refused": "this server cannot be used (it is not a public mail server)",
    "unreachable": "the server could not be reached over an encrypted connection",
}


def check_platform_servers(
    get: Callable[[str], str | None], probe_for: PlatformMailProbeFactory
) -> ConnectionResult:
    """Reach each server of the stored profile. `get` reads one global setting by key."""
    profile, missing = read_profile(get)
    if profile is None:
        return ConnectionResult(
            False,
            "Email servers: the platform mail profile is not complete yet.",
            {"missing": ", ".join(missing)},
        )
    probe = probe_for(profile)
    details: dict[str, str] = {}
    problems: list[str] = []
    for name, check in (("smtp", probe.check_smtp), ("imap", probe.check_imap)):
        try:
            check()
        except MailProbeFailure as failure:
            details[name] = failure.kind
            problems.append(f"{name.upper()}: {_PROBLEMS.get(failure.kind, 'it failed')}")
        else:
            details[name] = "ok"
    if problems:
        return ConnectionResult(False, "Email servers: " + "; ".join(problems) + ".", details)
    return ConnectionResult(
        True,
        "Email servers: both answered over an encrypted connection. "
        "No login was attempted and no message was sent.",
        details,
    )
