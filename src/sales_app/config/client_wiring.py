"""A client's operator wiring, from its database record first and its `client.yaml` as the fallback.

`wiring_for` is the one place that decides what the wiring of a client is:

- a client with a `client.yaml` and no record (or an empty one) is read from the file, unchanged;
- a client with both gets the record laid over the file: its `enabled`, its name, and, only if the
  file has no real Brain yet, its engine. A Brain the file names is never replaced;
- a client with a record and no file at all (a client created without a folder) gets a skeleton
  built here, from the platform's defaults.

The result is what `read_wiring` returns, so everything downstream (validation, merge with the
published settings, readiness) is unchanged.
"""

from pathlib import Path
from typing import Any

from sales_app.client_records import ClientRecord, ClientRecordStore
from sales_app.config.resolution import CONFIG_FILENAME, read_wiring
from sales_app.config.schema import PLACEHOLDERS
from sales_app.config_store.models import CLIENT_ID_PATTERN


def skeleton_wiring(
    client_id: str,
    display_name: str,
    *,
    enabled: bool,
    engine: str | None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """A new client's wiring: placeholders stand in for what is not given yet (Sheet, model), and the
    engine is the platform's. Nothing here is written to disk by this function."""
    return {
        "client_id": client_id,
        "display_name": display_name,
        "enabled": enabled,
        "mode": "dry_run",
        "timezone": "UTC",
        "daily_action_limit": 20,
        "max_attempts": 3,
        "opt_out_keywords": ["stop"],
        "brain": {"agent": engine, "settings": {}}
        if engine
        else {"factory": "TODO", "settings": {}},
        "sheets": {
            "provider": "google_sheets",
            "spreadsheet_id": "TODO",
            "prospects_tab": "Prospects",
            "actions_tab": "Actions",
            "messages_tab": "Messages",
            "log_tab": "Log",
            "column_mapping": {},
            "credentials_env": "GOOGLE_APPLICATION_CREDENTIALS",
        },
        "llm": {
            "provider": provider or "openai",
            "api_key_env": "OPENAI_API_KEY",
            "model": model or "TODO",
        },
        # No credential names: Meta's values come from the credential store.
        "whatsapp": {"provider": "whatsapp"},
        "schedule": {
            "daily_at": "09:00",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
            "prepare_every_minutes": 60,
            "dispatch_every_minutes": 5,
            "recover_every_minutes": 15,
        },
    }


def _brain_unassigned(brain: object) -> bool:
    """The same test as `loader.brain_unassigned` (not imported: the loader imports this module)."""
    if not isinstance(brain, dict):
        return True
    if brain.get("agent"):
        return False
    factory = brain.get("factory")
    return isinstance(factory, str) and factory.strip().lower() in PLACEHOLDERS


def overlay_record(raw: dict[str, Any], record: ClientRecord) -> dict[str, Any]:
    """`raw` with the record's decisions laid over it. A field the record leaves None is the file's."""
    wiring = dict(raw)
    if record.enabled is not None:
        wiring["enabled"] = record.enabled
    if record.display_name:
        wiring["display_name"] = record.display_name
    if record.engine and _brain_unassigned(wiring.get("brain")):
        brain = wiring.get("brain")
        settings = brain.get("settings", {}) if isinstance(brain, dict) else {}
        wiring["brain"] = {"agent": record.engine, "settings": settings}
    return wiring


def wiring_for(
    clients_dir: Path | None, client_id: str, records: ClientRecordStore | None
) -> tuple[dict[str, Any] | None, str | None]:
    """`read_wiring`'s answer, with the client's record taken into account. `clients_dir` None: no
    client file is read at all (the database alone)."""
    record = (
        records.get(client_id)
        if records is not None and CLIENT_ID_PATTERN.match(client_id)
        else None
    )
    if clients_dir is None:
        if record is None:
            return None, f"{client_id}: unknown client"
        return (
            skeleton_wiring(
                client_id,
                record.display_name or client_id,
                enabled=bool(record.enabled),
                engine=record.engine,
            ),
            None,
        )
    directory = clients_dir / client_id
    if record is None:
        return read_wiring(directory)
    if not (directory / CONFIG_FILENAME).is_file():
        return (
            skeleton_wiring(
                client_id,
                record.display_name or client_id,
                enabled=bool(record.enabled),
                engine=record.engine,
            ),
            None,
        )
    raw, problem = read_wiring(directory)
    return (overlay_record(raw, record) if raw is not None else None), problem


def known_client_ids(clients_dir: Path | None, records: ClientRecordStore | None) -> list[str]:
    """Every client: the ones with a record that is complete enough to run without a file (a name
    and a status), and the historical directories."""
    ids = {
        p.name
        for p in (clients_dir.iterdir() if clients_dir is not None and clients_dir.is_dir() else ())
        if p.is_dir()
        and CLIENT_ID_PATTERN.match(p.name)
        and (p / CONFIG_FILENAME).is_file()
        and not p.name.startswith(("_", "."))
    }
    if records is not None:
        ids |= {
            r.client_id
            for r in records.list()
            if r.display_name and r.enabled is not None and CLIENT_ID_PATTERN.match(r.client_id)
        }
    return sorted(ids)
