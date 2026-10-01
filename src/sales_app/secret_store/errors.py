"""Errors of the secret store. No message here ever carries a secret value."""


class SecretStoreError(Exception):
    """The store could not do what was asked."""


class SecretStoreUnavailableError(SecretStoreError):
    """No master key is configured, so nothing can be stored (reads find nothing)."""


class SecretStoreCorruptError(SecretStoreError):
    """The secrets file is unreadable or inconsistent. It is never guessed at."""


class InvalidMasterKeyError(SecretStoreError):
    """The master key is malformed, or does not match the data it is meant to open."""


class SecretDecryptError(SecretStoreError):
    """One record could not be decrypted: wrong key, altered, or moved to another scope/key."""


class DuplicateSecretError(SecretStoreError):
    """A value that must be unique across scopes is already held by another scope."""

    def __init__(self, holder_scope: str) -> None:
        self.holder_scope = holder_scope
        super().__init__(f"value already held by {holder_scope}")


class UnknownCredentialError(ValueError):
    """The key is not in the catalog, or does not belong to this kind of scope."""


class InvalidCredentialError(ValueError):
    """The value is not acceptable for this credential. Describes the rule, never the value."""
