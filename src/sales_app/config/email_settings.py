"""What a client says about its own mailbox: servers, ports, addresses. Never the password.

The password is a credential (`email.password`, client scope, write-only). These settings are what
an administrator may read back and what the settings document holds, so nothing secret may ever be
a field here (the document check refuses keys that look like one).

The servers are typed by the client, so the ports are limited to the ones mail servers use and the
hosts are checked again, address by address, each time a connection is made
(`sales_integrations.mail.net`): a client cannot aim Revansa at the platform's own network.
"""

from typing import Annotated, Literal

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


class EmailChannelSettings(BaseModel):
    """One client's mailbox: where Revansa sends from and reads replies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

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
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > 80 or any(ch in value for ch in '<>"\r\n,;\\'):
            raise ValueError("must be a short plain name")
        return value

    @field_validator("username", "mailbox", "subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("must be a single line")
        return value

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
