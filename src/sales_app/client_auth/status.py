"""What the client sees of its own account: its name and whether it is active.

Read from the client's database record first, and from `clients/<id>/client.yaml` only for a client
whose record has not been filled yet (see `sales_app.config.client_wiring`). The Admin API reads
the same way, so the two never disagree about what "active" means."""

from pathlib import Path

from sales_app.client_records import ClientRecordStore
from sales_app.config.client_wiring import wiring_for
from sales_app.config_store.models import CLIENT_ID_PATTERN


def _raw(
    clients_dir: Path | None, client_id: str, records: ClientRecordStore | None
) -> dict | None:
    if not CLIENT_ID_PATTERN.match(client_id):
        return None
    raw, _problem = wiring_for(clients_dir, client_id, records)
    return raw


def is_client_active(
    clients_dir: Path | None, client_id: str, records: ClientRecordStore | None = None
) -> bool:
    """`enabled` as the record says, else as stored in client.yaml. A client with neither is not
    active."""
    raw = _raw(clients_dir, client_id, records)
    return raw is not None and raw.get("enabled", True) is not False


def stored_display_name(
    clients_dir: Path | None, client_id: str, records: ClientRecordStore | None = None
) -> str | None:
    raw = _raw(clients_dir, client_id, records)
    name = raw.get("display_name") if raw is not None else None
    return name.strip() if isinstance(name, str) and name.strip() else None
