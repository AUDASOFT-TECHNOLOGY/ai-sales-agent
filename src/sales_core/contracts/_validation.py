"""Small invariant helpers shared by the contract dataclasses (structure checks, not business rules)."""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType


def require_text(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def require_aware(value: datetime | None, name: str) -> None:
    """Datetimes crossing a contract boundary must be timezone-aware."""
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware")


def freeze_mapping[K, V](value: Mapping[K, V]) -> Mapping[K, V]:
    """Read-only defensive copy, so frozen dataclasses stay effectively immutable."""
    return MappingProxyType(dict(value))
