"""The registry's keys, and how a contact becomes an opaque digest.

One random root key per client. From it two independent sub-keys are derived (HKDF-Expand with
HMAC-SHA256, one output block each, a different label each): one turns a contact into a digest, the
other signs the chain of events. Knowing a digest reveals nothing about the contact, and without
the key one cannot test a guess (an unkeyed hash of a phone number could be reversed by trying every
number).

Nothing here prints, logs or exposes a key: `repr` hides it and there is no accessor for it.
"""

import base64
import hashlib
import hmac
import re
import secrets

from sales_app.sourcing.consent.errors import ConsentKeyError
from sales_app.sourcing.contacts import contact_keys, lookup_keys
from sales_core.contracts import ContactInfo

ROOT_KEY_BYTES = 32
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")  # 32 bytes, base64url, no padding


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_root_key() -> str:
    """A fresh root key, in the form it is stored."""
    return _b64(secrets.token_bytes(ROOT_KEY_BYTES))


def parse_root_key(text: str) -> bytes:
    """The raw key behind a stored one. Strict; never repeats the text in its error."""
    if not isinstance(text, str) or not TOKEN.fullmatch(text):
        raise ConsentKeyError("the consent root key is malformed")
    return base64.urlsafe_b64decode(text + "=")


def _expand(root: bytes, label: str) -> bytes:
    return hmac.new(root, label.encode("utf-8") + b"\x01", hashlib.sha256).digest()


class ConsentKeys:
    """The two sub-keys of one client. Different clients never share a digest for one contact."""

    __slots__ = ("_chain", "_contact", "client_id")

    def __init__(self, root: bytes, client_id: str) -> None:
        if len(root) != ROOT_KEY_BYTES:
            raise ConsentKeyError("the consent root key has the wrong length")
        self.client_id = client_id
        self._contact = _expand(root, f"revansa-consent|v1|contact|{client_id}")
        self._chain = _expand(root, f"revansa-consent|v1|chain|{client_id}")

    @classmethod
    def from_stored(cls, text: str, client_id: str) -> "ConsentKeys":
        return cls(parse_root_key(text), client_id)

    # ---- contacts ------------------------------------------------------------------------

    def digest(self, contact_key: str) -> str:
        """The digest of one `phone:<digits>` / `email:<address>` key."""
        mac = hmac.new(self._contact, contact_key.encode("utf-8"), hashlib.sha256).digest()
        return _b64(mac)

    def digests(self, contact: ContactInfo) -> tuple[str, ...]:
        """One digest per usable identity of a contact, in a stable order. None if it has none."""
        return tuple(sorted(self.digest(key) for key in contact_keys(contact)))

    def digests_for_lookup(self, *, phone: str | None = None, email: str | None = None):
        return tuple(sorted(self.digest(key) for key in lookup_keys(phone=phone, email=email)))

    def kind_of(self, digest: str, contact: ContactInfo) -> str | None:
        """Which identity of `contact` ('phone' or 'email') a digest stands for, if any."""
        for key in contact_keys(contact):
            if hmac.compare_digest(self.digest(key), digest):
                return key.split(":", 1)[0]
        return None

    # ---- the chain -----------------------------------------------------------------------

    def sign(self, data: bytes) -> str:
        return _b64(hmac.new(self._chain, data, hashlib.sha256).digest())

    def genesis_mac(self) -> str:
        """What the first event links back to (and what the anchor holds before any event)."""
        return self.sign(f"genesis|{self.client_id}".encode())

    def __repr__(self) -> str:
        return f"ConsentKeys(client={self.client_id!r}, <keys redacted>)"

    def __eq__(self, other: object) -> bool:
        return NotImplemented  # never compared by value

    __hash__ = None  # type: ignore[assignment]
