"""Port implemented by every Business Brain (one per client)."""

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from sales_core.contracts import BrainDecision, BrainRequest
from sales_core.ports.llm import LLMProvider


@runtime_checkable
class Brain(Protocol):
    """Decides what to do for one prospect. Client knowledge lives entirely behind this call.

    A Brain receives its collaborators (e.g. an `LLMProvider`) at construction time, never
    through the request, so the Core stays independent of how a Brain reaches its model.
    """

    def decide(self, request: BrainRequest) -> BrainDecision: ...


# Entry point every Brain package exposes (named by `brain.factory` in client configuration).
# It receives the client's own LLM provider and its opaque Brain settings.
type BrainFactory = Callable[[LLMProvider, Mapping[str, object]], Brain]


@runtime_checkable
class BrainSettingsSpec(Protocol):
    """What a Brain family says about the settings it understands.

    A Brain package may expose one as the module attribute `SETTINGS_SPEC`, next to its factory.
    The application uses it to validate settings before they become a stored version and when
    they are loaded, and a future admin interface uses `json_schema()` to build forms without
    knowing anything about the Brain. The Core never interprets settings: it only defines this
    shape. A Brain without a spec keeps opaque settings.
    """

    @property
    def family(self) -> str:
        """Name of the Brain family whose settings this describes."""
        ...

    @property
    def schema_version(self) -> int:
        """The newest settings version this spec produces (`prepare` returns this version)."""
        ...

    def json_schema(self) -> Mapping[str, object]:
        """JSON Schema of the latest settings, for tools that render forms."""
        ...

    def validate(self, settings: Mapping[str, object], version: int) -> Sequence[str]:
        """Every problem with `settings` written for `version`, or an empty sequence. Never raises
        for bad settings."""
        ...

    def prepare(self, settings: Mapping[str, object], version: int) -> Mapping[str, object]:
        """Valid settings brought to the latest version, with defaults written out. Raises
        `ValueError` if the settings are invalid."""
        ...
