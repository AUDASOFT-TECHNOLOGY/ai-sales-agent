"""Process-level settings, read from the environment. Client settings live in clients/."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from sales_core.approval import ApprovalMode


class Settings(BaseSettings):
    """Environment variables are `SALES_AGENT_<FIELD>` (plus `WHATSAPP_VERIFY_TOKEN`)."""

    model_config = SettingsConfigDict(env_prefix="SALES_AGENT_", extra="ignore")

    clients_dir: Path = Path("clients")
    # Whether a client that has a `clients/<id>/client.yaml` may still be read from it. True only while
    # a deployment still has clients that are not in the database (`sales-agent db legacy-check` says
    # when none is left). False: no client file is ever read or written, whatever `clients_dir` holds.
    legacy_client_files: bool = True
    # Where the consent registry's events are kept. "file": one JSON-lines file per client under
    # `secrets_dir` (what a deployment did until now). "sql": the database, strictly: a client whose
    # consent files have not been imported yet is refused rather than started on an empty registry, and
    # no consent file is ever read or written (see `sales-agent consent import-log`).
    consent_store: Literal["file", "sql"] = "file"
    # Versioned client settings (see sales_app.config_store). Holds client business data and
    # changes at runtime, so it lives outside git and belongs in server backups.
    config_dir: Path = Path("config")
    # Admin Revansa accounts and sessions (see sales_app.admin_auth). Same reasoning as
    # config_dir: outside git, included in backups.
    admin_dir: Path = Path("admin")
    # Client account profiles (see sales_app.client_profiles): login, hashed temporary password,
    # logo. Outside git like admin_dir, and included in backups.
    profiles_dir: Path = Path("profiles")
    # Credentials kept server-side and encrypted (see sales_app.secret_store). Outside git, like
    # admin_dir, and included in backups -- useless without the master key, which is kept apart.
    secrets_dir: Path = Path("secrets")
    # The master key that opens secrets_dir: SALES_AGENT_SECRETS_KEY, 32 random bytes in base64
    # (`sales-agent secrets generate-key`). Never logged: SecretStr hides it from repr.
    secrets_key: SecretStr | None = None
    # Where accounts, sessions and versioned settings live (see sales_app.persistence):
    # SALES_AGENT_DATABASE_URL, e.g. postgresql://user:password@host:5432/revansa. Unset: the JSON
    # files in admin_dir / profiles_dir / config_dir (the fallback kept during the move to a
    # database). The URL carries a password, so SecretStr keeps it out of any repr or log.
    database_url: SecretStr | None = None
    admin_session_ttl_hours: float = Field(default=24.0, gt=0)
    client_session_ttl_hours: float = Field(default=24.0, gt=0)
    # False only for local http development; a production deployment must keep this true so the
    # session cookies (Admin and Client) are never sent over a plain connection.
    admin_cookie_secure: bool = True
    log_level: str = "INFO"
    # Highest mode any client may run in, whatever its client.yaml says. Autonomy needs an
    # explicit deployment decision: SALES_AGENT_MODE_CEILING=autonomous.
    mode_ceiling: ApprovalMode = ApprovalMode.APPROVAL
    # Deployment allow-list for `llm.provider` / `llm.model` (see sales_app.config.llm_registry).
    # Empty (default): no restriction, so today's clients and tests keep working unchanged.
    # Set as JSON, e.g. SALES_AGENT_ALLOWED_LLM_MODELS='{"openai": ["<model-id>"]}'.
    allowed_llm_models: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    # DEPRECATED and ignored (a warning is logged at startup when it is set). The Model IDs a
    # provider's API wants are entered by the Admin in Settings, the single source of them (see
    # sales_app.llm_models.settings_catalog). Kept only so an older deployment still starts.
    model_upstream_ids: dict[str, str] = Field(default_factory=dict)
    # The address people reach this deployment on (https://revansa.example). Telegram needs it to
    # know where to deliver a client's messages (`<base>/webhooks/telegram`); unset, a Telegram bot
    # can be saved and tested but not connected. No trailing path, never a secret.
    public_base_url: str | None = None
    lock_file: Path = Path(".sales-agent.lock")
    host: str = "127.0.0.1"
    port: int = 8000
    tick_seconds: float = Field(default=30.0, gt=0)
    whatsapp_verify_token: str | None = Field(
        default=None, validation_alias="WHATSAPP_VERIFY_TOKEN"
    )

    @property
    def client_files_dir(self) -> Path | None:
        """Where legacy client files are read, or None when they are switched off."""
        return self.clients_dir if self.legacy_client_files else None
