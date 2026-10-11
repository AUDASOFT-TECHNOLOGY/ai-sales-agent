"""The consent registry: who must never be contacted, kept as keyed digests in a signed chain.

See `registry` for what it guarantees and what it does not, `admin` for the only way to lift a
refusal, and `recorder` for how a source's refusals reach it.
"""

from sales_app.sourcing.consent.admin import (
    CONFIRMATION_PHRASE,
    RELEASE_QUOTA,
    CheckResult,
    ConsentAdmin,
    IdentityState,
    ReleaseResult,
)
from sales_app.sourcing.consent.anchor import Anchor, ConsentSecrets, consent_scope
from sales_app.sourcing.consent.errors import (
    ConsentConflictError,
    ConsentError,
    ConsentFormatError,
    ConsentKeyError,
    ConsentLockTimeoutError,
    ConsentLogUnavailableError,
    ConsentNotAllowedError,
    ConsentRefusedError,
    ConsentStoreUnavailableError,
    ConsentUnavailableError,
)
from sales_app.sourcing.consent.events import (
    ConsentEvent,
    EventType,
    Origin,
    ReasonCode,
    verify_chain,
)
from sales_app.sourcing.consent.keys import ConsentKeys, new_root_key
from sales_app.sourcing.consent.log import ConsentLog, FileConsentLog, InMemoryConsentLog
from sales_app.sourcing.consent.recorder import record_refusals
from sales_app.sourcing.consent.registry import (
    ConsentRegistry,
    ConsentWriter,
    Health,
    InitResult,
    RegistrySnapshot,
    Report,
)

__all__ = [
    "CONFIRMATION_PHRASE",
    "RELEASE_QUOTA",
    "Anchor",
    "CheckResult",
    "ConsentAdmin",
    "ConsentConflictError",
    "ConsentError",
    "ConsentEvent",
    "ConsentFormatError",
    "ConsentKeyError",
    "ConsentKeys",
    "ConsentLockTimeoutError",
    "ConsentLog",
    "ConsentLogUnavailableError",
    "ConsentNotAllowedError",
    "ConsentRefusedError",
    "ConsentRegistry",
    "ConsentSecrets",
    "ConsentStoreUnavailableError",
    "ConsentUnavailableError",
    "ConsentWriter",
    "EventType",
    "FileConsentLog",
    "Health",
    "IdentityState",
    "InMemoryConsentLog",
    "InitResult",
    "Origin",
    "ReasonCode",
    "RegistrySnapshot",
    "ReleaseResult",
    "Report",
    "consent_scope",
    "new_root_key",
    "record_refusals",
    "verify_chain",
]
