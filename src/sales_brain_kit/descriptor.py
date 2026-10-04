"""What a Brain package declares about itself, so the application can find it by key.

A Brain package exposes one `BrainDescriptor` and names it in the `revansa.brains` entry-point
group of its own `pyproject.toml`. The application discovers installed Brains from those entry
points; it never imports a Brain from a path written in a client's configuration. The descriptor is
data only: it holds the Brain's factory and, if the Brain has settings rules, its settings spec.
"""

from dataclasses import dataclass

from sales_core.ports import BrainFactory, BrainSettingsSpec

ENTRY_POINT_GROUP = "revansa.brains"


@dataclass(frozen=True, slots=True)
class BrainDescriptor:
    key: str  # the catalogue key a client names in `brain.agent`; equals the entry point's name
    factory: BrainFactory
    settings_spec: BrainSettingsSpec | None = None  # None: the Brain keeps opaque settings
