"""Brain modules whose SETTINGS_SPEC is wrong: not a spec at all, or one that fails."""

from app_test_brains import good_brain as create_brain  # noqa: F401

SETTINGS_SPEC = "this is not a spec"
