"""What the application knows about one provider: a descriptor.

A provider is a way of reaching something outside Revansa: where prospects are (source), where
Revansa keeps its own state (store), how it talks to prospects (channel) or which model service
runs the Brain (llm). A descriptor gathers, in one place and as data, everything the application
needs to know about one of them: the settings it accepts, the credentials it needs and where they
live, what it can do, what must be unique across clients, and how to build it.

This module is only types: it knows no provider, imports no adapter and nothing of the Core (a
descriptor's `build` is a plain callable). A provider's own module declares its descriptor.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sales_app.model_catalog import ModelDescriptor
from sales_app.secret_store import ScopeKind

PROVIDER_KEY = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_CREDENTIAL_KEY = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class ProviderKind(StrEnum):
    SOURCE = "source"
    STORE = "store"
    CHANNEL = "channel"
    LLM = "llm"


class Product(StrEnum):
    """What a source provider's `build` makes. A `REPOSITORY` plays source and store at once (a
    spreadsheet) and is handed to the Core as it is; a `PROSPECT_SOURCE` only knows where the people
    are, and the runtime composes it with an operational store and the consent registry."""

    REPOSITORY = "repository"
    PROSPECT_SOURCE = "prospect_source"


class Requirement(StrEnum):
    """When a client cannot run without a credential."""

    ALWAYS = "always"
    LIVE = "live"  # unless the client runs in dry run, which never talks to the outside
    OPTIONAL = "optional"


class EmptySettings(BaseModel):
    """The settings model of a provider that takes none."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class CredentialDeclaration:
    """One value a provider needs, by the logical key it is stored under.

    `scope` says where it lives: the credential store's global scope (Revansa's own), its client
    scope (one client's), or `None` for a value that only the environment can supply. `sensitive`
    False marks an identifier or a setting that may be shown to an admin rather than a secret.
    """

    key: str
    label: str
    scope: ScopeKind | None
    sensitive: bool = True
    required_when: Requirement = Requirement.OPTIONAL
    unique: bool = False  # no two scopes may hold the same value
    pattern: re.Pattern[str] | None = None  # the whole (stripped) value must match
    rule: str = ""  # what `pattern` expects, in words

    def __post_init__(self) -> None:
        if not _CREDENTIAL_KEY.match(self.key):
            raise ValueError(f"credential key {self.key!r} must look like 'provider.name'")
        if self.unique and self.scope is None:
            raise ValueError(f"{self.key}: only a stored credential can be unique")


@dataclass(frozen=True, slots=True)
class UniquenessRule:
    """Something two clients must never share: a setting of their configuration, or a credential."""

    kind: Literal["setting", "credential"]
    name: str
    label: str


@dataclass(frozen=True, slots=True)
class ChannelSupport:
    """What a channel provider adds: how its inbound messages are routed and authenticated.

    `kind` is the Core's name for the channel (its string value; the application turns it into the
    Core's own type). The first two callables take a client's resolved configuration; the parser
    builder takes the signing secret of each routing key and the token that answers the provider's
    subscription handshake.
    """

    kind: str
    routing_key: Callable[[Any], str | None]  # which account an inbound event addresses
    webhook_secret: Callable[[Any], str | None]  # what signs inbound requests for that account
    build_parser: Callable[[Mapping[str, str], str | None], Any]


@dataclass(frozen=True, slots=True)
class ModelList:
    """Where the Admin keeps the models of a provider: the credential that holds the list (a JSON
    array of `{name, id, active, key}`), and the older credentials that stand in for it until the
    list is first saved (credential key -> the model's name)."""

    key: str
    legacy_keys: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in (self.key, *self.legacy_keys):
            if not _CREDENTIAL_KEY.match(key):
                raise ValueError(f"model list key {key!r} must look like 'provider.name'")
        object.__setattr__(self, "legacy_keys", MappingProxyType(dict(self.legacy_keys)))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderDescriptor:
    """Everything the application knows about one provider.

    - `kind` is what it mainly is; `roles` lists every role it can play (it defaults to `kind`
      alone). A provider that can host a store beside the source sets `can_host_store`.
    - `family` names the technical adapter family, so two providers may share an adapter while
      staying two providers.
    - `settings_models` give, per role, the model that validates the non-secret settings of a
      client's configuration (what is under `settings` there).
    - `connection_keys` are the credentials that together make a connection to the provider (the
      ones a connection check needs). Some credentials are not among them because they prove
      themselves only when the provider calls Revansa (a webhook secret, a verify token).
    - `models` are the models it serves (key, label); their upstream ids are deployment data, not
      declared here (see `sales_app.model_catalog`).
    - `model_list` says where the Admin keeps the models it adds in Settings; they complete the
      declared ones, and give them their upstream ids.
    - `build` makes the thing the runtime uses (a repository, a prospect source, a channel, a model
      client; `product` says which, for a source). A provider with no `build` is known but cannot be
      run yet.
    """

    key: str
    kind: ProviderKind
    label: str
    roles: frozenset[ProviderKind] = frozenset()
    family: str | None = None
    settings_models: Mapping[ProviderKind, type[BaseModel]] = field(default_factory=dict)
    credentials: tuple[CredentialDeclaration, ...] = ()
    connection_keys: tuple[str, ...] = ()
    capabilities: Mapping[str, object] = field(default_factory=dict)
    unique_across_clients: tuple[UniquenessRule, ...] = ()
    can_host_store: bool = False
    build: Callable[[Any], Any] | None = None
    product: Product = Product.REPOSITORY  # what `build` makes, for a source provider
    channel: ChannelSupport | None = None
    models: tuple[ModelDescriptor, ...] = ()  # the models it serves, by Revansa's stable key
    model_list: ModelList | None = None  # where the Admin keeps its models (Settings), if it can

    def __post_init__(self) -> None:
        if not PROVIDER_KEY.match(self.key):
            raise ValueError(f"provider key {self.key!r} must match {PROVIDER_KEY.pattern}")
        roles = frozenset(self.roles) | {self.kind}
        if self.can_host_store and not {ProviderKind.SOURCE, ProviderKind.STORE} <= roles:
            raise ValueError(f"{self.key}: can_host_store needs the source and store roles")
        if (ProviderKind.CHANNEL in roles) != (self.channel is not None):
            raise ValueError(f"{self.key}: exactly the channel providers carry `channel`")
        unknown = set(self.settings_models) - roles
        if unknown:
            raise ValueError(f"{self.key}: settings given for a role it does not play: {unknown}")
        keys = [credential.key for credential in self.credentials]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.key}: a credential is declared twice")
        undeclared = [key for key in self.connection_keys if key not in keys]
        if undeclared:
            raise ValueError(f"{self.key}: connection keys that are not declared: {undeclared}")
        if self.models and ProviderKind.LLM not in roles:
            raise ValueError(f"{self.key}: only a model provider declares models")
        if self.model_list is not None and ProviderKind.LLM not in roles:
            raise ValueError(f"{self.key}: only a model provider keeps a model list")
        stray = [m.key for m in self.models if m.provider != self.key]
        if stray:
            raise ValueError(f"{self.key}: models that name another provider: {stray}")
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "settings_models", MappingProxyType(dict(self.settings_models)))
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))

    def plays(self, kind: ProviderKind) -> bool:
        return kind in self.roles

    def credential(self, key: str) -> CredentialDeclaration | None:
        return next((c for c in self.credentials if c.key == key), None)

    @property
    def runnable(self) -> bool:
        """Whether the runtime can build it today."""
        return self.build is not None
