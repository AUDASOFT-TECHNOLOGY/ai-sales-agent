"""What a client says about its own Telegram bot: how it greets people. Never a token.

The bot's token and webhook secret are credentials (`telegram.bot_token`, `telegram.webhook_secret`,
client scope, write-only). These settings are what an administrator may read back and what the
settings document holds, so nothing secret may ever be a field here.

A Telegram bot cannot write to someone who has not started it. When a person writes to the bot and
we do not know who they are, the bot asks them to share their phone number with one button, which
is how a prospect from a spreadsheet or a CRM is recognised without a personal invitation link. The
words are the client's (its customers' language), with a neutral English default.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

_TOKEN = re.compile(r"^(\d{5,20}):[A-Za-z0-9_-]{30,64}$")

DEFAULT_CONTACT_PROMPT = (
    "Hello! To continue, please share your phone number with the button below, "
    "so we can recognise you."
)
DEFAULT_CONTACT_BUTTON = "Share my phone number"
DEFAULT_NOT_FOUND = (
    "Thank you. We could not match this number to a request. A member of the team will get back "
    "to you."
)

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


BOT_MODE = "bot"
ACCOUNT_MODE = "account"
DEFAULT_CONSENT_FIELD = "telegram_consent"
DEFAULT_MAX_NEW_PER_DAY = 10
MAX_NEW_PER_DAY = 30  # Telegram limits accounts that write to many people who did not expect it


class TelegramChannelSettings(BaseModel):
    """One client's Telegram: its own bot (the default), or its own account.

    In `account` mode the client writes from its own Telegram account (it signs in with Telegram, the
    platform never sees its password). First messages go only to people the client marked, in the
    column `consent_field` of its prospects, as expecting the contact, and at most
    `max_new_conversations_per_day` of them a day; replies and follow-ups on a conversation the
    person started or accepted are not rationed. The bot's sentences are used in `bot` mode only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["bot", "account"] = BOT_MODE
    consent_field: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[\w .-]+$"
        ),
    ] = DEFAULT_CONSENT_FIELD
    max_new_conversations_per_day: Annotated[int, Field(ge=1, le=MAX_NEW_PER_DAY)] = (
        DEFAULT_MAX_NEW_PER_DAY
    )

    contact_prompt: Line = DEFAULT_CONTACT_PROMPT
    contact_button: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ] = DEFAULT_CONTACT_BUTTON
    not_found_text: Line = DEFAULT_NOT_FOUND

    @field_validator("contact_prompt", "contact_button", "not_found_text")
    @classmethod
    def _plain_text(cls, value: str) -> str:
        if any((ord(ch) < 32 and ch not in "\n") or ord(ch) == 127 for ch in value):
            raise ValueError("must be plain text")
        return value


def account_mode_of(settings: object) -> bool:
    """Whether a client's raw Telegram settings say `account` (without validating the rest)."""
    return isinstance(settings, dict) and settings.get("mode") == ACCOUNT_MODE


def bot_id_of(token: str | None) -> str | None:
    """The bot's numeric id (the part of the token before the colon), or None if it is not a
    token. It is not secret and identifies the bot: two clients must never share one."""
    match = _TOKEN.match((token or "").strip())
    return match.group(1) if match else None
