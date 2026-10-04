"""Checking credentials, and what two clients must not share, from what descriptors declare.

Everything here is generic: it reads a `ProviderDescriptor` and applies what it says, and names no
provider. Adding a provider with new credentials, new patterns or a new uniqueness rule changes its
own descriptor and nothing in this module.

- `validate_credentials` checks the values found for one provider against its declarations: a
  credential it does not declare, one found in the wrong scope, one that is required and absent
  (`required_when`), one that breaks its pattern or the general rules for a value, and, when the
  caller can tell who holds a value, one that must be unique and is held elsewhere. The messages
  describe the rule and never carry the value.
- `shared_values` applies the uniqueness rules across clients, from claims the caller gathers
  (there is no database of clients here, and nothing is stored).
- `legacy_specs` gives the declarations in the shape of the credential catalog's `CredentialSpec`:
  the catalog is kept for compatibility and a test holds the two equal.

The rules for a single value are the catalog's own (`normalize_value`), so a value is refused with
the same words whether it is checked here or saved through the Admin API.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from sales_app.secret_store import CredentialSpec, InvalidCredentialError, ScopeKind
from sales_app.secret_store.catalog import normalize_value
from sales_app.wiring.providers.registry import ProviderRegistry
from sales_app.wiring.providers.types import CredentialDeclaration, ProviderDescriptor, Requirement


class ProblemCode(StrEnum):
    UNKNOWN_PROVIDER = "unknown_provider"
    UNKNOWN_CREDENTIAL = "unknown_credential"
    WRONG_SCOPE = "wrong_scope"
    MISSING = "missing"
    INVALID = "invalid"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class CredentialProblem:
    code: ProblemCode
    key: str | None
    message: str  # describes the rule; never the value


@dataclass(frozen=True, slots=True)
class ProvidedCredential:
    """A value found for a credential, and where it was found.

    `scope` is the credential store scope it came from; `None` means it did not come from the store
    (the environment, a form): a stored credential may always be supplied that way, as the runtime
    allows today, while an environment-only one may never come from the store.
    """

    value: str | None
    scope: ScopeKind | None = None


def is_required(credential: CredentialDeclaration, *, live: bool) -> bool:
    """Whether a client cannot run without it. `live` is False for a dry run, which never talks to
    the outside."""
    if credential.required_when is Requirement.ALWAYS:
        return True
    return credential.required_when is Requirement.LIVE and live


def validate_credentials(
    descriptor: ProviderDescriptor,
    provided: Mapping[str, ProvidedCredential],
    *,
    live: bool = True,
    holder_of: Callable[[str, str], str | None] | None = None,
) -> tuple[CredentialProblem, ...]:
    """Every problem with the values found for one provider; empty when there is none.

    `provided` maps a credential key to what was found (a key with no value counts as absent).
    `holder_of(key, value)` says which other scope already holds a unique value, if the caller can
    tell; without it uniqueness is not checked here.
    """
    problems: list[CredentialProblem] = []
    declared = {credential.key: credential for credential in descriptor.credentials}
    for key in sorted(set(provided) - set(declared)):
        problems.append(
            CredentialProblem(
                ProblemCode.UNKNOWN_CREDENTIAL,
                key,
                f"{key!r} is not a credential of provider {descriptor.key!r}",
            )
        )
    for credential in descriptor.credentials:
        found = provided.get(credential.key)
        if found is None or found.value is None:
            if is_required(credential, live=live):
                problems.append(
                    CredentialProblem(
                        ProblemCode.MISSING, credential.key, f"{credential.label} is not configured"
                    )
                )
            continue
        problems.extend(_check_value(credential, found, holder_of))
    return tuple(problems)


def validate_provider_credentials(
    registry: ProviderRegistry,
    provider: str,
    provided: Mapping[str, ProvidedCredential],
    *,
    live: bool = True,
    holder_of: Callable[[str, str], str | None] | None = None,
) -> tuple[CredentialProblem, ...]:
    descriptor = registry.get(provider)
    if descriptor is None:
        return (
            CredentialProblem(ProblemCode.UNKNOWN_PROVIDER, None, f"unknown provider {provider!r}"),
        )
    return validate_credentials(descriptor, provided, live=live, holder_of=holder_of)


def _check_value(
    credential: CredentialDeclaration,
    found: ProvidedCredential,
    holder_of: Callable[[str, str], str | None] | None,
) -> list[CredentialProblem]:
    if found.scope is not None and found.scope is not credential.scope:
        return [
            CredentialProblem(
                ProblemCode.WRONG_SCOPE,
                credential.key,
                f"{credential.label} belongs to {_scope_words(credential.scope)}, "
                f"not to {_scope_words(found.scope)}",
            )
        ]
    try:
        cleaned = normalize_value(_as_spec(credential), found.value or "")
    except InvalidCredentialError as error:
        return [CredentialProblem(ProblemCode.INVALID, credential.key, str(error))]
    if credential.unique and holder_of is not None:
        holder = holder_of(credential.key, cleaned)
        if holder is not None:
            return [
                CredentialProblem(
                    ProblemCode.DUPLICATE,
                    credential.key,
                    f"{credential.label} is already used by {holder}",
                )
            ]
    return []


def _scope_words(scope: ScopeKind | None) -> str:
    return "the environment only" if scope is None else f"the {scope.value} scope"


def _as_spec(credential: CredentialDeclaration) -> CredentialSpec:
    """The declaration as the catalog's spec, so a value is judged by the catalog's own rules."""
    return CredentialSpec(
        key=credential.key,
        scope_kind=credential.scope if credential.scope is not None else ScopeKind.GLOBAL,
        label=credential.label,
        sensitive=credential.sensitive,
        unique=credential.unique,
        pattern=credential.pattern,
        rule=credential.rule,
    )


def legacy_specs(registry: ProviderRegistry) -> tuple[CredentialSpec, ...]:
    """The stored credentials the registry declares, as the credential catalog's specs.

    A credential only the environment can supply has no catalog entry (nothing is stored for it),
    so it is left out. The result is ordered by provider, then by declaration.
    """
    return tuple(
        _as_spec(credential)
        for descriptor in registry
        for credential in descriptor.credentials
        if credential.scope is not None
    )


# ---- what clients must not share ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Claim:
    """A client's value for something a provider says no two clients may share."""

    client_id: str
    provider: str
    kind: Literal["setting", "credential"]
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class SharedValue:
    provider: str
    kind: Literal["setting", "credential"]
    name: str
    label: str
    value: str
    client_ids: tuple[str, ...]

    @property
    def message(self) -> str:
        return (
            f"{self.label} {self.value!r} is used by several clients: {', '.join(self.client_ids)}"
        )


