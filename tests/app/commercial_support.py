"""The harness of the commercial journey (`test_commercial_journey_e2e.py`).

One deployment, as a client would meet it, with doubles only where a real account would be needed.

REAL: the `Application` over a real SQLite database migrated by Alembic (accounts, sessions, the
versioned settings, the operational store, the write-back ledger, the mailbox positions, the LLM
usage, plans, invoices, daily reports, the send-cap counter), the Admin and Client HTTP routes, the
scheduler, the Engine, the Brain package loading, the real Google Sheets source (`SheetsProspectSource`)
and its write-back, the real e-mail channel and inbox (`MailChannel`, `ImapMailbox`), the consent
registry, the daily report service and the payment webhook.

SIMULATED: the Google Sheets API (`FakeSheetsApi`, an in-memory grid), the SMTP and IMAP servers
(doubles at the socket's edge), the model provider, the clock, and the payment provider (the webhook
is signed with the real HMAC scheme and a test secret). Nothing here proves a real Google, mail
server, Telegram, OpenAI or payment-provider call works.

A restart is a restart: `restart()` closes the application and builds it again over the same
database, spreadsheets and mailboxes, with adapters that remember nothing.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import yaml
from app_support import InlineExecutor, client_yaml, env_for, fake_builders, make_settings
from factories import NOW
from fastapi.testclient import TestClient
from sheets_support import TUNIS, FakeSheetsApi
from test_admin_clients_api import PASSWORD, editor, owner
from test_client_agent_api import LOGIN, PASSWORDS, give_profile, mark_source_tested
from test_mail_channel import FakeSmtp
from test_mail_mailbox import FakeImap, raw_mail

from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.llm_usage_rows import SqlLLMUsageStore
from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    InMemorySecretAudit,
    InMemorySecretStore,
    client_scope,
)
from sales_app.sourcing.consent.files import open_file_registry
from sales_app.sourcing.sql_store import SqlOperationalStore
from sales_app.wiring import default_builders
from sales_app.wiring.providers import email as email_provider
from sales_app.wiring.providers import google_sheets
from sales_app.wiring.writeback import WritebackSupport, default_policies, sql_ledgers
from sales_brain_kit import ConsultativeSalesSpec
from sales_core.contracts import (
    BrainDecision,
    DecisionType,
    MessageDraft,
    SalesActionType,
)
from sales_integrations.fakes import FakeClock
from sales_integrations.mail import MailChannel

BRAIN = "commercial_support:create_brain"
OWNER_ADDRESS = {"alpha": "owner-alpha@example.com", "beta": "owner-beta@example.com"}

# The columns Revansa writes its results to, in the client's own words (French sheet).
HEADERS = {
    "status": "Statut",
    "last_contacted_at": "Dernier contact",
    "last_inbound_at": "Derniere reponse",
    "follow_up_count": "Relances",
    "next_action_type": "Prochaine action",
    "next_action_due_at": "Echeance",
}
WRITEBACK = {"columns": HEADERS, "propagate_opt_out": True}


# ---- the Brain: a stand-in loaded by dotted path, as a real one is ---------------------------------

SETTINGS_SPEC = ConsultativeSalesSpec()


class JourneyBrain:
    """Writes a short message naming the products of the lane it is working for, and always asks
    to be reminded three days later (so a follow-up is planned after every decision)."""

    def __init__(self, llm: Any, settings: Mapping[str, object]) -> None:
        self.settings = settings

    def decide(self, request: Any) -> BrainDecision:
        products = ",".join(request.lane.product_ids) if request.lane is not None else "all"
        verb = "Re" if request.action.action_type is SalesActionType.REPLY else "Hello"
        return BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE,
            message=MessageDraft(text=f"{verb} {request.prospect.display_name} [{products}]"),
            next_follow_up_at=request.now + timedelta(days=3),
            next_action_type=SalesActionType.FOLLOW_UP,
            new_stage="warm",
            rationale="journey",
        )


def create_brain(llm: Any, settings: Mapping[str, object]) -> JourneyBrain:
    return JourneyBrain(llm, settings)


def brain_settings(*product_ids: str) -> dict[str, Any]:
    from kit_support import minimal

    settings = minimal()
    settings["catalog"] = {"products": [{"id": i, "name": i, "summary": "s"} for i in product_ids]}
    return settings


# ---- the doubles -----------------------------------------------------------------------------------


class Mailbox(FakeImap):
    """A mailbox that new mail can be delivered to."""

    def reply(self, sender: str, text: str, message_id: str, *, at) -> None:
        raw = raw_mail(sender=sender, text=text, message_id=message_id, date=format_datetime(at))
        self.messages[max(self.messages, default=0) + 1] = {"raw": raw, "seen": False}


def mailbox_settings(cid: str) -> dict[str, str]:
    return {
        "from_address": f"{cid}@example.org",
        "from_name": f"{cid.title()} Sales",
        "username": f"{cid}@example.org",
        "smtp_host": "smtp.example.org",
        "imap_host": "imap.example.org",
    }


def sheet_id(cid: str, source: str) -> str:
    return f"sheet-{cid}-{source}"


def new_sheet(*, writeback: bool) -> FakeSheetsApi:
    api = FakeSheetsApi()
    api.create_layout({f"prospects.{k}": h for k, h in HEADERS.items()} if writeback else None)
    return api


def raw_client(cid: str, sources: list[dict], lanes: list[dict], products: tuple[str, ...]) -> dict:
    data = client_yaml(cid)
    data["brain"] = {"factory": BRAIN, "settings": brain_settings(*products)}
    data["sources"], data["lanes"] = sources, lanes
    data["channel"] = {"provider": "email", "settings": mailbox_settings(cid)}
    return data


def sheet_source(cid: str, source: str) -> dict:
    return {
        "id": source,
        "provider": "google_sheets",
        "settings": {"spreadsheet_id": sheet_id(cid, source), "writeback": dict(WRITEBACK)},
    }


# ---- the deployment ---------------------------------------------------------------------------------


class Journey:
    """alpha (two circuits, one product each) and beta (one circuit), behind one application."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "clients"
        self.root.mkdir()
        self.url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
        engine = create_database_engine(self.url)
        upgrade_database(engine)
        engine.dispose()
        self.tmp_path = tmp_path
        self.clock = FakeClock(NOW)  # 10:00 on Thursday 15 January 2026, in Tunis
        self.clients = {
            "alpha": raw_client(
                "alpha",
                [sheet_source("alpha", "sheet_a"), sheet_source("alpha", "sheet_b")],
                [
                    {"id": "offer_a", "source": "sheet_a", "channel": "email", "products": ["a"]},
                    {"id": "offer_b", "source": "sheet_b", "channel": "email", "products": ["b"]},
                ],
                ("a", "b"),
            ),
            "beta": raw_client(
                "beta",
                [sheet_source("beta", "sheet_x")],
                [{"id": "main", "source": "sheet_x", "channel": "email", "products": ["a"]}],
                ("a",),
            ),
        }
        self.sheets = {
            ("alpha", "sheet_a"): new_sheet(writeback=True),
            ("alpha", "sheet_b"): new_sheet(writeback=True),
            ("beta", "sheet_x"): new_sheet(writeback=True),
        }
        self.smtp = {cid: FakeSmtp() for cid in self.clients}
        self.imap = {cid: Mailbox() for cid in self.clients}
        for cid, raw in self.clients.items():
            (self.root / cid).mkdir()
            (self.root / cid / "client.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.secrets = InMemorySecretStore()
        self.audit = InMemorySecretAudit()
        self.secrets.set(GLOBAL_SCOPE, "channels.allowed", "whatsapp,email", by="test")
        for cid in self.clients:
            self.secrets.set(client_scope(cid), "email.password", f"pass-{cid}", by="test")
        self.engine = create_database_engine(self.url)  # the harness' own door to the database
        for cid in self.clients:  # what `sales-agent consent init` does for each client
            open_file_registry(
                self.settings().secrets_dir, self.secrets, self.audit, cid
            ).initialise(actor="cli:test")
        self.application = None
        self.start()
        self.application.account_stores.admin_users.create(owner())
        self.application.account_stores.admin_users.create(
            editor("bob", client_ids=frozenset({"beta"}))
        )
        for index, cid in enumerate(self.clients):
            give_profile(self.application, cid, f"+216 20 000 00{index + 1}")
            mark_source_tested(self.application, cid)

    # ---- the application ---------------------------------------------------------------------

    def settings(self):
        return make_settings(
            self.root,
            database_url=self.url,
            secrets_dir=self.tmp_path / "_secrets",
        )

    def _builders(self, settings):
        engine = self.engine
        base = default_builders(
            settings,
            operational_stores=lambda cid: SqlOperationalStore(SqlOperationalRows(engine, cid)),
            consent=lambda cid: open_file_registry(
                settings.secrets_dir, self.secrets, self.audit, cid
            ),
            writeback=WritebackSupport(
                open_ledger=sql_ledgers(engine), policies=default_policies()
            ),
            llm_usage=lambda cid: SqlLLMUsageStore(engine, cid),
        )
        fakes, self.made = fake_builders(self.clock)

        def build_source(config, instance):
            return google_sheets.build_prospect_source(
                self.sheets[(config.client_id, instance.id)],
                client_id=config.client_id,
                source_id=instance.id,
                settings=instance.settings,
                timezone=TUNIS,
            )

        def send_through(config):
            # The descriptor's own settings and password; the socket is a double, and the clock is
            # the test's (the production build leaves it to the system clock, which would stamp
            # every message with the real time of the run).
            return MailChannel(
                email_provider.resolve_mailbox(config).smtp,
                factory=lambda s: self.smtp[config.client_id],
                clock=self.clock,
            )

        def read_through(config):
            return email_provider.build_inbox(
                config, imap_factory=lambda s: self.imap[config.client_id]
            )

        email = replace(base.channels["email"], build=send_through, build_inbox=read_through)
        return replace(
            base,
            llms=fakes.llms,
            channels={"email": email},
            source_instances={"google_sheets": build_source},
        )

    def start(self) -> None:
        settings = self.settings()
        self.application = build_application(
            settings,
            env=env_for(*self.clients),
            builders=self._builders(settings),
            clock=self.clock,
            executor=InlineExecutor(),
            secret_store=self.secrets,
            secret_audit=self.audit,
            refresh_executor=InlineExecutor(),
        )

    def restart(self) -> None:
        self.application.close()
        self.start()

    def close(self) -> None:
        self.application.close()
        self.engine.dispose()

    # ---- the people ----------------------------------------------------------------------------

    def http(self) -> TestClient:
        return TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )

    def admin(self, username: str = "root") -> TestClient:
        client = self.http()
        response = client.post(
            "/admin/v1/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return client

    def browser(self, cid: str) -> TestClient:
        client = self.http()
        response = client.post(
            LOGIN, json={"login": f"{cid}.login", "password": PASSWORDS.get(cid, "naga-secret-789")}
        )
        assert response.status_code == 200, response.text
        return client

    def add_prospect(self, cid: str, source: str, pid: str, name: str, **cells: Any) -> str:
        values = {
            "prospect_id": pid,
            "display_name": name,
            "phone": "",
            "email": "",
            "Statut": "new",
        }
        self.sheets[(cid, source)].human_add_row("Prospects", **(values | cells))
        return f"{source}:{pid}"

    # ---- time and work -------------------------------------------------------------------------

    def tick(self, advance: timedelta = timedelta(minutes=10)):
        self.clock.advance(advance)
        return self.run_due()

    def run_due(self):
        futures = self.application.scheduler.tick(self.clock.now())
        return [result for future in futures for result in future.result()]

    def goto(self, moment) -> list:
        self.clock.set(moment)
        return self.run_due()

    # ---- what the doubles and the database hold ------------------------------------------------

    def store(self, cid: str) -> SqlOperationalStore:
        """The client's operational store, opened on its own connection to the database."""
        return SqlOperationalStore(SqlOperationalRows(self.engine, cid))

    def sheet(self, cid: str, source: str, pid: str) -> dict[str, Any]:
        api = self.sheets[(cid, source)]
        row = api.find_row("Prospects", "prospect_id", pid)
        return {h: api.cell("Prospects", row, h) for h in api.headers("Prospects")}

    def prospect_mails(self, cid: str) -> list:
        """What left through the client's SMTP server for a prospect (not the reports)."""
        return [m for m in self.smtp[cid].sent if m["To"] != OWNER_ADDRESS[cid]]

    def report_mails(self, cid: str) -> list:
        return [m for m in self.smtp[cid].sent if m["To"] == OWNER_ADDRESS[cid]]

    def reply(self, cid: str, sender: str, text: str, message_id: str) -> None:
        self.imap[cid].reply(sender, text, message_id, at=self.clock.now())

    def unread(self, cid: str) -> list[int]:
        return [uid for uid, m in self.imap[cid].messages.items() if not m["seen"]]
