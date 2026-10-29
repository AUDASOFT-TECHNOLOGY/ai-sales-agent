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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sales_app.agent_activity import ActivityStore
from sales_app.config import ClientConfig
from sales_app.config.runtime_config import channel_provider_keys
from sales_app.config.schema import SourceInstance
from sales_app.inbox import Fetched, InboxPoller
from sales_app.learning_store import LearningStore
from sales_app.llm_usage import LLMUsageStore
from sales_app.send_cap import DailySendGuard
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
from sales_app.wiring.writeback import WritebackSupport
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
    # Whether this client's configuration needs its inbox polled (a channel that polls in one of its
    # modes only: Telegram from the client's own account); None: always, when there is an inbox.
    polls: Callable[[ClientConfig], bool] | None = None
    # Every message through this channel takes a place in the client's daily send cap (e-mail, in
    # both modes). The runtime wraps the channel it builds: with no guard wired, nothing is sent.
    metered: bool = False


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
    # How to build one named instance of a source, by provider key, for a client that runs several
    # (`sources`): `build(config, instance)`. A provider missing here can only be a client's one
    # source.
    source_instances: Mapping[str, Callable[[ClientConfig, SourceInstance], ProspectSource]] = (
        field(default_factory=dict)
    )
    # Writing results back into the sources that ask for it (off unless the deployment says so).
    writeback: WritebackSupport | None = None
    # One client's LLM usage register, or None when the deployment has no database: calls are then
    # simply not recorded.
    llm_usage: Callable[[str], LLMUsageStore] | None = None
    # One client's live activity (what its agent is doing now), or None: nothing is then told.
    activity: Callable[[str], ActivityStore] | None = None
    # One client's learning registers (what its agent keeps to learn from its results), or None:
    # nothing is then traced and nothing learned.
    learning: Callable[[str], LearningStore] | None = None
    # One client's reference documents (what its Brain may consult to answer from the documents the
    # client attached to its products), or None when the deployment keeps none. See
    # `sales_app.resources`.
    knowledge: Callable[[str], Any] | None = None
    # One client's cap guard for a channel kind (`guard(client_id, channel)`), or None when the
    # deployment has no database: a `metered` channel then refuses every message.
    send_guards: Callable[[str, str], DailySendGuard] | None = None
    # One client's conversation control (who is in charge of each conversation: the agent or a
    # person), or None: the Engine is then built without it and no conversation can be taken over.
    conversation_control: Callable[[str], Any] | None = None
    # Tells the notifications how sends and model calls go (`sales_app.operational_signals`), or None.
    signals: Any | None = None

    # ---- polling the channels that do not call us ------------------------------------------

    def _inbox_providers(self, config: ClientConfig) -> list[ChannelProvider]:
        """The channels of this client that have to be polled, once each however many lanes share
        them (a mailbox is read once, not once per lane)."""
        found = (self.channels.get(key) for key in channel_provider_keys(config))
        return [
            p
            for p in found
            if p is not None
            and p.build_inbox is not None
            and (p.polls is None or p.polls(config))
        ]

    def polls_inbox(self, config: ClientConfig) -> bool:
        """Whether this client's replies are fetched by the scheduler. Never in a dry run, which
        does not talk to the outside: a message read there would be marked read and then dropped."""
        if config.effective_mode is ApprovalMode.DRY_RUN:
            return False
        return bool(self._inbox_providers(config))

    def inbox_channels(self, config: ClientConfig) -> tuple[str, ...]:
        """The channel kinds that `open_inbox` reads (for the notifications of a failed poll)."""
        return tuple(p.kind.value for p in self._inbox_providers(config))

    def open_inbox(self, config: ClientConfig) -> InboxPoller:
        providers = self._inbox_providers(config)
        if not providers:
            raise LookupError("this client's channel has no inbox to poll")
        pollers = [provider.build_inbox(config) for provider in providers]  # type: ignore[misc]
        return pollers[0] if len(pollers) == 1 else _MergedInbox(pollers)


class _MergedInbox:
    """The inboxes of a client that has several channels to poll, read as one. A handle says which
    inbox it came from, so each message is acknowledged where it was found."""

    def __init__(self, pollers: Sequence[InboxPoller]) -> None:
        self._pollers = tuple(pollers)

    @property
    def needs_cursor(self) -> bool:
        return any(getattr(p, "needs_cursor", False) for p in self._pollers)

    @property
    def filters_by_sender(self) -> bool:
        return any(getattr(p, "filters_by_sender", False) for p in self._pollers)

    def use_cursor(self, store) -> None:
        for poller in self._pollers:
            if getattr(poller, "needs_cursor", False):
                poller.use_cursor(store)  # type: ignore[attr-defined]

    def fetch(self, limit: int = 50, accept=None) -> Sequence[Fetched]:
        found: list[Fetched] = []
        for index, poller in enumerate(self._pollers):
            room = limit - len(found)
            items = (
                poller.fetch(room, accept=accept)  # type: ignore[call-arg]
                if getattr(poller, "filters_by_sender", False)
                else poller.fetch(room)
            )
            found.extend(_Tagged(index, item) for item in items)
            if len(found) >= limit:
                break
        return found

    def acknowledge(self, handle: str) -> None:
        index, _, inner = handle.partition(":")
        self._pollers[int(index)].acknowledge(inner)

    def close(self) -> None:
        for poller in self._pollers:
            poller.close()


