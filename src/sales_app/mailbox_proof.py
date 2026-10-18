"""Whether the mailbox Revansa assigned to a client has really been logged in to.

A mailbox is usable when the platform's servers are set, a mailbox is assigned, it has its password
**and** both logins (sending, reading) were accepted with exactly those settings. The proof is the
last login test, kept in `Application.mail_auth` and voided by any change of server, address or
password (see `email_platform.fingerprint`). Both the Admin (its status, its tests) and the Client's
channel status read it here, so they cannot disagree.

Nothing here sends or reads a message. Nothing returned carries an address or a password: states and
fixed sentences only.
"""

import logging
from dataclasses import dataclass

from sales_app.bootstrap import Application
from sales_app.config.email_settings import PlatformEmailSettings, parse_email_settings
from sales_app.config.secrets import EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX
from sales_app.connection_tests import ConnectionResult
from sales_app.email_platform import (
    AUTHENTICATED,
    MailLoginProbeFactory,
    fingerprint,
    read_profile,
    run_login_check,
)
from sales_app.mailbox_access import MailboxAccess, platform_access
from sales_app.secret_store import GLOBAL_SCOPE, SecretStoreError, client_scope

log = logging.getLogger(__name__)

# The states of a client's mailbox, in the order they are met.
MISSING_CONFIGURATION = "missing_configuration"
NOT_ASSIGNED = "mailbox_not_assigned"
PASSWORD_MISSING = "password_missing"
NOT_AUTHENTICATED = "not_authenticated"
READY = "ready"

_PROBLEMS = {
    MISSING_CONFIGURATION: "the platform's mail servers are not fully set up",
    NOT_ASSIGNED: "no mailbox has been assigned to this client",
    PASSWORD_MISSING: "the mailbox assigned to this client has no password",
}


@dataclass(frozen=True, slots=True)
class ClientMailbox:
    state: str  # one of the states above, never READY here: that needs the proof
    access: MailboxAccess | None  # None unless servers, address and password are all there
    problem: str | None  # a fixed sentence when `access` is None

    @property
    def print_(self) -> str:
        assert self.access is not None
        a = self.access
        return fingerprint(
            a.smtp.host,
            str(a.smtp.port),
            a.smtp.security,
            a.smtp.username,
            a.smtp.password,
            a.imap.host,
            str(a.imap.port),
            a.imap.security,
        )


def _reveal(application: Application, scope: str, key: str) -> str | None:
    try:
        return application.credentials.reveal(scope, key)
    except SecretStoreError:
        return None


def client_mailbox(application: Application, client_id: str) -> ClientMailbox:
    """What the client's assigned mailbox is made of. Reads only this client's scope and the
    platform's servers."""
    profile, _ = read_profile(lambda key: _reveal(application, GLOBAL_SCOPE, key))
    if profile is None:
        return ClientMailbox(MISSING_CONFIGURATION, None, _PROBLEMS[MISSING_CONFIGURATION])
    scope = client_scope(client_id)
    address = _reveal(application, scope, EMAIL_PLATFORM_MAILBOX)
    if not address:
        return ClientMailbox(NOT_ASSIGNED, None, _PROBLEMS[NOT_ASSIGNED])
    password = _reveal(application, scope, EMAIL_PASSWORD)
    if not password:
        return ClientMailbox(PASSWORD_MISSING, None, _PROBLEMS[PASSWORD_MISSING])
    return ClientMailbox(
        NOT_AUTHENTICATED,
        platform_access(profile, address, password),
        None,
    )


def protocol_states(
    application: Application, client_id: str, mailbox: ClientMailbox
) -> tuple[str, str]:
    assert mailbox.access is not None
    ledger = application.mail_auth
    return (
        ledger.state(f"client:{client_id}:smtp", mailbox.print_),
        ledger.state(f"client:{client_id}:imap", mailbox.print_),
    )


def mailbox_state(application: Application, client_id: str) -> str:
    """`ready` only when both logins were accepted with the settings as they are now."""
    mailbox = client_mailbox(application, client_id)
    if mailbox.access is None:
        return mailbox.state
    smtp, imap = protocol_states(application, client_id, mailbox)
    return READY if smtp == imap == AUTHENTICATED else NOT_AUTHENTICATED


def verify_client_mailbox(
    application: Application, client_id: str, probe_for: MailLoginProbeFactory
) -> tuple[ClientMailbox, list[ConnectionResult]]:
    """Log in to both servers with the client's mailbox and remember the outcome. Nothing is
    attempted (and the results are empty) while the mailbox is incomplete."""
    mailbox = client_mailbox(application, client_id)
    if mailbox.access is None:
        return mailbox, []
    probe = probe_for(mailbox.access.smtp, mailbox.access.imap)
    results = [
        run_login_check(
            protocol,
            probe,
            application.mail_auth,
            f"client:{client_id}:{protocol}",
            mailbox.print_,
            protocol.upper(),
        )
        for protocol in ("smtp", "imap")
    ]
    return mailbox, results


def platform_mailbox_clients(application: Application) -> list[str]:
    """The clients whose e-mail is a mailbox Revansa provides."""
    found = []
    for config in application.registry.configs():
        try:
            settings = parse_email_settings(config.spec.channel_settings_for("email"))
        except ValueError:
            continue
        if isinstance(settings, PlatformEmailSettings):
            found.append(config.client_id)
    return sorted(found)


def verify_all(application: Application, probe_for: MailLoginProbeFactory) -> None:
    """Check again every platform mailbox that is not proven yet (after a restart nothing is). One
    client's failure never stops the others, and nothing it reports reaches a log."""
    for client_id in platform_mailbox_clients(application):
        try:
            if mailbox_state(application, client_id) == NOT_AUTHENTICATED:
                verify_client_mailbox(application, client_id, probe_for)
        except Exception:  # noqa: BLE001 - a broken mailbox must not stop the checks of the others
            log.warning("mailbox check failed for client %s", client_id)
