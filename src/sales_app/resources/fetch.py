"""Opening a web address a client gave, without letting it reach anything private.

The address comes from a client, so it is untrusted: it could name the platform's own network
(``https://localhost/``, a cloud metadata address, a private IP) to make the server read something it
should not. The fetcher therefore:

- accepts ``https`` only, on the standard port, with no credentials in the address;
- resolves the host itself and refuses it unless **every** address it resolves to is a public one;
- connects to the address it checked (not to the name again), so the answer cannot change between the
  check and the connection, while the certificate is still verified against the host name;
- follows at most three redirects, checking each one the same way;
- reads at most `MAX_BYTES`, within `TIMEOUT` seconds, and only text-like pages or PDFs.

It sends nothing but a plain GET with an honest User-Agent. Known video sites are not fetched at all:
a page of a video player holds no readable content, and the client is told so.
"""

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 8.0
USER_AGENT = "RevansaReader/1.0 (reads a page its owner gave for their own assistant)"
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
PDF_TYPE = "application/pdf"

# Sites whose pages are players, not documents.
VIDEO_HOSTS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "dailymotion.com",
    "tiktok.com",
    "loom.com",
    "wistia.com",
    "wistia.net",
    "twitch.tv",
)

Resolver = Callable[[str, int], Sequence[str]]


class UnsafeAddressError(Exception):
    """The address is not a public https address (or does not resolve to only public ones)."""


class FetchFailedError(Exception):
    """The page could not be read (unreachable, an error status, too large, not text)."""


@dataclass(frozen=True, slots=True)
class Page:
    url: str  # the address finally read
    content_type: str  # without parameters, lowercase
    full_content_type: str
    body: bytes


def is_video_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise FetchFailedError("the host cannot be found") from None
    return [str(info[4][0]) for info in infos]


def check_address(url: str, resolver: Resolver = system_resolver) -> tuple[str, str, str]:
    """The (host, path-and-query, public IP to connect to) of a safe address, else an error."""
    parts = urlsplit(url.strip())
    host = parts.hostname
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise UnsafeAddressError("only plain https addresses are read")
    try:
        if parts.port not in (None, 443):
            raise UnsafeAddressError("only the standard port is used")
    except ValueError:
        raise UnsafeAddressError("the address is not valid") from None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        raise UnsafeAddressError("an address is not read by number")
    addresses = list(resolver(host, 443))
    if not addresses:
        raise FetchFailedError("the host cannot be found")
    chosen: str | None = None
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeAddressError("the address leads to a private network")
        chosen = chosen or str(ip)
    assert chosen is not None
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return host, target, chosen


def fetch_page(
    url: str,
    *,
    resolver: Resolver = system_resolver,
    transport: httpx.BaseTransport | None = None,
) -> Page:
    """Reads the page at `url` (see the module notes). Raises `UnsafeAddressError` or `FetchFailedError`."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        host, target, ip = check_address(current, resolver)
        netloc = f"[{ip}]" if ":" in ip else ip
        request_url = f"https://{netloc}{target}"
        try:
            with (
                httpx.Client(
                    transport=transport, timeout=TIMEOUT, follow_redirects=False, verify=True
                ) as client,
                client.stream(
                    "GET",
                    request_url,
                    headers={
                        "Host": host,
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,text/plain,application/pdf;q=0.9",
                    },
                    extensions={"sni_hostname": host},
                ) as response,
            ):
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise FetchFailedError("a redirect without a destination")
                    current = urljoin(f"https://{host}{target}", location)
                    continue
                if response.status_code != 200:
                    raise FetchFailedError(f"the page answered {response.status_code}")
                full = response.headers.get("content-type", "")
                kind = full.split(";")[0].strip().lower()
                if kind not in (*TEXT_TYPES, PDF_TYPE):
                    raise FetchFailedError("not a text page")
                limit = MAX_PDF_BYTES if kind == PDF_TYPE else MAX_BYTES
                body = bytearray()
                for piece in response.iter_bytes():
                    body.extend(piece)
                    if len(body) > limit:
                        raise FetchFailedError("the page is too large")
                return Page(
                    url=current, content_type=kind, full_content_type=full, body=bytes(body)
                )
        except httpx.HTTPError:
            raise FetchFailedError("the page could not be reached") from None
    raise FetchFailedError("too many redirects")
