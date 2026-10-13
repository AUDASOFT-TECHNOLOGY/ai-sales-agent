"""E-mail adapters: sending over SMTP and reading replies over IMAP (standard library only)."""

from sales_integrations.mail.channel import MailChannel, SmtpSettings, open_smtp
from sales_integrations.mail.errors import (
    MailAuthError,
    MailError,
    MailHostRefusedError,
    MailUnavailableError,
)
from sales_integrations.mail.mailbox import (
    FetchedMail,
    ImapMailbox,
    ImapSettings,
    open_imap,
    parse_mail,
)

__all__ = [
    "FetchedMail",
    "ImapMailbox",
    "ImapSettings",
    "MailAuthError",
    "MailChannel",
    "MailError",
    "MailHostRefusedError",
    "MailUnavailableError",
    "SmtpSettings",
    "open_imap",
    "open_smtp",
    "parse_mail",
]
