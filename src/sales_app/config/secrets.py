"""Credentials resolved for one client, by logical key. Config files hold only names, never values.

A value comes from the credential store (`sales_app.secret_store`) first and from an environment
variable second (the older way, kept as a fallback so existing deployments keep working).
"""

from collections.abc import Mapping

# The logical keys the runtime asks for. The first six are the credential store's own keys (see
# `sales_app.secret_store.catalog`; a test keeps the two lists equal).
OPENAI_API_KEY = "openai.api_key"
OPENAI_ORGANIZATION = "openai.organization"  # optional
OPENAI_PROJECT = "openai.project"  # optional
OPENAI_BASE_URL = "openai.base_url"  # optional
OPENAI_MODEL_SOL = "openai.model_sol"  # which models Revansa offers; read by the Admin API
OPENAI_MODEL_LUNE = "openai.model_lune"  # legacy: only read while OPENAI_MODELS is not stored
OPENAI_MODELS = "openai.models"  # JSON list of {name, id, active}: the models Settings offers
WHATSAPP_ACCESS_TOKEN = "whatsapp.access_token"
WHATSAPP_PHONE_NUMBER_ID = "whatsapp.phone_number_id"
WHATSAPP_APP_SECRET = "whatsapp.app_secret"
WHATSAPP_API_VERSION = "whatsapp.api_version"
WHATSAPP_VERIFY_TOKEN = "whatsapp.verify_token"  # global; read when the parsers are built
# Resolved from an environment variable only, and not in the credential store: the Google access
# model is going to change (OAuth per client), so nothing is stored for it in the meantime.
SHEETS_CREDENTIALS_FILE = "sheets.credentials_file"

_MIN_REDACTED_LENGTH = 8  # shorter values (an API version) would blank out ordinary words


class Secrets:
    """The values one client refers to. Never prints its contents."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str) -> str:
        return self._values[name]

    def get_optional(self, name: str) -> str | None:
        return self._values.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._values

    def redact(self, text: str) -> str:
        """`text` with any value held here replaced, for messages that may reach an admin."""
        for value in sorted(self._values.values(), key=len, reverse=True):
            if len(value) >= _MIN_REDACTED_LENGTH:
                text = text.replace(value, "***")
        return text

    def __repr__(self) -> str:
        return f"Secrets(<{len(self._values)} values redacted>)"

    __str__ = __repr__
