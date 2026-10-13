"""The Telegram channel: sending through a client's bot, and reading what people write to it."""

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
    "BotCheck",
    "BotCheckError",
    "BotReply",
    "TelegramApi",
    "TelegramChannel",
    "TelegramWebhookParser",
    "chunks",
    "invitation_link",
    "make_invitation",
    "read_invitation",
]
