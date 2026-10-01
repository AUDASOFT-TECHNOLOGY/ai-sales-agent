"""Encryption of one value at rest. AES-256-GCM with the scope and key bound in as associated data,
so a record copied to another scope or key (another client's token, say) fails to open."""

import base64
import binascii
import os
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sales_app.secret_store.errors import InvalidMasterKeyError, SecretDecryptError

KEY_BYTES = 32
_NONCE_BYTES = 12
KEY_HINT = (
    "it must be 32 random bytes, base64-encoded; create one with `sales-agent secrets generate-key`"
)


@runtime_checkable
class SecretCipher(Protocol):
    """`context` is bound to the ciphertext: decrypting with another context fails."""

    def encrypt(self, plaintext: str, context: str) -> str: ...

    def decrypt(self, token: str, context: str) -> str: ...


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def parse_master_key(text: str) -> bytes:
    """The raw key behind `SALES_AGENT_SECRETS_KEY`. Never echoes the key in an error."""
    cleaned = text.strip()
    try:
        raw = base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
    except (binascii.Error, ValueError):
        raise InvalidMasterKeyError(
            f"SALES_AGENT_SECRETS_KEY is not valid base64; {KEY_HINT}"
        ) from None
    if len(raw) != KEY_BYTES:
        raise InvalidMasterKeyError(f"SALES_AGENT_SECRETS_KEY has the wrong length; {KEY_HINT}")
    return raw


class AesGcmCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise InvalidMasterKeyError(f"the master key has the wrong length; {KEY_HINT}")
        self._aead = AESGCM(key)

    @classmethod
    def from_text(cls, text: str) -> "AesGcmCipher":
        return cls(parse_master_key(text))

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)  # a fresh nonce for every write
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return base64.urlsafe_b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, token: str, context: str) -> str:
        try:
            blob = base64.urlsafe_b64decode(token.encode("ascii"))
            plain = self._aead.decrypt(
                blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], context.encode("utf-8")
            )
            return plain.decode("utf-8")
        except (InvalidTag, binascii.Error, ValueError, UnicodeError):
            raise SecretDecryptError(
                "a stored record could not be decrypted (wrong master key, or the record was altered)"
            ) from None

    def __repr__(self) -> str:
        return "AesGcmCipher(<key redacted>)"
