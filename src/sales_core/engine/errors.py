"""Engine-level errors."""


class InvalidDecisionError(ValueError):
    """A Brain returned a decision that is well-formed but cannot be applied."""


class ProposalNotFoundError(LookupError):
    """No action with this id awaits approval (unknown, or already decided, sent or cancelled)."""


class ProposalNotApprovableError(ValueError):
    """The proposal is still waiting, but approving it now would be unsafe (the person cannot be
    contacted any more, or the draft is gone). Rejecting it remains possible."""


class ControlUnavailableError(RuntimeError):
    """The Engine was built without a conversation-control store, so nobody can take a
    conversation over (nothing automatic is ever blocked by mistake in that case either)."""


class ManualSendError(Exception):
    """A message written by a person was not sent. `code` is stable, `message` is a sentence that
    can be shown as it is (it holds no address, no credential and no message text).

    Codes: `empty`, `dry_run`, `control_unavailable`, `unknown_prospect`, `not_in_human_mode`,
    `opted_out`, `no_channel`, `no_contact`, `not_linked`, `not_now` (the channel says wait, e.g.
    outside WhatsApp's 24-hour window with no approved template), `send_failed`, `unconfirmed`
    (outcome unknown: do not retry blindly)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"
