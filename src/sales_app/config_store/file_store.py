"""ConfigStore on the local filesystem: one JSON file per version, never modified after writing.

    <root>/<client_id>/versions/000001.json, 000002.json, ...

Each file is an envelope (client, version, parent, time, author, note, checksum) around the
document. A version appears atomically (a fully written temporary file is hard-linked into place,
which fails if the number is taken), so readers never see a partial file and two publishers can
never both claim the same number, even from different processes.
"""

import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sales_app.config_store.models import (
    ConfigConflictError,
    ConfigCorruptError,
    ConfigStoreError,
    ConfigVersion,
    check_client_id,
    document_checksum,
    freeze_document,
    thaw_document,
)

FORMAT = 1
_VERSION_FILE = re.compile(r"^(\d{6,})\.json$")


class FileConfigStore:
    def __init__(self, root: Path, *, now: Callable[[], datetime] | None = None) -> None:
        self._root = Path(root)
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    # ---- reading -------------------------------------------------------------------------

    def current(self, client_id: str) -> ConfigVersion | None:
        numbers = self._numbers(client_id)
        return self._read(client_id, numbers[-1]) if numbers else None

    def get(self, client_id: str, version: int) -> ConfigVersion | None:
        numbers = self._numbers(client_id)
        return self._read(client_id, version) if version in numbers else None

    def history(self, client_id: str) -> Sequence[ConfigVersion]:
        return tuple(self._read(client_id, number) for number in self._numbers(client_id))

    # ---- writing -------------------------------------------------------------------------

    def publish(
        self,
        client_id: str,
        document: Mapping[str, Any],
        *,
        author: str,
        note: str = "",
        expected_parent: int | None,
    ) -> ConfigVersion:
        check_client_id(client_id)
        freeze_document(document)  # refuse anything that is not JSON before touching the disk
        with self._lock:
            numbers = self._numbers(client_id)
            latest = numbers[-1] if numbers else None
            if latest is not None:
                self._read(client_id, latest)  # never append to a history that cannot be trusted
            if latest != expected_parent:
                raise ConfigConflictError(
                    f"{client_id}: latest version is {latest}, expected {expected_parent}"
                )
            record = ConfigVersion(
                client_id=client_id,
                version=(latest or 0) + 1,
                document=document,
                created_at=self._now(),
                author=author,
                note=note,
                parent=latest,
            )
            self._write_new(record)
            return record

    # ---- internals -----------------------------------------------------------------------

    def _directory(self, client_id: str) -> Path:
        return self._root / check_client_id(client_id) / "versions"

    def _path(self, client_id: str, version: int) -> Path:
        return self._directory(client_id) / f"{version:06d}.json"

    def _numbers(self, client_id: str) -> list[int]:
        """Version numbers on disk, ascending. Anything but 1..n without gaps is corruption."""
        directory = self._directory(client_id)
        if not directory.is_dir():
            return []
        try:
            names = [p.name for p in directory.iterdir()]
        except OSError as error:
            raise ConfigCorruptError(f"{directory}: cannot list versions: {error}") from error
        numbers = sorted(int(m.group(1)) for n in names if (m := _VERSION_FILE.match(n)))
        if numbers != list(range(1, len(numbers) + 1)):
            raise ConfigCorruptError(f"{directory}: version files are not numbered 1..n: {numbers}")
        return numbers

    def _read(self, client_id: str, version: int) -> ConfigVersion:
        path = self._path(client_id, version)
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
                raise ValueError("unsupported file format")
            if envelope["client_id"] != client_id or envelope["version"] != version:
                raise ValueError("file does not match its name")
            document = envelope["document"]
            if not isinstance(document, dict):
                raise TypeError("document must be an object")
            if envelope["checksum"] != document_checksum(document):
                raise ValueError("checksum mismatch: the file was modified after publishing")
            return ConfigVersion(
                client_id=client_id,
                version=version,
                document=document,
                created_at=datetime.fromisoformat(envelope["created_at"]),
                author=envelope["author"],
                note=envelope["note"],
                parent=envelope["parent"],
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ConfigCorruptError(f"{path}: {error}") from error

    def _write_new(self, record: ConfigVersion) -> None:
        directory = self._directory(record.client_id)
        target = self._path(record.client_id, record.version)
        envelope = {
            "format": FORMAT,
            "client_id": record.client_id,
            "version": record.version,
            "parent": record.parent,
            "created_at": record.created_at.isoformat(),
            "author": record.author,
            "note": record.note,
            "checksum": document_checksum(record.document),
            "document": thaw_document(record.document),
        }
        temporary = directory / f".tmp-{uuid.uuid4().hex}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(envelope, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target)  # atomic, and refuses to replace an existing version
        except FileExistsError as error:
            raise ConfigConflictError(f"{target} already exists: published concurrently") from error
        except OSError as error:
            raise ConfigStoreError(f"cannot write {target}: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)
