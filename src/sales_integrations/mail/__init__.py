"""E-mail adapters: sending over SMTP and reading replies over IMAP (standard library only)."""

from sales_integrations.mail.auth import PASSWORD, XOAUTH2, xoauth2_string
from sales_integrations.mail.channel import MailChannel, SmtpSettings, open_smtp
from sales_integrations.mail.cursor import (
    InMemoryMailCursorStore,
    MailCursor,
    MailCursorStore,
    mailbox_digest,
)
from sales_integrations.mail.errors import (
    MailAuthError,
    MailCursorUnavailableError,
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
    "PASSWORD",
    "XOAUTH2",
    "FetchedMail",
    "ImapMailbox",
    "ImapSettings",
    "InMemoryMailCursorStore",
    "MailAuthError",
    "MailChannel",
    "MailCursor",
    "MailCursorStore",
    "MailCursorUnavailableError",
    "MailError",
    "MailHostRefusedError",
    "MailUnavailableError",
    "SmtpSettings",
    "mailbox_digest",
    "open_imap",
    "open_smtp",
    "parse_mail",
    "xoauth2_string",
]
