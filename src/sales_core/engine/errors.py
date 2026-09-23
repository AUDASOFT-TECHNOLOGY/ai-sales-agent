"""Engine-level errors."""


class InvalidDecisionError(ValueError):
    """A Brain returned a decision that is well-formed but cannot be applied."""


def describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"
