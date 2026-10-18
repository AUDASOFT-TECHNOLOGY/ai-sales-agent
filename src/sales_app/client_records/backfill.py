"""Filling the clients' records from their historical `client.yaml`, without losing anything.

`plan_backfill` only reads: it says, for every client directory, which fields of its record would be
filled, which already hold the right value, and which hold a *different* one (the database is kept:
a value someone chose there is never overwritten by a file). `apply_backfill` writes exactly the
planned fills. Running it twice changes nothing the second time. The files are never modified, and
the engine is never touched: a client whose file names its own Brain keeps that binding.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sales_app.client_records import ClientRecord, ClientRecordStore
from sales_app.config.resolution import CONFIG_FILENAME, read_wiring
from sales_app.config_store.models import CLIENT_ID_PATTERN

MAX_NAME = 80  # the width of the `display_name` column


@dataclass(frozen=True, slots=True)
class Change:
    client_id: str
    field: str
    action: str  # "fill" | "unchanged" | "conflict" | "skip"
    old: object = None
    new: object = None
    note: str = ""


@dataclass(slots=True)
class BackfillReport:
    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def fills(self) -> list[Change]:
        return [c for c in self.changes if c.action == "fill"]


def _directories(clients_dir: Path) -> list[str]:
    if not clients_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in clients_dir.iterdir()
        if p.is_dir()
        and CLIENT_ID_PATTERN.match(p.name)
        and not p.name.startswith(("_", "."))
        and (p / CONFIG_FILENAME).is_file()
    )


def plan_backfill(clients_dir: Path, records: ClientRecordStore) -> BackfillReport:
    report = BackfillReport()
    for client_id in _directories(clients_dir):
        raw, problem = read_wiring(clients_dir / client_id)
        if raw is None:
            report.problems.append(problem or f"{client_id}: cannot read {CONFIG_FILENAME}")
            continue
        record = records.get(client_id) or ClientRecord(client_id)
        name = raw.get("display_name")
        name = name.strip() if isinstance(name, str) and name.strip() else None
        wanted = {"enabled": raw.get("enabled", True) is not False}
        if name is None:
            report.changes.append(Change(client_id, "display_name", "skip", note="no name in file"))
        elif len(name) > MAX_NAME:
            report.changes.append(
                Change(client_id, "display_name", "skip", note=f"longer than {MAX_NAME}")
            )
        else:
            wanted["display_name"] = name
        for fieldname, new in wanted.items():
            old = getattr(record, fieldname)
            if old is None:
                report.changes.append(Change(client_id, fieldname, "fill", None, new))
            elif old == new:
                report.changes.append(Change(client_id, fieldname, "unchanged", old, new))
            else:
                report.changes.append(
                    Change(
                        client_id,
                        fieldname,
                        "conflict",
                        old,
                        new,
                        "the database value is kept",
                    )
                )
        brain = raw.get("brain")
        named = brain.get("agent") if isinstance(brain, dict) else None
        legacy = brain.get("factory") if isinstance(brain, dict) else None
        if isinstance(named, str) and named:
            if record.engine is None:
                report.changes.append(Change(client_id, "engine", "fill", None, named))
            elif record.engine == named:
                report.changes.append(Change(client_id, "engine", "unchanged", named, named))
            else:
                report.changes.append(
                    Change(
                        client_id,
                        "engine",
                        "conflict",
                        record.engine,
                        named,
                        "the database value is kept",
                    )
                )
        elif record.engine is None and isinstance(legacy, str):
            report.changes.append(
                Change(client_id, "engine", "unchanged", None, None, f"kept: file names {legacy}")
            )
    return report


def apply_backfill(
    records: ClientRecordStore, report: BackfillReport, *, at: datetime, by: str = "backfill"
) -> int:
    """Write the planned fills (one save per client). Returns how many fields were written."""
    per_client: dict[str, dict[str, object]] = {}
    for change in report.fills():
        per_client.setdefault(change.client_id, {})[change.field] = change.new
    for client_id, values in per_client.items():
        records.save(ClientRecord(client_id, **values), at=at, by=by)  # type: ignore[arg-type]
    return len(report.fills())
