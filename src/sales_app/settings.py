"""Process-level settings, read from the environment. Client settings live in clients/."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sales_core.approval import ApprovalMode


class Settings(BaseSettings):
    """Environment variables are `SALES_AGENT_<FIELD>` (plus `WHATSAPP_VERIFY_TOKEN`)."""

    model_config = SettingsConfigDict(env_prefix="SALES_AGENT_", extra="ignore")

    clients_dir: Path = Path("clients")
    log_level: str = "INFO"
    # Highest mode any client may run in, whatever its client.yaml says. Autonomy needs an
    # explicit deployment decision: SALES_AGENT_MODE_CEILING=autonomous.
    mode_ceiling: ApprovalMode = ApprovalMode.APPROVAL
    lock_file: Path = Path(".sales-agent.lock")
    host: str = "127.0.0.1"
    port: int = 8000
    tick_seconds: float = Field(default=30.0, gt=0)
    whatsapp_verify_token: str | None = Field(
        default=None, validation_alias="WHATSAPP_VERIFY_TOKEN"
    )
