"""Errors raised by the mail adapter.

Every message is a fixed sentence: never the password, never an address, never what the server
answered (a mail server's reply often repeats the address or the login it refused).
"""


class MailError(RuntimeError):
    """Base class for every adapter-specific failure."""


class MailUnavailableError(MailError):
    """The server could not be reached, or dropped the connection. Worth trying again later."""


class MailAuthError(MailError):
    """The server refused the login. Trying again with the same credentials cannot help."""


class MailHostRefusedError(MailError):
    """The host is not one Revansa may connect to (a private, loopback or reserved address)."""
