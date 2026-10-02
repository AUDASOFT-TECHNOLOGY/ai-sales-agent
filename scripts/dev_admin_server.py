"""Local Admin server for manually testing the Admin UI -- no real secrets needed.

Boots the same FastAPI app the real deployment runs (`sales_app.api.create_app`), but wired to
fake integrations (no Google Sheets, OpenAI or WhatsApp calls leave the machine) and the real
client definitions from `clients/` (Naga, Tounesimmo -- copied, never edited), so the Admin screens
(Dashboard, Clients, Agents, Settings, and their detail pages) can be exercised in a real browser
without configuring any provider credentials. No invented client is added. Because the app will not
boot without an enabled client, the temp copy of Naga alone is switched on (dry_run, stand-in Brain);
Tounesimmo stays disabled, exactly as in `clients/`.

This is dev tooling only: it is not imported by the application and does not ship any behaviour
`sales_app` relies on. `clients/` is only read: the client directories are copied into a throwaway temp directory,
and everything else lives in in-memory stores, so it never conflicts with a real deployment.

Usage:
    uv run python scripts/dev_admin_server.py

Then, in another terminal:
    cd web && npm run dev

Open http://localhost:5173/#/admin/login and sign in with:
    username: admin
    password: admin-dev-password

The Client space is at http://localhost:5173/#/login, with two seeded dev accounts:
    login: naga          password: naga-dev-password      (active)
    login: tounesimmo    password: tounesimmo-dev-password (inactive: login is refused)
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from sales_app.admin_auth import (
    AdminRole,
    AdminUser,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.client_profiles import ClientProfile, InMemoryClientProfileStore
from sales_app.config_store import InMemoryConfigStore
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore
from sales_app.settings import Settings
from sales_app.wiring import ChannelProvider, IntegrationBuilders
from sales_core.contracts import (
    BrainDecision,
    BrainRequest,
    Channel,
    ClientId,
    DecisionType,
    MessageDraft,
)
from sales_core.ports import LLMProvider
from sales_integrations.fakes import (
    FakeClock,
    FakeLLMProvider,
    FakeMessagingChannel,
    FakeSalesRepository,
)
from sales_integrations.whatsapp import WhatsAppWebhookParser

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin-dev-password"
REAL_CLIENTS_DIR = Path(__file__).resolve().parent.parent / "clients"


ENABLED_FOR_DEV = "naga"  # the app refuses to boot with no enabled client at all
CLIENT_DEV_PASSWORDS = {"naga": "naga-dev-password", "tounesimmo": "tounesimmo-dev-password"}


class DevBrain:
    """Generic stand-in so a copy of Naga can run without its real, still-unwritten Brain settings."""

    def __init__(self, llm: LLMProvider, settings: dict[str, Any]) -> None:
        self.llm = llm
        self.settings = settings

    def decide(self, request: BrainRequest) -> BrainDecision:
        return BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE,
            message=MessageDraft(text=f"hello {request.prospect.display_name}"),
        )


def create_dev_brain(llm: LLMProvider, settings: dict[str, Any]) -> DevBrain:
    return DevBrain(llm, settings)


def _enable_for_dev(client_dir: Path) -> None:
    """Dev-only override, applied to the temp COPY only: enable the client in dry_run with the
    stand-in Brain, keeping its real name, id and every other field."""
    path = client_dir / "client.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.update(enabled=True, mode="dry_run")
    raw["brain"] = {"factory": "dev_admin_server:create_dev_brain", "settings": {}}
    raw["sheets"]["spreadsheet_id"] = "dev-sheet"
    raw["llm"]["model"] = "gpt-4o-mini"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _copy_real_clients(destination: Path) -> list[str]:
    """Copy every real client definition (skipping `_template`) so the Admin shows the same
    clients as a real deployment. Returns their ids."""
    copied = []
    for source in sorted(REAL_CLIENTS_DIR.iterdir()):
        if (
            source.is_dir()
            and not source.name.startswith("_")
            and (source / "client.yaml").is_file()
        ):
            shutil.copytree(source, destination / source.name)
            copied.append(source.name)
    _enable_for_dev(destination / ENABLED_FOR_DEV)
    return copied


def _fake_builders(clock: FakeClock) -> IntegrationBuilders:
    def repository(config: Any) -> FakeSalesRepository:
        return FakeSalesRepository(ClientId(config.client_id))

    def llm(config: Any) -> FakeLLMProvider:
        return FakeLLMProvider(default="This is a fake LLM response for local Admin testing.")

    def channel(config: Any) -> FakeMessagingChannel:
        return FakeMessagingChannel(Channel.WHATSAPP, clock=clock)

    provider = ChannelProvider(
        kind=Channel.WHATSAPP,
        build=channel,
        routing_key=lambda config: config.phone_number_id,
        webhook_secret=lambda config: config.secrets.get_optional("whatsapp.app_secret"),
        build_parser=lambda secrets: WhatsAppWebhookParser(app_secrets=secrets, verify_token="dev"),
    )
    return IntegrationBuilders(
        repositories={"google_sheets": repository},
        llms={"openai": llm},
        channels={"whatsapp": provider},
    )


def main() -> None:
    import uvicorn

    clients_dir = Path(tempfile.mkdtemp(prefix="sales-agent-admin-dev-"))
    client_ids = _copy_real_clients(clients_dir)

    # False only for local http development (see Settings.admin_cookie_secure) -- exactly this
    # script's case: the browser talks to plain http://localhost, never https.
    settings = Settings(clients_dir=clients_dir, admin_cookie_secure=False)
    clock = FakeClock(datetime.now(UTC))
    env = {"GOOGLE_APPLICATION_CREDENTIALS": "unused", "OPENAI_API_KEY": "unused"}

    admin_users = InMemoryAdminUserStore()
    admin_users.create(
        AdminUser(
            id="dev-admin",
            username=ADMIN_USERNAME,
            password_hash=hash_password(ADMIN_PASSWORD),
            role=AdminRole.OWNER,
            created_at=datetime.now(UTC),
        )
    )

    application = build_application(
        settings,
        env=env,
        builders=_fake_builders(clock),
        clock=clock,
        config_store=InMemoryConfigStore(),
        client_profile_store=InMemoryClientProfileStore(
            [
                ClientProfile(
                    client_id=client_id,
                    login=client_id,
                    password_hash=hash_password(password),
                    must_change_password=False,
                    whatsapp_number="+216 20 000 000",
                    created_at=datetime.now(UTC),
                )
                for client_id, password in CLIENT_DEV_PASSWORDS.items()
                if client_id in client_ids
            ]
        ),
        client_session_store=InMemoryAdminSessionStore(),
        admin_user_store=admin_users,
        admin_session_store=InMemoryAdminSessionStore(),
        # Settings saves (OpenAI key, models) need a credential store; in memory, nothing on disk.
        secret_store=InMemorySecretStore(),
        secret_audit=InMemorySecretAudit(),
    )

    print("Clients copied from", REAL_CLIENTS_DIR, "->", ", ".join(client_ids))
    print(f"Admin login: username={ADMIN_USERNAME!r} password={ADMIN_PASSWORD!r}")
    for client_id, password in CLIENT_DEV_PASSWORDS.items():
        print(f"Client login: login={client_id!r} password={password!r}")
    print("Serving on http://127.0.0.1:8000 -- run `cd web && npm run dev` in another terminal,")
    print("then open http://localhost:5173/#/admin/login")

    uvicorn.run(
        create_app(application, run_scheduler=False), host="127.0.0.1", port=8000, workers=1
    )


if __name__ == "__main__":
    main()
