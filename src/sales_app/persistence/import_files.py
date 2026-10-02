"""Copy the data kept in files into the database, without changing a single file.

Safe by construction:

- **Non-destructive**: source files are only read, never modified or deleted.
- **All or nothing**: everything is written in one transaction; any problem rolls it all back.
- **Idempotent**: a record already in the database with identical content is left alone; one with
  different content is a reported conflict, never overwritten. Running it twice changes nothing.
- **Checked before and after**: duplicates and unknown references are detected before anything is
  written, configuration checksums are verified while reading, and every imported record is read
  back from the database and compared with its source before the transaction is committed.

Sessions are not imported: they last hours, and losing them only asks people to log in again.
`dry_run=True` does everything except the commit, which also makes it a way to verify a past import
(a complete one reports every record as unchanged).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from sales_app.admin_auth.models import AdminStoreError, AdminUser
from sales_app.admin_auth.user_store import FileAdminUserStore
from sales_app.client_profiles.models import ClientProfile, ClientProfileError
from sales_app.client_profiles.store import FileClientProfileStore
from sales_app.config_store.file_store import FileConfigStore
from sales_app.config_store.models import (
    CLIENT_ID_PATTERN,
    ConfigStoreError,
    ConfigVersion,
)
from sales_app.persistence.admin_stores import insert_user, read_user
from sales_app.persistence.client_stores import insert_profile, read_profile, whatsapp_digits
from sales_app.persistence.clients import client_directories, ensure_client
from sales_app.persistence.sql_config_store import insert_version, read_versions

_TABLES = ("clients", "admin_users", "client_profiles", "config_versions")


@dataclass
class ImportReport:
    dry_run: bool
    # table -> {"source": n, "inserted": n, "unchanged": n}
    counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: {t: {"source": 0, "inserted": 0, "unchanged": 0} for t in _TABLES}
    )
    errors: list[str] = field(default_factory=list)
    committed: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _Source:
    client_ids: dict[str, tuple[datetime | None, str | None]]
    users: list[AdminUser]
    profiles: list[ClientProfile]
    versions: dict[str, list[ConfigVersion]]


def import_file_data(
    engine: Engine,
    *,
    clients_dir: Path,
    admin_dir: Path,
    profiles_dir: Path,
    config_dir: Path,
    now: datetime,
    dry_run: bool = False,
) -> ImportReport:
    report = ImportReport(dry_run=dry_run)
    source = _read_sources(report, clients_dir, admin_dir, profiles_dir, config_dir, now)
    if source is not None:
        _check_sources(report, source)
    if not report.ok or source is None:
        return report
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                _apply(connection, source, report, now)
                if report.ok and not dry_run:
                    transaction.commit()
                    report.committed = True
                else:
                    transaction.rollback()
            except BaseException:
                transaction.rollback()
                raise
    except (SQLAlchemyError, ValueError) as error:
        report.errors.append(f"database error, nothing was imported: {error}")
        report.committed = False
    return report


# ---- reading the files ---------------------------------------------------------------------


def _read_sources(
    report: ImportReport,
    clients_dir: Path,
    admin_dir: Path,
    profiles_dir: Path,
    config_dir: Path,
    now: datetime,
) -> _Source | None:
    try:
        users = list(FileAdminUserStore(admin_dir / "users.json").list())
        profiles = list(FileClientProfileStore(profiles_dir / "profiles.json").list())
        versions = _read_versions(config_dir)
    except (AdminStoreError, ClientProfileError, ConfigStoreError) as error:
        # A corrupt or hand-edited file (including a configuration whose checksum no longer
        # matches) is reported, never guessed at.
        report.errors.append(f"cannot read the source files: {error}")
        return None
    client_ids: dict[str, tuple[datetime | None, str | None]] = {}
    for client_id in client_directories(clients_dir):
        client_ids[client_id] = (None, "import")
    for profile in profiles:
        client_ids[profile.client_id] = (profile.created_at, profile.created_by)
    for client_id, history in versions.items():
        client_ids.setdefault(client_id, (history[0].created_at, history[0].author))
    return _Source(client_ids, users, profiles, versions)


def _read_versions(config_dir: Path) -> dict[str, list[ConfigVersion]]:
    store = FileConfigStore(config_dir)
    found: dict[str, list[ConfigVersion]] = {}
    if not config_dir.is_dir():
        return found
    for entry in sorted(config_dir.iterdir()):
        if entry.is_dir() and CLIENT_ID_PATTERN.match(entry.name):
            history = list(store.history(entry.name))  # verifies numbering, parents and checksums
            if history:
                found[entry.name] = history
    return found


def _check_sources(report: ImportReport, source: _Source) -> None:
    """Everything that would make the import lose or merge data, found before writing anything."""
    for label, values in (
        ("admin username", [u.username for u in source.users]),
        ("admin id", [u.id for u in source.users]),
        ("client login", [p.login for p in source.profiles]),
        (
            "WhatsApp number",
            [d for p in source.profiles if (d := whatsapp_digits(p.whatsapp_number))],
        ),
    ):
        for value in sorted({v for v in values if values.count(v) > 1}):
            report.errors.append(f"duplicate {label} in the source files: {value!r}")
    for user in source.users:
        for client_id in sorted(user.client_ids - source.client_ids.keys()):
            report.errors.append(
                f"admin {user.username!r} is scoped to {client_id!r}, which is not a known client "
                "(no clients/<id>/client.yaml, profile or settings): create it or fix the account"
            )
    for client_id in source.client_ids:
        if not re.fullmatch(CLIENT_ID_PATTERN.pattern, client_id):
            report.errors.append(f"invalid client id {client_id!r}")


# ---- writing and verifying -------------------------------------------------------------------


def _apply(connection, source: _Source, report: ImportReport, now: datetime) -> None:
    counts = report.counts
    counts["clients"]["source"] = len(source.client_ids)
    for client_id, (created_at, created_by) in sorted(source.client_ids.items()):
        created = ensure_client(
            connection, client_id, created_at=created_at or now, created_by=created_by
        )
        counts["clients"]["inserted" if created else "unchanged"] += 1

    counts["admin_users"]["source"] = len(source.users)
    for user in source.users:
        _import_record(
            report,
            "admin_users",
            f"admin account {user.username!r}",
            existing=read_user(connection, admin_id=user.id)
            or read_user(connection, username=user.username),
            wanted=user,
            insert=lambda user=user: insert_user(connection, user),
            read_back=lambda user=user: read_user(connection, admin_id=user.id),
        )

    counts["client_profiles"]["source"] = len(source.profiles)
    for profile in source.profiles:
        _import_record(
            report,
            "client_profiles",
            f"profile of client {profile.client_id!r}",
            existing=read_profile(connection, profile.client_id),
            wanted=profile,
            insert=lambda profile=profile: insert_profile(connection, profile),
            read_back=lambda profile=profile: read_profile(connection, profile.client_id),
        )

    for client_id, history in sorted(source.versions.items()):
        counts["config_versions"]["source"] += len(history)
        stored = {v.version: v for v in read_versions(connection, client_id)}
        for record in history:
            current = stored.get(record.version)
            if current is not None:
                _compare(
                    report, "config_versions", f"{client_id} v{record.version}", current, record
                )
                continue
            insert_version(connection, record)
            counts["config_versions"]["inserted"] += 1
        after = read_versions(connection, client_id)
        if after[: len(history)] != history:
            report.errors.append(f"settings of {client_id!r} differ from the source after import")


def _import_record(report, table: str, label: str, *, existing, wanted, insert, read_back) -> None:
    if existing is not None:
        _compare(report, table, label, existing, wanted)
        return
    try:
        insert()
    except ValueError as error:
        report.errors.append(f"{label}: {error}")
        return
    report.counts[table]["inserted"] += 1
    if read_back() != wanted:
        report.errors.append(f"{label} differs from its source after import")


def _compare(report: ImportReport, table: str, label: str, stored, wanted) -> None:
    if stored == wanted:
        report.counts[table]["unchanged"] += 1
    else:
        report.errors.append(
            f"conflict: {label} already exists in the database with different content "
            "(left untouched)"
        )
