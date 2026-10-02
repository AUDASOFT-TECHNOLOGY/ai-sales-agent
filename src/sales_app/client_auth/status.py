"""What the client sees of its own account that lives in `clients/<id>/client.yaml`: its name and
whether it is active. Read the same way the Admin API reads a disabled client (`read_wiring`), so
the two never disagree about what "active" means."""

from pathlib import Path

from sales_app.config import read_wiring
from sales_app.config_store.models import CLIENT_ID_PATTERN


def _raw(clients_dir: Path, client_id: str) -> dict | None:
    if not CLIENT_ID_PATTERN.match(client_id):
        return None
    raw, _problem = read_wiring(clients_dir / client_id)
    return raw


def is_client_active(clients_dir: Path, client_id: str) -> bool:
    """`enabled` as stored in client.yaml. A client with no readable client.yaml is not active."""
    raw = _raw(clients_dir, client_id)
    return raw is not None and raw.get("enabled", True) is not False


def stored_display_name(clients_dir: Path, client_id: str) -> str | None:
    raw = _raw(clients_dir, client_id)
    name = raw.get("display_name") if raw is not None else None
    return name.strip() if isinstance(name, str) and name.strip() else None
