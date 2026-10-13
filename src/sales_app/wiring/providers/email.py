"""The e-mail channel: a client's own mailbox, written to over SMTP and read back over IMAP.

Nothing here is shared between clients: the servers, the address and the password are each
client's own, and a mailbox may belong to one client only (two clients reading the same one would
take each other's replies). The password is stored write-only in the client's scope; everything
else is a non-secret setting of the client's configuration (`channel.settings`).

Replies are not pushed to Revansa: the scheduler polls each client's mailbox (`sales_app.inbox`).
Which clients may use the channel at all is the Admin's decision (`sales_app.config.channel_access`).
"""

import contextlib
import smtplib

from sales_app.config import ClientConfig
from sales_app.config.email_settings import EmailChannelSettings
from sales_app.config.secrets import EMAIL_PASSWORD
from sales_app.connection_tests import MailProbeFailure
from sales_app.secret_store import ScopeKind
from sales_app.wiring.providers.types import (
    ChannelSupport,
    CredentialDeclaration,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
)
from sales_integrations.mail import (
    ImapMailbox,
    ImapSettings,
    MailAuthError,
    MailChannel,
    MailError,
    MailHostRefusedError,
    SmtpSettings,
    open_imap,
    open_smtp,
)

KEY = "email"
CHANNEL_KIND = "email"  # the Core's name for this channel


def _settings(config: ClientConfig) -> EmailChannelSettings:
    return EmailChannelSettings.model_validate(config.spec.channel_settings)


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


def build_channel(config: ClientConfig, *, smtp_factory=open_smtp) -> MailChannel:
    return MailChannel(
        smtp_settings(_settings(config), config.secrets.get(EMAIL_PASSWORD)), factory=smtp_factory
    )


def build_inbox(config: ClientConfig, *, imap_factory=open_imap) -> ImapMailbox:
    return ImapMailbox(
        imap_settings(_settings(config), config.secrets.get(EMAIL_PASSWORD)), factory=imap_factory
    )


class MailProbe:
    """What a connection test does with a mailbox: log in to each server, and nothing else. No
    message is sent and none is read."""

    def __init__(
        self,
        settings: EmailChannelSettings,
        password: str,
        *,
        smtp_factory=open_smtp,
        imap_factory=open_imap,
    ) -> None:
        self._settings = settings
        self._password = password
        self._smtp_factory = smtp_factory
        self._imap_factory = imap_factory

    def check_smtp(self) -> None:
        connection = None
        try:
            connection = self._smtp_factory(smtp_settings(self._settings, self._password))
            connection.login(self._settings.username, self._password)
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
        mailbox = ImapMailbox(
            imap_settings(self._settings, self._password), factory=self._imap_factory
        )
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


def _quietly(connection, method: str) -> None:
    if connection is None:
        return
    with contextlib.suppress(Exception):  # closing a connection must never mask the result
        getattr(connection, method)()


DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.CHANNEL,
    label="Email",
    settings_models={ProviderKind.CHANNEL: EmailChannelSettings},
    credentials=(
        CredentialDeclaration(
            key=EMAIL_PASSWORD,
            label="Email password",
            scope=ScopeKind.CLIENT,
            required_when=Requirement.LIVE,
        ),
    ),
    connection_keys=(EMAIL_PASSWORD,),
    capabilities={
        "can_initiate": True,
        "reply_window_hours": None,
        "requires_template_outside_window": False,
        "inbound": "polling",
    },
    build=build_channel,
    channel=ChannelSupport(
        kind=CHANNEL_KIND,
        routing_key=lambda config: None,
        webhook_secret=lambda config: None,
        build_parser=None,
        build_inbox=build_inbox,
    ),
)
