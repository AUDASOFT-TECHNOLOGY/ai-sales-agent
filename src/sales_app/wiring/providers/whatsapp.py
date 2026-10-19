"""The WhatsApp Business Cloud API channel: sending, and authenticating what Meta sends back.

One account per client, identified by its phone number id (what inbound events are routed by).
The credential keys and their rules are the ones the credential store already holds.
"""

import re
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from sales_app.config import ClientConfig
from sales_app.config.secrets import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_ID,
    WHATSAPP_APP_SECRET,
    WHATSAPP_CONFIG_ID,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_VERIFY_TOKEN,
    WHATSAPP_WABA_ID,
)
from sales_app.secret_store import ScopeKind
from sales_app.wiring.providers.types import (
    ChannelSupport,
    CredentialDeclaration,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
    UniquenessRule,
)
from sales_integrations.whatsapp import WhatsAppChannel, WhatsAppWebhookParser

KEY = "whatsapp"
CHANNEL_KIND = "whatsapp"  # the Core's name for this channel

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WhatsAppChannelSettings(BaseModel):
    """What a client's channel entry may say about itself (no account, no credential)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_version: NonEmpty = "v21.0"  # a global setting in the credential store wins


def build_channel(config: ClientConfig, *, service: Any = None) -> WhatsAppChannel:
    """The client's sending channel. `service` is the application's `WhatsAppService`: it binds the
    channel to the client's 24-hour windows and message templates. Without it the channel sends
    exactly what it is given (tests, and the deployments that predate the service)."""
    if service is not None:
        return service.channel_for(config)
    return WhatsAppChannel(
        token=config.secrets.get(WHATSAPP_ACCESS_TOKEN),
        phone_number_id=config.secrets.get(WHATSAPP_PHONE_NUMBER_ID),
        api_version=config.secrets.get(WHATSAPP_API_VERSION),
    )


def routing_key(config: ClientConfig) -> str | None:
    return config.phone_number_id


def webhook_secret(config: ClientConfig) -> str | None:
    return config.secrets.get_optional(WHATSAPP_APP_SECRET)


def build_parser(app_secrets: Mapping[str, str], verify_token: str | None) -> WhatsAppWebhookParser:
    return WhatsAppWebhookParser(app_secrets=app_secrets, verify_token=verify_token)


DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.CHANNEL,
    label="WhatsApp",
    settings_models={ProviderKind.CHANNEL: WhatsAppChannelSettings},
    credentials=(
        # Revansa's own (global): the Meta app that signs webhooks and the subscription token.
        CredentialDeclaration(
            key=WHATSAPP_VERIFY_TOKEN, label="WhatsApp verify token", scope=ScopeKind.GLOBAL
        ),
        CredentialDeclaration(
            key=WHATSAPP_APP_SECRET,
            label="Meta app secret",
            scope=ScopeKind.GLOBAL,
            required_when=Requirement.LIVE,
        ),
        # The Meta app that clients connect their own account through (Embedded Signup): public
        # identifiers, not secrets. Setting them is what turns "Connect WhatsApp" on for clients.
        CredentialDeclaration(
            key=WHATSAPP_APP_ID,
            label="Meta app id",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            pattern=re.compile(r"^\d{5,32}$"),
            rule="must be the numeric id of the Meta app (digits only)",
        ),
        CredentialDeclaration(
            key=WHATSAPP_CONFIG_ID,
            label="Meta Embedded Signup configuration id",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            pattern=re.compile(r"^\d{5,32}$"),
            rule="must be the numeric id of the Login for Business configuration (digits only)",
        ),
        CredentialDeclaration(
            key=WHATSAPP_API_VERSION,
            label="WhatsApp API version",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            pattern=re.compile(r"^v\d{1,3}\.\d{1,3}$"),
            rule="must look like v21.0",
        ),
        # One client's account.
        CredentialDeclaration(
            key=WHATSAPP_ACCESS_TOKEN,
            label="WhatsApp access token",
            scope=ScopeKind.CLIENT,
            required_when=Requirement.LIVE,
        ),
        CredentialDeclaration(
            key=WHATSAPP_PHONE_NUMBER_ID,
            label="WhatsApp phone number id",
            scope=ScopeKind.CLIENT,
            sensitive=False,
            required_when=Requirement.LIVE,
            unique=True,
            pattern=re.compile(r"^\d{5,32}$"),
            rule="must be the numeric id Meta gives the phone number (digits only)",
        ),
        # The client's WhatsApp Business Account: written when the client connects, or by the Admin.
        # Needed for the message templates and the webhook subscription.
        CredentialDeclaration(
            key=WHATSAPP_WABA_ID,
            label="WhatsApp Business Account id",
            scope=ScopeKind.CLIENT,
            sensitive=False,
            pattern=re.compile(r"^\d{5,32}$"),
            rule="must be the numeric id Meta gives the WhatsApp Business Account (digits only)",
        ),
    ),
    # What Meta can check on its own: the token for the phone number. The app secret and the verify
    # token prove themselves only when Meta calls the webhook.
    connection_keys=(WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_API_VERSION),
    capabilities={
        "can_initiate": True,
        "reply_window_hours": 24,
        "requires_template_outside_window": True,
    },
    unique_across_clients=(
        UniquenessRule("credential", WHATSAPP_PHONE_NUMBER_ID, "WhatsApp phone_number_id"),
    ),
    build=build_channel,
    channel=ChannelSupport(
        kind=CHANNEL_KIND,
        routing_key=routing_key,
        webhook_secret=webhook_secret,
        build_parser=build_parser,
    ),
)
