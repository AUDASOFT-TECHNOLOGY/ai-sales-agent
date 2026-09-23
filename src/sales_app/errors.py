"""Application-level errors."""

from collections.abc import Sequence


class ConfigurationError(Exception):
    """One or more startup problems. Carries every error found, not just the first."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


class RuntimeBuildError(Exception):
    """A client's runtime could not be assembled (bad Brain, unknown provider, adapter failure)."""
