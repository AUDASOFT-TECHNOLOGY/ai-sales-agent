"""Webhook parsers: built from the clients' current credentials, and replaceable while running.

`ParserTable` is what the HTTP layer reads on every request; swapping its contents is atomic, so a
request sees either the old parsers or the new ones, never a mixture.
"""

from collections.abc import Iterator, Mapping, Sequence

from sales_app.config import ClientConfig
from sales_app.config.runtime_config import channel_provider_keys
from sales_app.wiring import IntegrationBuilders
from sales_core.ports import WebhookParser


class ParserTable(Mapping[str, WebhookParser]):
    """Channel provider name -> webhook parser."""

    def __init__(self, parsers: Mapping[str, WebhookParser] | None = None) -> None:
        self._parsers: dict[str, WebhookParser] = dict(parsers or {})

    def replace(self, parsers: Mapping[str, WebhookParser]) -> None:
        self._parsers = dict(parsers)  # one reference assignment: readers never see half of it

    def __getitem__(self, name: str) -> WebhookParser:
        return self._parsers[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._parsers)

    def __len__(self) -> int:
        return len(self._parsers)


def build_parsers(
    configs: Sequence[ClientConfig],
    builders: IntegrationBuilders,
    installed: Sequence[ClientConfig] = (),
) -> dict[str, WebhookParser]:
    """One parser per channel provider that has clients, keyed by what each client's inbound
    account is called and signed with. Clients that are not ready are included: their messages
    must still be authenticated (and then dropped, with a log) rather than treated as unknown.

    `installed` are the configs currently in force, which may name an account a newer config in
    `configs` is about to replace: that account keeps being accepted until the swap, but signed
    with the newest secret, so a rotated secret never leaves an old one working."""
    parsers: dict[str, WebhookParser] = {}
    newest = {c.client_id: c for c in configs}
    for name, provider in builders.channels.items():
        if provider.build_parser is None:
            continue  # this channel does not call us: its replies are polled (`sales_app.inbox`)
        secrets: dict[str, str] = {}
        holders: dict[str, str] = {}  # routing key -> the one client it belongs to
        serving = False
        for config in [*installed, *configs]:
            if name not in channel_provider_keys(config):
                continue
            serving = True
            key = provider.routing_key(config)
            secret = provider.webhook_secret(newest.get(config.client_id, config))
            if key and secret:
                # An account belongs to one client. The clients in force come first, so a client
                # that is being set up on someone else's account can never take its messages (nor
                # replace its secret): it is refused when its runtime is built, and ignored here.
                if holders.setdefault(key, config.client_id) != config.client_id:
                    continue
                secrets[key] = secret
        if serving:
            parsers[name] = provider.build_parser(secrets)
    return parsers
