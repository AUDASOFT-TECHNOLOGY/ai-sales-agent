"""The registry of provider descriptors: one entry per provider key, whatever its kind."""

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from sales_app.wiring.providers.types import ChannelSupport, ProviderDescriptor, ProviderKind


class DuplicateProviderError(ValueError):
    """A provider key was registered twice."""


class UnknownProviderError(KeyError):
    """No provider is registered under this key."""


class ProviderRegistry:
    """Provider keys are unique across kinds: one key cannot be a model service and a source."""

    def __init__(self, descriptors: Iterable[ProviderDescriptor] = ()) -> None:
        self._by_key: dict[str, ProviderDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: ProviderDescriptor) -> ProviderDescriptor:
        if descriptor.key in self._by_key:
            raise DuplicateProviderError(f"provider {descriptor.key!r} is already registered")
        self._by_key[descriptor.key] = descriptor
        return descriptor

    def get(self, key: str) -> ProviderDescriptor | None:
        return self._by_key.get(key)

    def require(self, key: str) -> ProviderDescriptor:
        descriptor = self._by_key.get(key)
        if descriptor is None:
            raise UnknownProviderError(f"no provider is registered under {key!r}")
        return descriptor

    def list(self, kind: ProviderKind | None = None) -> tuple[ProviderDescriptor, ...]:
        """Every descriptor, or those that can play `kind`, ordered by key."""
        found = (d for d in self._by_key.values() if kind is None or d.plays(kind))
        return tuple(sorted(found, key=lambda d: d.key))

    def register_fake(
        self,
        key: str,
        kind: ProviderKind = ProviderKind.SOURCE,
        *,
        build: Callable[[Any], Any] | None = None,
        channel: ChannelSupport | None = None,
        **overrides: Any,
    ) -> ProviderDescriptor:
        """Register a descriptor for a test double; see `fake_descriptor`."""
        return self.register(fake_descriptor(key, kind, build=build, channel=channel, **overrides))

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __iter__(self) -> Iterator[ProviderDescriptor]:
        return iter(self.list())

    def __len__(self) -> int:
        return len(self._by_key)


def fake_descriptor(
    key: str,
    kind: ProviderKind = ProviderKind.SOURCE,
    *,
    build: Callable[[Any], Any] | None = None,
    channel: ChannelSupport | None = None,
    **overrides: Any,
) -> ProviderDescriptor:
    """A descriptor for a fake provider: only what a test names, everything else a default."""
    label = overrides.pop("label", key)
    return ProviderDescriptor(
        key=key, kind=kind, label=label, build=build, channel=channel, **overrides
    )
