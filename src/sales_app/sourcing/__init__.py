"""Prospect sources and operational stores: the contracts and the pure rules around them.

Nothing here talks to a provider or to the Engine. See `ports` for the two contracts, `identifiers`
for how ids are named, `contacts` for how a contact is recognised, `statuses` for which status
wins and `merge` for how a prospect is put back together.
"""

from sales_app.sourcing.contacts import (
    contact_keys,
    lookup_keys,
    normalize_email,
    normalize_phone,
    same_contact,
)
from sales_app.sourcing.errors import (
    InvalidNativeIdError,
    SourceAuthError,
    SourceConflictError,
    SourceDataError,
    SourceError,
    SourceRateLimitedError,
    SourceReadOnlyError,
    SourceUnavailableError,
    StateConflictError,
    StoreError,
    StoreUnavailableError,
)
from sales_app.sourcing.fields import (
    OVERLAY_FIELDS,
    SOURCE_ONLY_FIELDS,
    ProspectField,
    changed_fields,
    field_value,
)
from sales_app.sourcing.identifiers import (
    LOCAL_ID_PREFIX,
    is_local_id,
    local_id_problem,
    native_id_problem,
    to_native_id,
    to_public_id,
)
from sales_app.sourcing.merge import merge_prospect
from sales_app.sourcing.ports import (
    OperationalStore,
    ProspectChange,
    ProspectSource,
    ProspectState,
    SourceCapabilities,
)
from sales_app.sourcing.problems import ProblemCode, SourceProblem
from sales_app.sourcing.statuses import (
    CONTACTABLE,
    MAPPABLE,
    StatusMapping,
    StatusMappingError,
    StatusResolution,
    effective_status,
    is_restrictive,
    lowers_restriction,
    resolve_source_status,
    severity,
)

__all__ = [
    "CONTACTABLE",
    "LOCAL_ID_PREFIX",
    "MAPPABLE",
    "OVERLAY_FIELDS",
    "SOURCE_ONLY_FIELDS",
    "InvalidNativeIdError",
    "OperationalStore",
    "ProblemCode",
    "ProspectChange",
    "ProspectField",
    "ProspectSource",
    "ProspectState",
    "SourceAuthError",
    "SourceCapabilities",
    "SourceConflictError",
    "SourceDataError",
    "SourceError",
    "SourceProblem",
    "SourceRateLimitedError",
    "SourceReadOnlyError",
    "SourceUnavailableError",
    "StateConflictError",
    "StatusMapping",
    "StatusMappingError",
    "StatusResolution",
    "StoreError",
    "StoreUnavailableError",
    "changed_fields",
    "contact_keys",
    "effective_status",
    "field_value",
    "is_local_id",
    "is_restrictive",
    "local_id_problem",
    "lookup_keys",
    "lowers_restriction",
    "merge_prospect",
    "native_id_problem",
    "normalize_email",
    "normalize_phone",
    "resolve_source_status",
    "same_contact",
    "severity",
    "to_native_id",
    "to_public_id",
]
