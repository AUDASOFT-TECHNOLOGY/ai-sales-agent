"""The table that turns a `provider` name in configuration into a Core port implementation.

The table is built from the provider descriptors (`sales_app.wiring.providers`): each runnable
provider contributes its factory under its own key. Replacing Google Sheets, OpenAI or WhatsApp
later means adding a descriptor and editing the config; the registry and the Engine do not change.
Tests replace the whole table with fakes.

A source provider that only knows where the people are (`Product.PROSPECT_SOURCE`) is listed apart,
in `sources`, together with what the runtime composes it with: how to open the client's operational
store and its consent registry. Both are optional because a deployment may have neither (no
database, no registry set up): a client that needs one then does not run, and says so.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from sales_app.config import ClientConfig
from sales_app.config.runtime_config import channel_provider_key
from sales_app.inbox import InboxPoller
from sales_app.settings import Settings
from sales_app.sourcing.consent import ConsentWriter
from sales_app.sourcing.ports import OperationalStore, ProspectSource
from sales_app.wiring.providers import (
    Product,
    ProviderKind,
    ProviderRegistry,
    default_registry,
    google_sheets,
    openai,
    whatsapp,
)
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel, InboundMessage
from sales_core.ports import LLMProvider, MessagingChannel, SalesRepository, WebhookParser

# The factories now live in the providers' descriptors; these names are what existing importers
# (and tests) have always used.
_google_sheets = google_sheets.build_repository
_openai = openai.build_provider
_whatsapp_channel = whatsapp.build_channel


@dataclass(frozen=True, slots=True)
class ChannelProvider:
    """Everything the app needs to know about one messaging channel provider."""

    kind: Channel
    build: Callable[[ClientConfig], MessagingChannel]
    routing_key: Callable[[ClientConfig], str | None]  # which account inbound events address
    webhook_secret: Callable[[ClientConfig], str | None]  # what signs inbound requests
    # From {routing_key: secret}; None for a channel with no webhook (e-mail: see `build_inbox`).
    build_parser: Callable[[Mapping[str, str]], WebhookParser] | None
    # What the scheduler polls for replies, one per run; None for a channel that calls us.
    build_inbox: Callable[[ClientConfig], InboxPoller] | None = None
    # Answers a sender no prospect matches (see `ChannelSupport.answer_unknown`); None: no answer.
    answer_unknown: Callable[[ClientConfig, InboundMessage], None] | None = None


@dataclass(frozen=True, slots=True)
class IntegrationBuilders:
    repositories: Mapping[str, Callable[[ClientConfig], SalesRepository]]
    llms: Mapping[str, Callable[[ClientConfig], LLMProvider]]
    channels: Mapping[str, ChannelProvider]
    # Sources that are only a source, by provider key. The runtime composes each with the client's
    # operational store and consent registry; it never uses one in place of a repository.
    sources: Mapping[str, Callable[[ClientConfig], ProspectSource]] = field(default_factory=dict)
    # One client's operational store / consent registry, or None when the deployment has none.
    operational_stores: Callable[[str], OperationalStore] | None = None
    consent: Callable[[str], ConsentWriter] | None = None

    # ---- polling the channels that do not call us ------------------------------------------

    def polls_inbox(self, config: ClientConfig) -> bool:
        """Whether this client's replies are fetched by the scheduler. Never in a dry run, which
        does not talk to the outside: a message read there would be marked read and then dropped."""
        if config.effective_mode is ApprovalMode.DRY_RUN:
            return False
        key = channel_provider_key(config)
        provider = self.channels.get(key) if key is not None else None
        return provider is not None and provider.build_inbox is not None

    def open_inbox(self, config: ClientConfig) -> InboxPoller:
        key = channel_provider_key(config)
        provider = self.channels.get(key) if key is not None else None
        if provider is None or provider.build_inbox is None:
            raise LookupError("this client's channel has no inbox to poll")
        return provider.build_inbox(config)


def default_builders(
    settings: Settings,
    verify_token: Callable[[], str | None] | None = None,
    *,
    registry: ProviderRegistry | None = None,
    operational_stores: Callable[[str], OperationalStore] | None = None,
    consent: Callable[[str], ConsentWriter] | None = None,
) -> IntegrationBuilders:
    """The builders of every provider the runtime can run today (those with a factory).

    `verify_token` is asked each time the parsers are (re)built, so a token saved in Admin
    applies without a restart; without it, the deployment's environment variable is used.
    `registry` defaults to the providers Revansa knows. A provider that is declared but has no
    factory is left out, so a client naming it is refused exactly as for any unknown provider.
    `operational_stores` and `consent` open one client's store and registry (None: the deployment
    has none, and a client whose source needs them is refused).
    """
    registry = registry if registry is not None else default_registry()

    def channel_provider(descriptor) -> ChannelProvider:
        support = descriptor.channel

        def parser(secrets: Mapping[str, str]) -> WebhookParser:
            token = verify_token() if verify_token is not None else None
            return support.build_parser(secrets, token or settings.whatsapp_verify_token)

        return ChannelProvider(
            kind=Channel(support.kind),
            build=descriptor.build,
            routing_key=support.routing_key,
            webhook_secret=support.webhook_secret,
            build_parser=parser if support.build_parser is not None else None,
            build_inbox=support.build_inbox,
            answer_unknown=support.answer_unknown,
        )

    def runnable(kind: ProviderKind):
        return [d for d in registry.list(kind) if d.kind is kind and d.runnable]

    sources = runnable(ProviderKind.SOURCE)
    return IntegrationBuilders(
        repositories={d.key: d.build for d in sources if d.product is Product.REPOSITORY},
        llms={d.key: d.build for d in runnable(ProviderKind.LLM)},
        channels={d.key: channel_provider(d) for d in runnable(ProviderKind.CHANNEL)},
        sources={d.key: d.build for d in sources if d.product is Product.PROSPECT_SOURCE},
        operational_stores=operational_stores,
        consent=consent,
    )
