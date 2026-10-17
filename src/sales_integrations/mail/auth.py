"""How a mailbox proves who it is: a password (the historical way) or an OAuth access token (XOAUTH2).

The adapter never obtains a token: it is given one, already valid, by the layer above (which owns
the OAuth consent, the refresh token and the renewal). The access token is held like the password:
out of every `repr`, log line, error message and result. A server's refusal is reported with a
fixed sentence, never with what the server said, because that reply can echo the login.

`XOAUTH2` is the SASL mechanism Gmail and Microsoft 365 accept on IMAP and SMTP: the initial response
is `user=<address>^Aauth=Bearer <token>^A^A`. When the server refuses it, it first sends a challenge
(a JSON description of the error); the client must answer it with an empty line, after which the
server ends the exchange with its failure. The authenticators below do exactly that.
"""

PASSWORD = "password"
XOAUTH2 = "xoauth2"
AUTH_METHODS = (PASSWORD, XOAUTH2)

_SEPARATOR = "\x01"
_FORBIDDEN = set("\x00\x01\r\n")


def check_credentials(auth: str, password: str, access_token: str) -> None:
    """Refuse a mix-up before any connection: one way to authenticate, with what it needs.

    Messages are fixed sentences: never the password, the token or the address.
    """
    if auth == PASSWORD:
        if access_token:
            raise ValueError("an access token goes with the xoauth2 method, not the password one")
        return
    if auth == XOAUTH2:
        if password:
            raise ValueError("the xoauth2 method uses an access token, not a password")
        if not access_token:
            raise ValueError("the xoauth2 method needs an access token")
        return
    raise ValueError("unknown authentication method")


def xoauth2_string(username: str, access_token: str) -> str:
    """The XOAUTH2 initial response for a mailbox, before any base64 (the protocol library adds it).

    A user or a token holding a control character could forge the framing, so they are refused.
    """
    if not username or not access_token:
        raise ValueError("xoauth2 needs a username and an access token")
    if _FORBIDDEN & set(username) or _FORBIDDEN & set(access_token):
        raise ValueError("xoauth2 values may not hold control characters")
    if not (username.isascii() and access_token.isascii()):
        raise ValueError("xoauth2 values must be plain ASCII")
    return f"user={username}{_SEPARATOR}auth=Bearer {access_token}{_SEPARATOR}{_SEPARATOR}"


class ImapXOAuth2:
    """The `authobject` `imaplib.IMAP4.authenticate` wants: the response first, then an empty line
    for the error challenge a refusing server sends."""

    __slots__ = ("_initial", "_sent")

    def __init__(self, username: str, access_token: str) -> None:
        self._initial = xoauth2_string(username, access_token).encode("ascii")
        self._sent = False

    def __call__(self, challenge: bytes | None = None) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._initial

    def __repr__(self) -> str:
        return "ImapXOAuth2()"


class SmtpXOAuth2:
    """The `authobject` `smtplib.SMTP.auth` wants (it works in text, and asks for the initial
    response with no argument)."""

    __slots__ = ("_initial", "_sent")

    def __init__(self, username: str, access_token: str) -> None:
        self._initial = xoauth2_string(username, access_token)
        self._sent = False

    def __call__(self, challenge: bytes | None = None) -> str:
        if self._sent:
            return ""
        self._sent = True
        return self._initial

    def __repr__(self) -> str:
        return "SmtpXOAuth2()"
