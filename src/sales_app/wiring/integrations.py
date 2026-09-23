"""The table that turns a `provider` name in configuration into a Core port implementation.

Replacing Google Sheets, OpenAI or WhatsApp later means adding a row here and editing the
config; the registry and the Engine do not change. Tests replace the whole table with fakes.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sales_app.config import ClientConfig
from sales_app.settings import Settings
from sales_core.contracts import Channel, ClientId
from sales_core.ports import LLMProvider, MessagingChannel, SalesRepository, WebhookParser
from sales_integrations.google_sheets import (
    GoogleSheetsApi,
    GoogleSheetsRepository,
    SheetTabs,
    extract_spreadsheet_id,
)
from sales_integrations.openai import OpenAIProvider
from sales_integrations.whatsapp import WhatsAppChannel, WhatsAppWebhookParser


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


def _google_sheets(config: ClientConfig) -> SalesRepository:
    section = config.spec.sheets
    return GoogleSheetsRepository(
        GoogleSheetsApi.from_service_account_file(config.secrets.get(section.credentials_env)),
        client_id=ClientId(config.client_id),
        spreadsheet_id=extract_spreadsheet_id(section.spreadsheet_id),
        timezone=ZoneInfo(config.spec.timezone),
        tabs=SheetTabs(
            prospects=section.prospects_tab,
            actions=section.actions_tab,
            messages=section.messages_tab,
            log=section.log_tab,
        ),
        column_mapping=section.column_mapping,
    )


def _openai(config: ClientConfig) -> LLMProvider:
    llm = config.spec.llm
    return OpenAIProvider(model=llm.model, api_key=config.secrets.get(llm.api_key_env))


def _whatsapp_channel(config: ClientConfig) -> MessagingChannel:
    section = config.spec.whatsapp
    return WhatsAppChannel(
        token=config.secrets.get(section.token_env),
        phone_number_id=config.secrets.get(section.phone_number_id_env),
        api_version=section.api_version,
    )


def default_builders(settings: Settings) -> IntegrationBuilders:
    def secret(config: ClientConfig) -> str | None:
        return config.secrets.get_optional(config.spec.whatsapp.app_secret_env)

    def parser(secrets: Mapping[str, str]) -> WebhookParser:
        return WhatsAppWebhookParser(
            app_secrets=secrets, verify_token=settings.whatsapp_verify_token
        )

    return IntegrationBuilders(
        repositories={"google_sheets": _google_sheets},
        llms={"openai": _openai},
        channels={
            "whatsapp": ChannelProvider(
                kind=Channel.WHATSAPP,
                build=_whatsapp_channel,
                routing_key=lambda config: config.phone_number_id,
                webhook_secret=secret,
                build_parser=parser,
            )
        },
    )