def shared_values(registry: ProviderRegistry, claims: Iterable[Claim]) -> tuple[SharedValue, ...]:
    """The values claimed by more than one client, under the rules the providers declare.

    A claim for something no descriptor declares unique is ignored, and so is an empty value. Clients
    appear in the order of their first claim.
    """
    rules = {
        (descriptor.key, rule.kind, rule.name): rule
        for descriptor in registry
        for rule in descriptor.unique_across_clients
    }
    owners: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for claim in claims:
        if (claim.provider, claim.kind, claim.name) not in rules or not claim.value:
            continue
        holders = owners[(claim.provider, claim.kind, claim.name, claim.value)]
        if claim.client_id not in holders:
            holders.append(claim.client_id)
    return tuple(
        SharedValue(
            provider=provider,
            kind=kind,  # type: ignore[arg-type]
            name=name,
            label=rules[(provider, kind, name)].label,
            value=value,
            client_ids=tuple(holders),
        )
        for (provider, kind, name, value), holders in sorted(owners.items())
        if len(holders) > 1
    )


def setting_claims(registry: ProviderRegistry, client_id: str, spec: Any) -> tuple[Claim, ...]:
    """The claims a canonical spec (a `ClientSpec`) makes through its providers' settings. Only its
    `source`, `store` and `channels` bindings are read, so nothing of the config package is imported
    here. The credential claims come from resolved credentials, which only the caller has."""
    bindings = [spec.source, spec.store, *spec.channels]
    claims = []
    for binding in bindings:
        descriptor = registry.get(binding.provider) if binding is not None else None
        if descriptor is None:
            continue
        for rule in descriptor.unique_across_clients:
            value = binding.settings.get(rule.name) if rule.kind == "setting" else None
            if isinstance(value, str):
                claims.append(Claim(client_id, descriptor.key, "setting", rule.name, value))
    return tuple(claims)
