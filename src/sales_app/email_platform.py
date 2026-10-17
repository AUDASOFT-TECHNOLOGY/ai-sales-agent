"""The servers of the mailboxes Revansa provides, and the check that they can be reached.

The Admin describes them once, in Settings (`email.platform_*`, global scope, none of them secret).
Nothing here assigns a mailbox to a client or reads a client's configuration: it only knows the
servers, and `check_platform_servers` says whether each answers over an encrypted connection.

**The check never logs in and never sends or reads a message.** It opens the connection the way the
channel does (`open_smtp` / `open_imap`: same port limits, same refusal of hosts that are not public,
same certificate checks), then closes it. A success proves the servers can be reached from here at
that moment and nothing more: not that a login works, not that a message is delivered.

Messages are fixed sentences: never an address, never what a server answered.
"""

import contextlib
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
    ImapSettings,
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
