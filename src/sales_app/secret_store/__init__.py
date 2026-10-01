"""Credentials kept server-side, encrypted, apart from client configuration.

A peer of `sales_app.config_store` and `sales_app.admin_auth`: a port first, a file-backed
implementation now, so a vault or a database can replace it later. Nothing in `client.yaml` or in a
versioned settings document ever holds a secret; this is where they live.
"""

from sales_app.secret_store.audit import (
    FileSecretAudit,
    InMemorySecretAudit,
    SecretAudit,
    SecretAuditEvent,
)
from sales_app.secret_store.catalog import (
    CATALOG,
    GLOBAL_SCOPE,
    CredentialSpec,
    ScopeKind,
    client_scope,
    scope_kind,
)
from sales_app.secret_store.cipher import AesGcmCipher, SecretCipher, generate_master_key
from sales_app.secret_store.errors import (
    DuplicateSecretError,
    InvalidCredentialError,
    InvalidMasterKeyError,
    SecretDecryptError,
    SecretStoreCorruptError,
    SecretStoreError,
    SecretStoreUnavailableError,
    UnknownCredentialError,
)
from sales_app.secret_store.factory import (
    AUDIT_FILENAME,
    KEY_VARIABLE,
    SECRETS_FILENAME,
    UnavailableSecretStore,
    build_secret_store,
)
from sales_app.secret_store.file_store import FileSecretStore
from sales_app.secret_store.memory_store import InMemorySecretStore
from sales_app.secret_store.port import SecretEntry, SecretStore
from sales_app.secret_store.service import CredentialService, CredentialStatus

__all__ = [
    "AUDIT_FILENAME",
    "CATALOG",
    "GLOBAL_SCOPE",
    "KEY_VARIABLE",
    "SECRETS_FILENAME",
    "AesGcmCipher",
    "CredentialService",
    "CredentialSpec",
    "CredentialStatus",
    "DuplicateSecretError",
    "FileSecretAudit",
    "FileSecretStore",
    "InMemorySecretAudit",
    "InMemorySecretStore",
    "InvalidCredentialError",
    "InvalidMasterKeyError",
    "ScopeKind",
    "SecretAudit",
    "SecretAuditEvent",
    "SecretCipher",
    "SecretDecryptError",
    "SecretEntry",
    "SecretStore",
    "SecretStoreCorruptError",
    "SecretStoreError",
    "SecretStoreUnavailableError",
    "UnavailableSecretStore",
    "UnknownCredentialError",
    "build_secret_store",
    "client_scope",
    "generate_master_key",
    "scope_kind",
]
