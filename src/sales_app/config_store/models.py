"""Versioned configuration records and the errors a store can raise.

A store deals in opaque, JSON-compatible documents. What a document means (its schema, its
guardrails) is decided above the store, so any backend can hold them, from files to database rows.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

CLIENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigStoreError(Exception):
    """A store could not do what was asked."""


class ConfigConflictError(ConfigStoreError):
    """Someone else published first: the caller's view of the latest version is stale."""


class ConfigCorruptError(ConfigStoreError):
    """Stored data is unreadable, edited by hand, or inconsistent. It is never guessed at."""


def check_client_id(client_id: str) -> str:
    if not CLIENT_ID_PATTERN.match(client_id):
        raise ValueError(f"invalid client id {client_id!r}")
    return client_id


def freeze_document(value: Any) -> Any:
    """Deep-immutable copy of a JSON-compatible value. Raises TypeError or ValueError otherwise."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("numbers must be finite")
        return value
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"keys must be text, got {key!r}")
            frozen[key] = freeze_document(item)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(freeze_document(item) for item in value)
    raise TypeError(f"{type(value).__name__} is not JSON-compatible")


def thaw_document(value: Any) -> Any:
    """Plain dicts and lists again, ready for `json.dumps`."""
    if isinstance(value, Mapping):
        return {key: thaw_document(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [thaw_document(item) for item in value]
    return value


def canonical_json(document: Mapping[str, Any]) -> str:
    return json.dumps(
        thaw_document(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def document_checksum(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfigVersion:
    """One immutable version of one client's settings. Versions are numbered 1, 2, 3, ..."""

    client_id: str
    version: int
    document: Mapping[str, Any]
    created_at: datetime
    author: str
    note: str = ""
    parent: int | None = None

    def __post_init__(self) -> None:
        check_client_id(self.client_id)
        if self.version < 1:
            raise ValueError("version must be at least 1")
        expected_parent = self.version - 1 or None
        if self.parent != expected_parent:
            raise ValueError(f"parent of version {self.version} must be {expected_parent}")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        object.__setattr__(self, "document", freeze_document(dict(self.document)))
