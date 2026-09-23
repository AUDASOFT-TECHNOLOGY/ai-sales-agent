"""Secrets resolved from environment variables. Config files hold only the variable names."""

from collections.abc import Mapping


class Secrets:
    """The environment values one client refers to. Never prints its contents."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str) -> str:
        return self._values[name]

    def get_optional(self, name: str) -> str | None:
        return self._values.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._values

    def __repr__(self) -> str:
        return f"Secrets(<{len(self._values)} values redacted>)"

    __str__ = __repr__