class _Tagged:
    def __init__(self, index: int, item: Fetched) -> None:
        self.handle = f"{index}:{item.handle}"
        self.message = item.message


def default_builders(
    settings: Settings,
    verify_token: Callable[[], str | None] | None = None,
    *,
    registry: ProviderRegistry | None = None,
    operational_stores: Callable[[str], OperationalStore] | None = None,
    consent: Callable[[str], ConsentWriter] | None = None,
    writeback: WritebackSupport | None = None,
    llm_usage: Callable[[str], LLMUsageStore] | None = None,
    activity: Callable[[str], ActivityStore] | None = None,
    learning: Callable[[str], LearningStore] | None = None,
    knowledge: Callable[[str], Any] | None = None,
    send_guards: Callable[[str, str], DailySendGuard] | None = None,
    conversation_control: Callable[[str], Any] | None = None,
    gmail: Any = None,
    whatsapp_service: Any = None,
    telegram_accounts: Any = None,
    first_contacts: Callable[[str, int], DailySendGuard] | None = None,
) -> IntegrationBuilders:
    """The builders of every provider the runtime can run today (those with a factory).

    `verify_token` is asked each time the parsers are (re)built, so a token saved in Admin
    applies without a restart; without it, the deployment's environment variable is used.
    `registry` defaults to the providers Revansa knows. A provider that is declared but has no
    factory is left out, so a client naming it is refused exactly as for any unknown provider.
    `operational_stores` and `consent` open one client's store and registry (None: the deployment
    has none, and a client whose source needs them is refused). `send_guards` gives the daily send
    cap guard of a client's channel; without it the e-mail channel (metered) sends nothing.
    """
    registry = registry if registry is not None else default_registry()

    def channel_provider(descriptor) -> ChannelProvider:
        support = descriptor.channel
        build, build_inbox = descriptor.build, support.build_inbox
        if Channel(support.kind) is Channel.EMAIL and gmail is not None:
            # The e-mail channel of a client in Gmail mode comes from the application's Gmail
            # service; the other modes are built exactly as before.
            build = lambda config: descriptor.build(config, gmail=gmail)
            build_inbox = lambda config: support.build_inbox(config, gmail=gmail)
        if Channel(support.kind) is Channel.TELEGRAM and telegram_accounts is not None:
            # A client on its own Telegram account is served by the application's account service,
            # behind the rationing of first messages; a client on a bot is built as it always was.
            build = lambda config: descriptor.build(
                config, accounts=telegram_accounts, first_contacts=first_contacts
            )
            build_inbox = lambda config: support.build_inbox(config, accounts=telegram_accounts)

        if Channel(support.kind) is Channel.WHATSAPP and whatsapp_service is not None:
            # The WhatsApp channel of a client follows the 24-hour window with its templates.
            build = lambda config: descriptor.build(config, service=whatsapp_service)

        def parser(secrets: Mapping[str, str]) -> WebhookParser:
            token = verify_token() if verify_token is not None else None
            return support.build_parser(secrets, token or settings.whatsapp_verify_token)

        return ChannelProvider(
            kind=Channel(support.kind),
            build=build,
            routing_key=support.routing_key,
            webhook_secret=support.webhook_secret,
            build_parser=parser if support.build_parser is not None else None,
            build_inbox=build_inbox,
            answer_unknown=support.answer_unknown,
            polls=support.polls_inbox,
            metered=Channel(support.kind) is Channel.EMAIL,
        )

    def runnable(kind: ProviderKind):
        return [d for d in registry.list(kind) if d.kind is kind and d.runnable]

    sources = runnable(ProviderKind.SOURCE)
    return IntegrationBuilders(
        repositories={d.key: d.build for d in sources if d.product is Product.REPOSITORY},
        llms={d.key: d.build for d in runnable(ProviderKind.LLM)},
        channels={d.key: channel_provider(d) for d in runnable(ProviderKind.CHANNEL)},
        sources={d.key: d.build for d in sources if d.product is Product.PROSPECT_SOURCE},
        source_instances={d.key: d.build_instance for d in sources if d.build_instance is not None},
        operational_stores=operational_stores,
        consent=consent,
        writeback=writeback,
        llm_usage=llm_usage,
        activity=activity,
        learning=learning,
        knowledge=knowledge,
        send_guards=send_guards,
        conversation_control=conversation_control,
    )
