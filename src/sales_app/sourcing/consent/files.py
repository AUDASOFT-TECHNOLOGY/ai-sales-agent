"""Where a client's consent registry lives on disk, in one place.

The application and the `consent` command open the registry through here, so they can never
disagree about which log and which keys a client's refusals are kept in.
"""

from pathlib import Path

from sales_app.secret_store import SecretAudit, SecretStore
from sales_app.sourcing.consent.anchor import ConsentSecrets
from sales_app.sourcing.consent.log import FileConsentLog
from sales_app.sourcing.consent.registry import ConsentRegistry

CONSENT_DIRNAME = "consent"


def open_file_registry(
    secrets_dir: Path, store: SecretStore, audit: SecretAudit, client_id: str
) -> ConsentRegistry:
    """The registry of one client: its log under `secrets_dir`, its key and anchor in `store`."""
    log = FileConsentLog(secrets_dir / CONSENT_DIRNAME / f"{client_id}.jsonl")
    return ConsentRegistry(client_id, log, ConsentSecrets(store, audit, client_id))
