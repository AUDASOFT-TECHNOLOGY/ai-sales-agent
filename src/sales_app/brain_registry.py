"""The Brains installed in this environment, found by key through package entry points.

A client names a Brain by key (`brain.agent: <key>`). The key is looked up here, in the registry
built from the `revansa.brains` entry-point group; nothing a client writes is ever turned into a
Python path or imported. A Brain package declares a descriptor (see `sales_brain_kit.BrainDescriptor`)
and names it in its own `pyproject.toml`:

    [project.entry-points."revansa.brains"]
    <key> = "<package>.brain:BRAIN"

`sales_app` does not import the kit: a descriptor is checked by its shape (`key`, `factory`,
`settings_spec`), so a Brain package depends on the kit and the Core only.

Discovery never raises for a bad Brain package. A broken entry point, a descriptor of the wrong
shape, a key that does not match the entry point's name, or the same key declared twice, leaves
that key *unusable* with the reason recorded; the other Brains still work. Asking for such a key
reports the reason, and asking for a key nobody declared lists the installed ones.

The registry is a plain object, so a test or an operator can build one from fake entry points.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from typing import Any, Protocol

from sales_core.ports import Brain, BrainFactory, BrainSettingsSpec, LLMProvider

ENTRY_POINT_GROUP = "revansa.brains"
BRAIN_KEY_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"
_KEY = re.compile(BRAIN_KEY_PATTERN)


class BrainLoadError(Exception):
    """The configured Brain cannot be found, built, or does not implement the Brain port."""


class BrainNotInstalledError(BrainLoadError):
    """No installed Brain has this key."""


class EntryPointLike(Protocol):
    """The part of `importlib.metadata.EntryPoint` the registry reads (fakes implement it too)."""

    name: str
    value: str

    def load(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class InstalledBrain:
    key: str
    factory: BrainFactory
    settings_spec: BrainSettingsSpec | None
    entry_point: str  # "package.module:attribute", for diagnostics


class BrainRegistry:
    def __init__(
        self,
        brains: Iterable[InstalledBrain] = (),
        unusable: Mapping[str, str] | None = None,
    ) -> None:
        self._brains: dict[str, InstalledBrain] = {}
        for brain in brains:
            if not _KEY.match(brain.key):
                raise ValueError(f"{brain.key!r} is not a valid Brain key")
            if brain.key in self._brains:
                raise ValueError(f"Brain {brain.key!r} is registered twice")
            self._brains[brain.key] = brain
        self._unusable = dict(unusable or {})

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._brains))

    def problems(self) -> dict[str, str]:
        """Entry points that could not be used, by the name they declared."""
        return dict(self._unusable)

    def find(self, key: str) -> InstalledBrain | None:
        return self._brains.get(key)

    def get(self, key: str) -> InstalledBrain:
        brain = self._brains.get(key)
        if brain is not None:
            return brain
        reason = self._unusable.get(key)
        if reason is not None:
            raise BrainLoadError(f"Brain {key!r} is installed but unusable: {reason}")
        installed = ", ".join(self.keys()) or "none"
        raise BrainNotInstalledError(f"no Brain {key!r} is installed (installed: {installed})")

    def problem_for(self, key: str) -> str | None:
        """Why `key` cannot be used, or `None` if it can."""
        try:
            self.get(key)
        except BrainLoadError as error:
            return str(error)
        return None

    def create(self, key: str, llm: LLMProvider, settings: Mapping[str, object]) -> Brain:
        """Build the Brain installed under `key`, and check it implements the Brain port."""
        installed = self.get(key)
        try:
            brain = installed.factory(llm, settings)
        except Exception as error:
            raise BrainLoadError(
                f"Brain {key!r} ({installed.entry_point}) failed while building: {error}"
            ) from error
        if not isinstance(brain, Brain):
            raise BrainLoadError(
                f"Brain {key!r} returned {type(brain).__name__}, which has no decide() method"
            )
        return brain


class _Unusable(Exception):
    pass


def discover_brains(entry_points: Iterable[EntryPointLike] | None = None) -> BrainRegistry:
    """The registry of the entry points given, or of those installed in this environment."""
    if entry_points is None:
        entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
    declared: dict[str, list[EntryPointLike]] = {}
    for entry_point in entry_points:
        declared.setdefault(entry_point.name, []).append(entry_point)

    brains: list[InstalledBrain] = []
    unusable: dict[str, str] = {}
    for name, found in declared.items():
        distinct = {e.value: e for e in found}
        if len(distinct) > 1:
            where = ", ".join(sorted(distinct))
            unusable[name] = f"declared more than once ({where})"
            continue
        try:
            brains.append(_installed(next(iter(distinct.values()))))
        except _Unusable as error:
            unusable[name] = str(error)
    return BrainRegistry(brains, unusable)


@lru_cache(maxsize=1)
def installed_brains() -> BrainRegistry:
    """The Brains installed in this environment (discovered once per process)."""
    return discover_brains()


def _installed(entry_point: EntryPointLike) -> InstalledBrain:
    name = entry_point.name
    if not _KEY.match(name):
        raise _Unusable(f"{name!r} is not a valid Brain key")
    try:
        descriptor = entry_point.load()
    except Exception as error:
        raise _Unusable(f"cannot load {entry_point.value!r}: {error}") from error
    key = getattr(descriptor, "key", None)
    factory = getattr(descriptor, "factory", None)
    spec = getattr(descriptor, "settings_spec", None)
    if not isinstance(key, str) or not callable(factory):
        raise _Unusable(f"{entry_point.value!r} is not a Brain descriptor (key, factory)")
    if key != name:
        raise _Unusable(f"{entry_point.value!r} declares key {key!r}, not {name!r}")
    if spec is not None and not isinstance(spec, BrainSettingsSpec):
        raise _Unusable(f"{entry_point.value!r}: settings_spec is not a BrainSettingsSpec")
    return InstalledBrain(key, factory, spec, entry_point.value)
