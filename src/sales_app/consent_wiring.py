"""Which consent log a deployment uses: files (as before) or, strictly, the database.

In `sql` mode nothing here touches the file system, and there is no silent fallback: without a database
the application refuses to start, and a client that still has a consent file the database does not know
is refused with the command that imports it, never started on an empty registry (which would let every
opted-out contact be written to again).
"""

from typing import Any

from sales_app.errors import ConfigurationError
from sales_app.secret_store import SecretAudit, SecretStore
from sales_app.settings import Settings
from sales_app.sourcing.consent.anchor import ConsentSecrets
from sales_app.sourcing.consent.errors import ConsentError
from sales_app.sourcing.consent.files import CONSENT_DIRNAME, open_file_registry
from sales_app.sourcing.consent.registry import ConsentRegistry

IMPORT_COMMAND = "sales-agent consent import-log --client {client} --apply"


class ConsentImportPendingError(ConsentError):
    """The client's consent registry is still in a file the database has not received."""


def check_consent_mode(settings: Settings, engine: Any | None) -> None:
    """Refuse at startup a `sql` mode that has no database to run on."""
    if settings.consent_store == "sql" and engine is None:
        raise ConfigurationError(
            [
                (
                    "SALES_AGENT_CONSENT_STORE=sql needs SALES_AGENT_DATABASE_URL: consent is "
                    "never kept in files in that mode"
                )
            ]
        )


def open_consent_registry(
    settings: Settings,
    engine: Any | None,
    store: SecretStore,
    audit: SecretAudit,
    client_id: str,
) -> ConsentRegistry:
    if settings.consent_store == "file":
        return open_file_registry(settings.secrets_dir, store, audit, client_id)
    check_consent_mode(settings, engine)
    from sales_app.persistence.consent_rows import SqlConsentLog

    log = SqlConsentLog(engine, client_id)
    if (
        not log.exists()
        and (settings.secrets_dir / CONSENT_DIRNAME / f"{client_id}.jsonl").exists()
    ):
        raise ConsentImportPendingError(
            f"{client_id}: its consent registry is still in a file: run "
            f"`{IMPORT_COMMAND.format(client=client_id)}`"
        )
    return ConsentRegistry(client_id, log, ConsentSecrets(store, audit, client_id))
