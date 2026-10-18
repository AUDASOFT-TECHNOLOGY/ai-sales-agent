"""Which credential store and audit a deployment uses.

With a database (`SALES_AGENT_DATABASE_URL`) and a master key, credentials live in the database,
encrypted, isolated per scope (`global` or `client:<id>`): no file and no per-client environment
variable. Without a database the encrypted file under `secrets_dir` remains (single-machine setups).
A credential file left over from before is never ignored silently: with a database that holds no
credential yet, startup is refused and says which command moves it.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sales_app.persistence.secret_rows import SqlSecretAudit, SqlSecretStore
from sales_app.secret_store import (
    AUDIT_FILENAME,
    KEY_VARIABLE,
    SECRETS_FILENAME,
    AesGcmCipher,
    FileSecretAudit,
    InvalidMasterKeyError,
    SecretAudit,
    SecretStore,
    UnavailableSecretStore,
    build_secret_store,
)
from sales_app.settings import Settings

MIGRATE_COMMAND = "sales-agent secrets migrate-to-db"


def open_secrets(
    settings: Settings, engine: Any | None, *, now: Callable[[], datetime] | None = None
) -> tuple[SecretStore, SecretAudit]:
    """The store and the audit of this deployment, or a SecretStoreError describing what to fix."""
    key = settings.secrets_key.get_secret_value() if settings.secrets_key else None
    if engine is None:
        return (
            build_secret_store(settings.secrets_dir, key, now=now),
            FileSecretAudit(Path(settings.secrets_dir) / AUDIT_FILENAME),
        )
    audit = SqlSecretAudit(engine)
    if not (key or "").strip():
        # Nothing can be encrypted or opened: reads are empty, writes say why they cannot happen.
        return UnavailableSecretStore(), audit
    store = SqlSecretStore(engine, AesGcmCipher.from_text(key), now=now)
    store.verify()
    if (Path(settings.secrets_dir) / SECRETS_FILENAME).exists() and store.count() == 0:
        raise InvalidMasterKeyError(
            f"credentials are still in {Path(settings.secrets_dir) / SECRETS_FILENAME} and the "
            f"database holds none: run `{MIGRATE_COMMAND}` first ({KEY_VARIABLE} must be set)"
        )
    return store, audit
