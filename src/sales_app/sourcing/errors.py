"""The errors a prospect source or an operational store may raise.

Adapters translate whatever their vendor library raises into these, so the code that composes them
(and decides what is safe to do next) never sees a vendor type. Every message is a fixed sentence:
never a credential, never a prospect's name, number or address, never what the remote service said.
The conventions of the existing repository port still apply for the two plain cases: `KeyError`
for an unknown prospect and `ValueError` for a duplicate or for an object of another client.
"""


class SourceError(RuntimeError):
    """Base of every failure of a prospect source."""


class SourceUnavailableError(SourceError):
    """The source could not be reached or answered with a server error. Worth retrying later."""


class SourceRateLimitedError(SourceError):
    """The source refused for now. `retry_after` is the seconds it asked to wait, when it said."""

    def __init__(self, message: str = "the source rate limit was reached", *, retry_after=None):
        super().__init__(message)
        self.retry_after: float | None = retry_after


class SourceAuthError(SourceError):
    """The credentials were refused or have expired. Retrying cannot help until they change."""


class SourceReadOnlyError(SourceError):
    """A write was asked of a source that is read-only (the default) or cannot do it."""


class SourceConflictError(SourceError):
    """A value changed in the source since it was read, so nothing was written.

    Names the prospect (by its native id) and the fields, never their values.
    """

    def __init__(self, prospect_id: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__("a value changed in the source since it was read; nothing was written")
        self.prospect_id = prospect_id
        self.fields = fields


class SourceDataError(SourceError):
    """A record of the source cannot be interpreted. Such a record is left out and reported, never
    guessed at (the same fail-closed rule as a Sheets row that cannot be read)."""


class InvalidNativeIdError(SourceDataError):
    """A source identifier outside the accepted alphabet. The message never repeats it."""


class StoreError(RuntimeError):
    """Base of every failure of an operational store."""


class StoreUnavailableError(StoreError):
    """The store could not be reached. Worth retrying later."""


class StateConflictError(StoreError):
    """A prospect's operational state was changed by someone else since it was read, so nothing
    was written. The caller re-reads, recomputes and tries once more."""

    def __init__(self, prospect_id: str) -> None:
        super().__init__("the operational state changed since it was read; nothing was written")
        self.prospect_id = prospect_id
