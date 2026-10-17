"""Which servers and which login a client's e-mail channel uses, whatever the mode.

One place turns a resolved client configuration (`ClientConfig`) into the settings the adapters
take (`SmtpSettings`, `ImapSettings`), so that sending, reading and the connection test cannot
disagree about where a mailbox is:

- **custom** (the historical mode, and every document without a `mode`): the servers and the login
  are the client's settings, the password its credential. Exactly what was done before modes.
- **platform**: the servers are the platform's (`email.platform_*`, set by the Admin in Settings,
  read from the same resolved configuration), the login and sender are the mailbox assigned to the
  client (`email.platform_mailbox`) and the password is the client's own (`email.password`). The
  login is the full address; that is the form the platform's providers are expected to take, and
  nothing here has been checked against a real server.

Nothing here connects to anything. A mailbox that cannot be resolved says so in a fixed sentence:
never an address, never a password.
"""

from dataclasses import dataclass

from sales_app.config import ClientConfig
from sales_app.config.email_settings import (
    CUSTOM_MODE,
    PLATFORM_MODE,
    EmailChannelSettings,
    PlatformEmailSettings,
    parse_email_settings,
)
from sales_app.config.secrets import EMAIL_PASSWORD, EMAIL_PLATFORM_MAILBOX
from sales_app.email_platform import read_profile
from sales_integrations.mail import ImapSettings, SmtpSettings


class MailboxNotReadyError(ValueError):
    """The mailbox cannot be resolved yet. The message is a fixed sentence, safe to show."""


@dataclass(frozen=True, slots=True)
class MailboxAccess:
    """Where one client's mailbox is and how it logs in. `repr` shows no credential (the adapters'
    settings hide their password)."""

    mode: str
    smtp: SmtpSettings
    imap: ImapSettings


def smtp_settings(settings: EmailChannelSettings, password: str) -> SmtpSettings:
    return SmtpSettings(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.username,
        password=password,
        from_address=settings.from_address,
        from_name=settings.from_name,
        subject=settings.subject,
    )


def imap_settings(settings: EmailChannelSettings, password: str) -> ImapSettings:
    return ImapSettings(
        host=settings.imap_host,
        port=settings.imap_port,
        security=settings.imap_security,
        username=settings.username,
        password=password,
        mailbox=settings.mailbox,
        own_addresses=(settings.from_address, settings.username),
    )


def resolve_mailbox(config: ClientConfig) -> MailboxAccess:
    """The mailbox of a client whose channel is e-mail, in whichever mode its settings name.

    Raises `MailboxNotReadyError` for a platform mailbox that lacks something (the loader reports
    it first, so a client that is ready never gets here without it); a pydantic `ValueError` for
    settings that are not valid; `KeyError` for a historical mailbox with no password, as before."""
    settings = parse_email_settings(config.spec.channel_settings_for("email"))
    if isinstance(settings, PlatformEmailSettings):
        return _platform(config, settings)
    password = config.secrets.get(EMAIL_PASSWORD)
    return MailboxAccess(
        mode=CUSTOM_MODE,
        smtp=smtp_settings(settings, password),
        imap=imap_settings(settings, password),
    )


def _platform(config: ClientConfig, settings: PlatformEmailSettings) -> MailboxAccess:
    profile, _ = read_profile(config.secrets.get_optional)
    if profile is None:
        raise MailboxNotReadyError("the platform mail servers are not configured")
    address = config.secrets.get_optional(EMAIL_PLATFORM_MAILBOX)
    if not address:
        raise MailboxNotReadyError("no mailbox has been assigned to this account")
    password = config.secrets.get_optional(EMAIL_PASSWORD)
    if not password:
        raise MailboxNotReadyError("the mailbox assigned to this account has no password")
    return MailboxAccess(
        mode=PLATFORM_MODE,
        smtp=SmtpSettings(
            host=profile.smtp_host,
            port=profile.smtp_port,
            security=profile.smtp_security,
            username=address,
            password=password,
            from_address=address,
            from_name=settings.from_name,
            subject=settings.subject,
        ),
        imap=ImapSettings(
            host=profile.imap_host,
            port=profile.imap_port,
            security=profile.imap_security,
            username=address,
            password=password,
            own_addresses=(address,),
        ),
    )
