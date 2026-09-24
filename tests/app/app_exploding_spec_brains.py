"""A Brain module whose spec raises instead of reporting problems."""

from collections.abc import Mapping, Sequence

from app_test_brains import good_brain as create_brain  # noqa: F401


class ExplodingSpec:
    family = "exploding"
    schema_version = 1

    def json_schema(self) -> Mapping[str, object]:
        return {}

    def validate(self, settings: Mapping[str, object], version: int) -> Sequence[str]:
        raise RuntimeError("boom")

    def prepare(self, settings: Mapping[str, object], version: int) -> Mapping[str, object]:
        raise RuntimeError("boom")


SETTINGS_SPEC = ExplodingSpec()
