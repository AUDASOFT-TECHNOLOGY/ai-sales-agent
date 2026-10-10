"""Builders for sales_app tests: client directories, environments, and fake integrations."""

from collections.abc import Mapping
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from factories import NOW

from sales_app.bootstrap import Application, build_application
from sales_app.config.offered_models import OfferedModel
from sales_app.llm_models import save_models, stored_models
from sales_app.settings import Settings
from sales_app.wiring import ChannelProvider, IntegrationBuilders
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel, ClientId
from sales_core.engine import RunReport
from sales_integrations.fakes import (
    FakeClock,
    FakeLLMProvider,
    FakeMessagingChannel,
    FakeSalesRepository,
)
from sales_integrations.whatsapp import WhatsAppWebhookParser

VERIFY_TOKEN = "verify-me"
SHARED_ENV = {"GOOGLE_APPLICATION_CREDENTIALS": "creds.json", "OPENAI_API_KEY": "sk-test"}


def phone_id(cid: str) -> str:
    return f"phone-{cid}"


def app_secret(cid: str) -> str:
    return f"secret-{cid}"


def deep_merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def client_yaml(cid: str = "alpha", **overrides: Any) -> dict[str, Any]:
    upper = cid.upper()
    base = {
        "client_id": cid,
        "display_name": cid.title(),
        "enabled": True,
        "mode": "approval",
        "timezone": "Africa/Tunis",
        "brain": {"factory": "app_test_brains:good_brain", "settings": {"tone": cid}},
        "sheets": {"spreadsheet_id": f"sheet-{cid}"},
        "llm": {"model": "test-model"},
        "whatsapp": {
            "token_env": f"{upper}_TOKEN",
            "phone_number_id_env": f"{upper}_PHONE_ID",
            "app_secret_env": f"{upper}_APP_SECRET",
        },
    }
    return deep_merge(base, overrides)


def env_for(*client_ids: str) -> dict[str, str]:
    env = dict(SHARED_ENV)
    for cid in client_ids:
        upper = cid.upper()
        env |= {
            f"{upper}_TOKEN": f"token-{cid}",
            f"{upper}_PHONE_ID": phone_id(cid),
            f"{upper}_APP_SECRET": app_secret(cid),
        }
    return env


def write_client(
    root: Path, cid: str = "alpha", *, raw: str | None = None, **overrides: Any
) -> Path:
    directory = root / cid
    directory.mkdir(parents=True, exist_ok=True)
    text = raw if raw is not None else yaml.safe_dump(client_yaml(cid, **overrides))
    (directory / "client.yaml").write_text(text, encoding="utf-8")
    return directory


def declare_models(credentials: Any, *model_ids: str, active: bool = True) -> None:
    """Have the Admin's model list in Settings hold these Model IDs, besides those it already holds.

    What an Admin assignment is checked against: a model can be given to a client only if it is
    configured there (and on). Needs a credential store, so the application under test must have
    been given a secrets directory and key. The name of each model is its id."""
    current = list(stored_models(credentials) or [])
    held = {m.id for m in current}
    added = [
        OfferedModel(model_id, model_id, active) for model_id in model_ids if model_id not in held
    ]
    save_models(credentials, [*current, *added], actor="tests")


def make_settings(root: Path, **kw: Any) -> Settings:
    return Settings(clients_dir=root, whatsapp_verify_token=VERIFY_TOKEN, **kw)


class InlineExecutor(Executor):
    """Runs submitted work immediately, in the caller's thread: deterministic scheduler tests."""

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:  # noqa: BLE001
            future.set_exception(error)
        return future


@dataclass
class Made:
    """The fake integrations the builders created, per client."""

    repositories: dict[str, FakeSalesRepository] = field(default_factory=dict)
    channels: dict[str, FakeMessagingChannel] = field(default_factory=dict)
    llms: dict[str, FakeLLMProvider] = field(default_factory=dict)


def fake_builders(clock: FakeClock | None = None) -> tuple[IntegrationBuilders, Made]:
    made = Made()
    clock = clock or FakeClock(NOW)

    def repository(config) -> FakeSalesRepository:
        return made.repositories.setdefault(
            config.client_id, FakeSalesRepository(ClientId(config.client_id))
        )

    def llm(config) -> FakeLLMProvider:
        return made.llms.setdefault(config.client_id, FakeLLMProvider(default="fake"))

    def channel(config) -> FakeMessagingChannel:
        return made.channels.setdefault(
            config.client_id, FakeMessagingChannel(Channel.WHATSAPP, clock=clock)
        )

    provider = ChannelProvider(
        kind=Channel.WHATSAPP,
        build=channel,
        routing_key=lambda config: config.phone_number_id,
        webhook_secret=lambda config: config.secrets.get_optional("whatsapp.app_secret"),
        build_parser=lambda secrets: WhatsAppWebhookParser(
            app_secrets=secrets, verify_token=VERIFY_TOKEN
        ),
    )
    builders = IntegrationBuilders(
        repositories={"google_sheets": repository},
        llms={"openai": llm},
        channels={"whatsapp": provider},
    )
    return builders, made


def build_test_application(
    root: Path,
    *client_ids: str,
    clock: FakeClock | None = None,
    ceiling: ApprovalMode | None = None,
    **client_overrides: Any,
) -> tuple[Application, Made, FakeClock]:
    """Write `client_ids` (each valid), then build the whole application with fake adapters."""
    clock = clock or FakeClock(NOW)
    for cid in client_ids:
        write_client(root, cid, **client_overrides)
    builders, made = fake_builders(clock)
    application = build_application(
        make_settings(root),
        env=env_for(*client_ids),
        builders=builders,
        clock=clock,
        executor=InlineExecutor(),
        mode_ceiling=ceiling,
    )
    return application, made, clock


class SpyEngine:
    """Has exactly the three methods the scheduler may call; anything else would raise."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_on: set[str] = set()
        self.problems: dict[str, tuple[str, ...]] = {}

    def _go(self, name: str) -> RunReport:
        self.calls.append(name)
        if name in self.fail_on:
            raise RuntimeError(f"{name} exploded")
        return RunReport(errors=self.problems.get(name, ()))

    def recover_pending_replies(self) -> RunReport:
        return self._go("recover")

    def prepare(self) -> RunReport:
        return self._go("prepare")

    def dispatch(self) -> RunReport:
        return self._go("dispatch")
