"""Errors of the consent registry. Every message is a fixed sentence.

No message, and no attribute of any of these errors, ever holds a phone number, an e-mail address,
a key or a MAC: only fixed reason codes. They are safe to print and to log.
"""


class ConsentError(RuntimeError):
    """Base of every failure of the consent registry."""


class ConsentFormatError(ConsentError):
    """A stored record is not in the exact format the registry writes."""


class ConsentKeyError(ConsentError):
    """The registry's root key is malformed."""


class ConsentLogUnavailableError(ConsentError):
    """The event log could not be read or written."""


class ConsentLockTimeoutError(ConsentLogUnavailableError):
    """The log is locked by another process and the wait ran out."""


class ConsentConflictError(ConsentError):
    """The log moved on since it was read; nothing was written."""


class ConsentStoreUnavailableError(ConsentError):
    """The secret store holding the key and the anchor cannot be used."""


class ConsentUnavailableError(ConsentError):
    """The registry is not healthy, so nothing can be recorded. Everything stays blocked.

    `health` and `reason` are fixed codes (see `registry.Health`)."""

    def __init__(self, health: str, reason: str) -> None:
        super().__init__("the consent registry is not healthy; every contact stays blocked")
        self.health = health
        self.reason = reason


class ConsentRefusedError(ConsentError):
    """The registry exists in a state where a new one must not be created over it."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            "a consent registry already exists and is not healthy; "
            "refusing to create a new key over it"
        )
        self.reason = reason


NOT_ALLOWED_MESSAGES = {
    "not_authorised": "not authorised: an active owner account and its password are required",
    "bad_confirmation": "the confirmation phrase does not match; nothing was changed",
    "invalid_reason": "that reason code cannot be used for this action",
    "evidence_required": "an evidence reference is required (letters, digits and . _ : / # - only)",
    "quota_exceeded": "too many releases by this account in the last 24 hours",
    "origin_rule": "this refusal came from the contact's own message: only 'written_consent' "
    "can lift it",
}


class ConsentNotAllowedError(ConsentError):
    """An operator action was refused. `code` is one of `NOT_ALLOWED_MESSAGES`."""

    def __init__(self, code: str) -> None:
        super().__init__(NOT_ALLOWED_MESSAGES[code])
        self.code = code
