"""The HubSpot provider: prospects kept in a client's own HubSpot account (a source only).

Declared and configurable, but not runnable yet: it has no `build`, so the runtime cannot construct
it and no client can be run on it. A client can choose it, store its Private App access token and
check the connection; the connector that reads prospects comes later. Nothing here calls HubSpot.

One credential, the client's own: its Private App access token. It is a secret of the client's
scope, never shown again, and no two clients may hold the same one (that would be one HubSpot
account serving two clients).
"""

import re

from sales_app.config.secrets import HUBSPOT_ACCESS_TOKEN
from sales_app.secret_store import ScopeKind
from sales_app.wiring.providers.types import (
    CredentialDeclaration,
    EmptySettings,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
    UniquenessRule,
)

KEY = "hubspot"

# What HubSpot issues for a Private App: `pat-<region>-<uuid>` (for instance `pat-eu1-...`).
TOKEN_PATTERN = re.compile(
    r"^pat-[a-z]{2,4}\d{1,2}-[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"
)
TOKEN_RULE = "must be a HubSpot Private App access token (it looks like pat-eu1-...)"

DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.SOURCE,
    label="HubSpot",
    roles=frozenset({ProviderKind.SOURCE}),
    settings_models={ProviderKind.SOURCE: EmptySettings},  # nothing else to choose yet
    credentials=(
        CredentialDeclaration(
            key=HUBSPOT_ACCESS_TOKEN,
            label="HubSpot access token",
            scope=ScopeKind.CLIENT,
            required_when=Requirement.ALWAYS,
            unique=True,
            pattern=TOKEN_PATTERN,
            rule=TOKEN_RULE,
        ),
    ),
    connection_keys=(HUBSPOT_ACCESS_TOKEN,),
    unique_across_clients=(UniquenessRule("credential", HUBSPOT_ACCESS_TOKEN, "HubSpot token"),),
    build=None,  # the connector is a later increment: known and configurable, not runnable
)
