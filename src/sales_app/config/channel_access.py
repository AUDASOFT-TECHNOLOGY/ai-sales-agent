"""Which messaging channels a client may use: the Admin's decision, never the client's.

Two stored values, both written only by an administrator:

- **the global list** (`channels.allowed`): the channels every client may use. When the Admin has
  never set it, only WhatsApp is allowed: a channel is a way of contacting people, and a new one is
  off until someone decides otherwise.
- **a client's exceptions** (`channels.access`, in that client's scope): channels added for this
  client (`+email`) and channels taken away from it (`-whatsapp`). A refusal wins over a permission
  wherever both appear.

The result is judged every time a client's configuration is resolved, not only when the client
chooses a channel: a channel the Admin forbids afterwards stops working for the client that had
already set it up (the client is then not ready, and takes no new work).

Both values are plain text so they can live in the credential store: `whatsapp,email` or `none`,
and `+email,-whatsapp`.
"""

import re
from collections.abc import Iterable

# The channels this policy governs: the ones the platform offers. A provider the platform does not
# know, or "no channel", is not judged here (it cannot be built anyway). A test keeps this equal to
# the registry's channel providers, so a new channel cannot be added without being governed.
GOVERNED = frozenset({"whatsapp", "email", "telegram"})
DEFAULT_ALLOWED = frozenset({"whatsapp"})
NOBODY = "none"

_KEY = r"[a-z][a-z0-9_]{1,31}"
ALLOWED_PATTERN = re.compile(rf"^(none|{_KEY}(,{_KEY})*)$")
ACCESS_PATTERN = re.compile(rf"^[+-]{_KEY}(,[+-]{_KEY})*$")


def parse_allowed(value: str | None) -> frozenset[str]:
    """The global list; the default when nothing (or something unreadable) is stored."""
    if value is None or not ALLOWED_PATTERN.match(value.strip()):
        return DEFAULT_ALLOWED
    text = value.strip()
    return frozenset() if text == NOBODY else frozenset(text.split(","))


def format_allowed(channels: Iterable[str]) -> str:
    keys = sorted(set(channels))
    return ",".join(keys) if keys else NOBODY


def parse_access(value: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """(added, removed) for one client; empty when nothing (or something unreadable) is stored."""
    if value is None or not ACCESS_PATTERN.match(value.strip()):
        return frozenset(), frozenset()
    tokens = value.strip().split(",")
    return (
        frozenset(t[1:] for t in tokens if t[0] == "+"),
        frozenset(t[1:] for t in tokens if t[0] == "-"),
    )


def format_access(added: Iterable[str], removed: Iterable[str]) -> str | None:
    """The text to store, or None when there is no exception (the value is then removed)."""
    tokens = [f"+{k}" for k in sorted(set(added) - set(removed))]
    tokens += [f"-{k}" for k in sorted(set(removed))]
    return ",".join(tokens) or None


def effective_channels(global_value: str | None, access_value: str | None) -> frozenset[str]:
    """The channels one client may use."""
    added, removed = parse_access(access_value)
    return (parse_allowed(global_value) | added) - removed
