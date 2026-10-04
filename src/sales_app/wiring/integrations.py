"""The table that turns a `provider` name in configuration into a Core port implementation.

The table is built from the provider descriptors (`sales_app.wiring.providers`): each runnable
provider contributes its factory under its own key. Replacing Google Sheets, OpenAI or WhatsApp
later means adding a descriptor and editing the config; the registry and the Engine do not change.
Tests replace the whole table with fakes.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sales_app.config import ClientConfig
from sales_app.settings import Settings
from sales_app.wiring.providers import (
    ProviderKind,
    ProviderRegistry,
    default_registry,
    google_sheets,
    openai,
    whatsapp,
)
from sales_core.contracts import Channel
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
    build_parser: Callable[[Mapping[str, str]], WebhookParser]  # from {routing_key: secret}


@dataclass(frozen=True, slots=True)
class IntegrationBuilders:
    repositories: Mapping[str, Callable[[ClientConfig], SalesRepository]]
    llms: Mapping[str, Callable[[ClientConfig], LLMProvider]]
    channels: Mapping[str, ChannelProvider]


def default_builders(
    settings: Settings,
    verify_token: Callable[[], str | None] | None = None,
    *,
    registry: ProviderRegistry | None = None,
) -> IntegrationBuilders:
    """The builders of every provider the runtime can run today (those with a factory).

    `verify_token` is asked each time the parsers are (re)built, so a token saved in Admin
    applies without a restart; without it, the deployment's environment variable is used.
    `registry` defaults to the providers Revansa knows. A provider that is declared but has no
    factory is left out, so a client naming it is refused exactly as for any unknown provider.
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
            build_parser=parser,
        )

    def runnable(kind: ProviderKind):
        return [d for d in registry.list(kind) if d.kind is kind and d.runnable]

    return IntegrationBuilders(
        repositories={d.key: d.build for d in runnable(ProviderKind.SOURCE)},
        llms={d.key: d.build for d in runnable(ProviderKind.LLM)},
        channels={d.key: channel_provider(d) for d in runnable(ProviderKind.CHANNEL)},
    )
