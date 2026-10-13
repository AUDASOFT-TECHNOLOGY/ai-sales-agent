"""Engine-level errors."""


class InvalidDecisionError(ValueError):
    """A Brain returned a decision that is well-formed but cannot be applied."""


class ProposalNotFoundError(LookupError):
    """No action with this id awaits approval (unknown, or already decided, sent or cancelled)."""


class ProposalNotApprovableError(ValueError):
    """The proposal is still waiting, but approving it now would be unsafe (the person cannot be
    contacted any more, or the draft is gone). Rejecting it remains possible."""


def describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"
