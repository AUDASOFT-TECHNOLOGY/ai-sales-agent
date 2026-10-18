"""What a client says about its own mailbox: servers, ports, addresses. Never the password.

The password is a credential (`email.password`, client scope, write-only). These settings are what
an administrator may read back and what the settings document holds, so nothing secret may ever be
a field here (the document check refuses keys that look like one).

The servers are typed by the client, so the ports are limited to the ones mail servers use and the
hosts are checked again, address by address, each time a connection is made
(`sales_integrations.mail.net`): a client cannot aim Revansa at the platform's own network.

**Three modes (exactly one at a time).** `mode` says where the mailbox comes from:

- absent, or `custom`: the historical mailbox, described here by its servers and login
  (`EmailChannelSettings`). A document written before modes existed has no `mode` and means this,
  unchanged. Only an administrator sets it up.
- `platform`: a mailbox Revansa provides. Its servers are the platform's (Admin -> Settings) and its
  address and password are the client's own credentials; the settings hold nothing technical, only
  what the client may say about how it writes (`PlatformEmailSettings`).
- `gmail`: the client's own Gmail, connected by Google OAuth (`GmailEmailSettings`); same wording
  fields, no technical setting and no password anywhere.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

_HOST = r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$"
_ADDRESS = r"^[^@\s<>,;\"'()\[\]\\]+@[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$"

Host = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, min_length=3, max_length=253, pattern=_HOST
    ),
]
Address = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, min_length=6, max_length=254, pattern=_ADDRESS
    ),
]
Login = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=254)]

DEFAULT_SUBJECT = "Following up on your enquiry"

PLATFORM_MODE = "platform"
CUSTOM_MODE = "custom"
GMAIL_MODE = "gmail"


def _plain_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 80 or any(ch in value for ch in '<>"\r\n,;\\'):
        raise ValueError("must be a short plain name")
    return value


def _single_line(value: str) -> str:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("must be a single line")
    return value


class EmailChannelSettings(BaseModel):
    """One client's mailbox: where Revansa sends from and reads replies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Absent: the historical mailbox. Never written by the code, so a document without it is
    # dumped exactly as before.
    mode: Literal["custom"] | None = None
    from_address: Address
    from_name: str | None = None
    username: Login
    smtp_host: Host
    smtp_port: Literal[25, 465, 587, 2525] = 587
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    imap_host: Host
    imap_port: Literal[143, 993] = 993
    imap_security: Literal["ssl", "starttls"] = "ssl"
    mailbox: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = "INBOX"
    subject: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
    ] = DEFAULT_SUBJECT

    @field_validator("from_address", "smtp_host", "imap_host", mode="before")
    @classmethod
    def _normalised(cls, value: object) -> object:
        """Typed by a person: spelling and capitals are not what makes an address different."""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("from_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _plain_name(value)

    @field_validator("username", "mailbox", "subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return _single_line(value)

    @property
    def mailbox_key(self) -> str:
        """What identifies the mailbox: two clients must never read the same one."""
        return f"{self.imap_host}|{self.username.lower()}"


def mailbox_key_of(settings: dict[str, object]) -> str | None:
    """`mailbox_key` of raw channel settings, or None if they are not (yet) a valid mailbox."""
    try:
        return EmailChannelSettings.model_validate(settings).mailbox_key
    except ValueError:
        return None


class PlatformEmailSettings(BaseModel):
    """A mailbox Revansa provides: nothing technical, because nothing here is the client's to say.

    The servers are the platform's (`sales_app.email_platform`), the address is the one assigned to
    this client and the password is its own credential: none of them is a setting. What is left is
    how the client's messages are written. `extra="forbid"` refuses any server, port, login or
    password, whoever sends them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["platform"]
    from_name: str | None = None
    subject: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
    ] = DEFAULT_SUBJECT

    @field_validator("from_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _plain_name(value)

    @field_validator("subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return _single_line(value)


class GmailEmailSettings(BaseModel):
    """The client's own Gmail, connected with Google OAuth: nothing technical here either. The
    address and the grant are not settings (they are kept in the secret store by the OAuth callback);
    what is left is how the client's messages are written. `extra="forbid"` refuses any server,
    port, login or password."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["gmail"]
    from_name: str | None = None
    subject: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
    ] = DEFAULT_SUBJECT

    @field_validator("from_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _plain_name(value)

    @field_validator("subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return _single_line(value)


MailboxSettings = EmailChannelSettings | PlatformEmailSettings | GmailEmailSettings


def email_mode_of(settings: Mapping[str, Any]) -> str:
    """`platform`, `gmail` or `custom`. A document with no `mode` is `custom`: it is the historical
    one. A document has exactly one mode: there is no way to name two."""
    mode = settings.get("mode")
    if mode == PLATFORM_MODE:
        return PLATFORM_MODE
    return GMAIL_MODE if mode == GMAIL_MODE else CUSTOM_MODE


def parse_email_settings(settings: Mapping[str, Any]) -> MailboxSettings:
    """The settings of the mode they name, validated. Raises ValueError (a pydantic one) when they
    are not valid for it. A mode that is not `platform` is read by the historical model, which
    refuses any `mode` other than `custom`."""
    mode = email_mode_of(settings)
    if mode == PLATFORM_MODE:
        return PlatformEmailSettings.model_validate(dict(settings))
    if mode == GMAIL_MODE:
        return GmailEmailSettings.model_validate(dict(settings))
    return EmailChannelSettings.model_validate(dict(settings))


class EmailSettingsOfAnyMode(BaseModel):
    """What the e-mail provider's descriptor validates a channel's settings with: the settings of
    whichever mode they name. It only dispatches; it has no fields of its own."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return parse_email_settings(obj)


def canonical_mailbox_address(value: str) -> str:
    """What identifies an assigned mailbox: its address, stripped and in lowercase. It is the very
    address the mailbox logs in and sends with, so nothing else is changed in it."""
    return value.strip().lower()


def platform_mailbox_key(address: str) -> str:
    """The key under which two clients are compared. In its own namespace: it can never equal the
    key of a historical mailbox (`host|login`)."""
    return f"platform|{canonical_mailbox_address(address)}"
