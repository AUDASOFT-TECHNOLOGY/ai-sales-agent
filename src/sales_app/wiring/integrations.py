"""The table that turns a `provider` name in configuration into a Core port implementation.

Replacing Google Sheets, OpenAI or WhatsApp later means adding a row here and editing the
config; the registry and the Engine do not change. Tests replace the whole table with fakes.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sales_app.config import ClientConfig
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_ORGANIZATION,
    OPENAI_PROJECT,
    SHEETS_CREDENTIALS_FILE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
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
        GoogleSheetsApi.from_service_account_file(config.secrets.get(SHEETS_CREDENTIALS_FILE)),
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
    return OpenAIProvider(
        model=llm.model,
        api_key=config.secrets.get(OPENAI_API_KEY),
        organization=config.secrets.get_optional(OPENAI_ORGANIZATION),
        project=config.secrets.get_optional(OPENAI_PROJECT),
        base_url=config.secrets.get_optional(OPENAI_BASE_URL),
    )


def _whatsapp_channel(config: ClientConfig) -> MessagingChannel:
    return WhatsAppChannel(
        token=config.secrets.get(WHATSAPP_ACCESS_TOKEN),
        phone_number_id=config.secrets.get(WHATSAPP_PHONE_NUMBER_ID),
        api_version=config.secrets.get(WHATSAPP_API_VERSION),
    )


def default_builders(
    settings: Settings, verify_token: Callable[[], str | None] | None = None
) -> IntegrationBuilders:
    """`verify_token` is asked each time the parsers are (re)built, so a token saved in Admin
    applies without a restart; without it, the deployment's environment variable is used."""

    def secret(config: ClientConfig) -> str | None:
        return config.secrets.get_optional(WHATSAPP_APP_SECRET)

    def parser(secrets: Mapping[str, str]) -> WebhookParser:
        token = verify_token() if verify_token is not None else None
        return WhatsAppWebhookParser(
            app_secrets=secrets, verify_token=token or settings.whatsapp_verify_token
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
