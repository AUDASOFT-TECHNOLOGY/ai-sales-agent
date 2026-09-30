"""Process-level settings, read from the environment. Client settings live in clients/."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sales_core.approval import ApprovalMode


class Settings(BaseSettings):
    """Environment variables are `SALES_AGENT_<FIELD>` (plus `WHATSAPP_VERIFY_TOKEN`)."""

    model_config = SettingsConfigDict(env_prefix="SALES_AGENT_", extra="ignore")

    clients_dir: Path = Path("clients")
    # Versioned client settings (see sales_app.config_store). Holds client business data and
    # changes at runtime, so it lives outside git and belongs in server backups.
    config_dir: Path = Path("config")
    # Admin Revansa accounts and sessions (see sales_app.admin_auth). Same reasoning as
    # config_dir: outside git, included in backups.
    admin_dir: Path = Path("admin")
    admin_session_ttl_hours: float = Field(default=24.0, gt=0)
    # False only for local http development; a production deployment must keep this true so the
    # session cookie is never sent over a plain connection.
    admin_cookie_secure: bool = True
    log_level: str = "INFO"
    # Highest mode any client may run in, whatever its client.yaml says. Autonomy needs an
    # explicit deployment decision: SALES_AGENT_MODE_CEILING=autonomous.
    mode_ceiling: ApprovalMode = ApprovalMode.APPROVAL
    # Deployment allow-list for `llm.provider` / `llm.model` (see sales_app.config.llm_registry).
    # Empty (default): no restriction, so today's clients and tests keep working unchanged.
    # Set as JSON, e.g. SALES_AGENT_ALLOWED_LLM_MODELS='{"openai": ["gpt-4o"]}'.
    allowed_llm_models: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    lock_file: Path = Path(".sales-agent.lock")
    host: str = "127.0.0.1"
    port: int = 8000
    tick_seconds: float = Field(default=30.0, gt=0)
    whatsapp_verify_token: str | None = Field(
        default=None, validation_alias="WHATSAPP_VERIFY_TOKEN"
    )
