"""Local Admin server for manually testing the Admin UI -- no real secrets needed.

Boots the same FastAPI app the real deployment runs (`sales_app.api.create_app`), but wired to
fake integrations (no Google Sheets, OpenAI or WhatsApp calls leave the machine) and the real
client definitions generated from the platform's own skeleton (two demo clients, one active and one
inactive), so the Admin screens (Dashboard, Clients, Agents, Settings, and their detail pages) can be
exercised in a real browser without configuring any provider credentials. Because the app will not
boot without an enabled client, the active demo client runs in dry_run with a stand-in Brain.

This is dev tooling only: it is not imported by the application and does not ship any behaviour
`sales_app` relies on. The demo clients are written into a throwaway temp directory,
and everything else lives in in-memory stores, so it never conflicts with a real deployment (it
ignores SALES_AGENT_DATABASE_URL: every store is injected, so no database is opened).

Usage:
    uv run python scripts/dev_admin_server.py

Then, in another terminal:
    cd web && npm run dev

Open http://localhost:5173/#/admin/login and sign in with:
    username: admin
    password: admin-dev-password

The Client space is at http://localhost:5173/#/login, with two seeded dev accounts:
    login: demo           password: demo-dev-password           (active)
    login: demo-inactive  password: demo-inactive-dev-password  (inactive: login is refused)
"""

from __future__ import annotations

import os
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
from sales_app.resources.service import ResourceService
from sales_app.resources.store import InMemoryResourceStore
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


ENABLED_FOR_DEV = "demo"  # the app refuses to boot with no enabled client at all
CLIENT_DEV_PASSWORDS = {
    "demo": "demo-dev-password",
    "demo-inactive": "demo-inactive-dev-password",
}


class DevBrain:
    """Generic stand-in so a demo client can run without Brain settings of its own."""

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


def _seed_dev_clients(destination: Path) -> list[str]:
    """Write the two demo clients from the platform's skeleton. Only the active one is made able to
    run: dry_run, the stand-in Brain, a placeholder-free Sheet and model."""
    from sales_app.config.client_wiring import skeleton_wiring

    seeded = []
    for client_id, name, enabled in (
        ("demo", "Demo", True),
        ("demo-inactive", "Demo Inactive", False),
    ):
        raw = skeleton_wiring(client_id, name, enabled=enabled, engine=None)
        if enabled:
            raw["brain"] = {"factory": "dev_admin_server:create_dev_brain", "settings": {}}
            raw["sheets"]["spreadsheet_id"] = "dev-sheet"
            raw["llm"]["model"] = "gpt-4o-mini"
        (destination / client_id).mkdir(parents=True)
        (destination / client_id / "client.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        seeded.append(client_id)
    return seeded


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
    client_ids = _seed_dev_clients(clients_dir)

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

    # Product documents live in the database in a real deployment; here, in memory, so the Client
    # screens for them can be tried without one (pages are fetched for real, so use public https).
    application.product_resources = ResourceService(InMemoryResourceStore(), clock=clock.now)

    print("Demo clients:", ", ".join(client_ids))
    print(f"Admin login: username={ADMIN_USERNAME!r} password={ADMIN_PASSWORD!r}")
    for client_id, password in CLIENT_DEV_PASSWORDS.items():
        print(f"Client login: login={client_id!r} password={password!r}")
    port = int(os.environ.get("DEV_PORT", "8000"))
    print(f"Serving on http://127.0.0.1:{port} -- run `cd web && npm run dev` in another terminal,")
    print("then open http://localhost:5173/#/admin/login")

    uvicorn.run(
        create_app(application, run_scheduler=False), host="127.0.0.1", port=port, workers=1
    )


if __name__ == "__main__":
    main()
