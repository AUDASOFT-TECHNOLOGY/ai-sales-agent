"""Where a client may point Revansa: never at the platform's own network.

The host and port of a mailbox are typed by a client, so without a check they would let a client
make the server connect to anything it can reach (an internal service, a cloud metadata address).
A host is accepted only if every address it resolves to is a public one, and only the ports mail
servers use are accepted.
"""

import ipaddress
import socket

from sales_integrations.mail.errors import MailHostRefusedError, MailUnavailableError

SMTP_PORTS = frozenset({25, 465, 587, 2525})
IMAP_PORTS = frozenset({143, 993})


def ensure_public_host(host: str, port: int, *, allowed_ports: frozenset[int]) -> None:
    if port not in allowed_ports:
        raise MailHostRefusedError("this port is not one a mail server uses")
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise MailUnavailableError("the mail server's name could not be resolved") from None
    for *_, address in found:
        ip = ipaddress.ip_address(address[0].split("%")[0])
        if not ip.is_global:
            raise MailHostRefusedError("this host is not a public mail server")
