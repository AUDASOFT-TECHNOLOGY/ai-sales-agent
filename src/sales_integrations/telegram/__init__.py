"""The Telegram channel: a client's own bot, or (`account`) the client's own Telegram account."""

from sales_integrations.telegram.account import (
    AccountClient,
    AccountFailure,
    TelegramAccountChannel,
    TelegramAccountInbox,
)
from sales_integrations.telegram.api import BotReply, TelegramApi, chunks
from sales_integrations.telegram.channel import TelegramChannel
from sales_integrations.telegram.invitations import (
    invitation_link,
    make_invitation,
    read_invitation,
)
from sales_integrations.telegram.probe import BotCheck, BotCheckError
from sales_integrations.telegram.webhook import SECRET_HEADER, TelegramWebhookParser

__all__ = [
    "SECRET_HEADER",
    "AccountClient",
    "AccountFailure",
    "BotCheck",
    "BotCheckError",
    "BotReply",
    "TelegramAccountChannel",
    "TelegramAccountInbox",
    "TelegramApi",
    "TelegramChannel",
    "TelegramWebhookParser",
    "chunks",
    "invitation_link",
    "make_invitation",
    "read_invitation",
]
