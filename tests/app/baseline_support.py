"""Helpers for the configuration baseline tests (the safety net around the Step 2 migration).

The baseline tests pin what the application does *today* with a client's configuration, so that
any later change to how configuration is read, normalised, wired or exposed is a visible diff.
They never assert what the behaviour *should* be: a surprising value in a golden file is recorded
as it is, not corrected here.

Golden files live in `baseline_fixtures/golden/`. They are compared, never rewritten, unless the
environment variable `BASELINE_RECORD=1` is set (only to record a deliberately changed baseline).
"""

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from app_support import (
    VERIFY_TOKEN,
    InlineExecutor,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)
from factories import NOW
from fastapi.testclient import TestClient

from sales_app.admin_auth import (
    AdminRole,
    AdminUser,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.api import create_app
from sales_app.bootstrap import Application, build_application
from sales_app.client_profiles import InMemoryClientProfileStore
from sales_app.config import resolve_client_config
from sales_app.config_store import InMemoryConfigStore
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore
from sales_app.wiring import integrations as integrations_module
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel
from sales_integrations.fakes import (
    FakeClock,
    FakeLLMProvider,
    FakeMessagingChannel,
    FakeSalesRepository,
)

FIXTURES = Path(__file__).resolve().parent / "baseline_fixtures"
GOLDEN = FIXTURES / "golden"
RECORD = os.environ.get("BASELINE_RECORD") == "1"

PASSWORD = "correct-horse-battery-staple"

# Every credential key the runtime resolves for a client (see `sales_app.config.secrets`).
SECRET_KEYS = (
    "openai.api_key",
    "openai.organization",
    "openai.project",
    "openai.base_url",
    "sheets.credentials_file",
    "whatsapp.access_token",
    "whatsapp.phone_number_id",
    "whatsapp.app_secret",
    "whatsapp.api_version",
)


# ---- fixtures and golden files -----------------------------------------------------------------


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict[str, Any]:
    return json.loads(fixture_text(name))


def place_yaml(root: Path, client_id: str, fixture: str) -> Path:
    """Write a fixture as `<root>/<client_id>/client.yaml` (its `client_id` must match)."""
    return write_client(root, client_id, raw=fixture_text(fixture))


def jsonable(value: Any) -> Any:
    """The value as plain JSON data (what a golden file holds); sets are sorted."""
    return json.loads(json.dumps(value, default=_default, sort_keys=True))


def _default(value: Any) -> Any:
    if isinstance(value, set | frozenset):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "value"):  # an enum
        return value.value
    raise TypeError(f"cannot serialise {type(value).__name__}")


def assert_golden(name: str, actual: Any) -> None:
    """Compare `actual` with `baseline_fixtures/golden/<name>.json` (or record it)."""
    data = jsonable(actual)
    path = GOLDEN / f"{name}.json"
    if RECORD:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return
    assert path.is_file(), f"missing golden file {path.name}; record it with BASELINE_RECORD=1"
    assert data == json.loads(path.read_text(encoding="utf-8")), f"{name} differs from its golden"


# ---- configuration snapshots -------------------------------------------------------------------


def snapshot_config(config) -> dict[str, Any]:
    """Everything a resolved `ClientConfig` carries that behaviour depends on."""
    return {
        "client_id": config.client_id,
        "directory": config.directory.name,
        "spec": config.spec.model_dump(mode="json"),
        "effective_mode": config.effective_mode.value,
        "mode_clamped": config.mode_clamped,
        "config_source": config.config_source,
        "config_version": config.config_version,
        "ready": config.ready,
        "missing_credentials": list(config.missing_credentials),
        "phone_number_id": config.phone_number_id,
        "secrets": {key: config.secrets.get_optional(key) for key in SECRET_KEYS},
    }


def snapshot_resolution(resolution) -> dict[str, Any]:
    return {
        "disabled": resolution.disabled,
        "errors": list(resolution.errors),
        "config": None if resolution.config is None else snapshot_config(resolution.config),
    }


def resolve(
    root: Path,
    client_id: str = "alpha",
    *,
    env: Mapping[str, str] | None = None,
    ceiling: ApprovalMode = ApprovalMode.AUTONOMOUS,
    store=None,
    llm_registry=None,
    secret_store=None,
):
    kwargs: dict[str, Any] = {}
    if llm_registry is not None:
        kwargs["llm_registry"] = llm_registry
    return resolve_client_config(
        root / client_id,
        env=env if env is not None else env_for(client_id),
        mode_ceiling=ceiling,
        store=store,
        secret_store=secret_store,
        **kwargs,
    )


def store_with_document(client_id: str, document: Mapping[str, Any]) -> InMemoryConfigStore:
    """A config store whose current version is `document`, stored exactly as given."""
    store = InMemoryConfigStore()
    store.publish(client_id, dict(document), author="baseline", expected_parent=None)
    return store


def settings_secret_store(models: list[tuple[str, str, bool]] | None = None, **globals_: str):
    """An in-memory credential store holding the Settings model list and/or global credentials.

    `globals_` keys use `__` for the dot (`openai__model_sol` is `openai.model_sol`).
    """
    from sales_app.secret_store import GLOBAL_SCOPE

    store = InMemorySecretStore()
    if models is not None:
        payload = json.dumps([{"name": n, "id": i, "active": a} for n, i, a in models])
        store.set(GLOBAL_SCOPE, "openai.models", payload, by="baseline")
    for key, value in globals_.items():
        store.set(GLOBAL_SCOPE, key.replace("__", "."), value, by="baseline")
    return store


# ---- recording vendor classes -------------------------------------------------------------------


@dataclass
class Recording:
    """What the real factories of `sales_app.wiring.integrations` handed to the vendor classes."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    llms: list[Any] = field(default_factory=list)

    def named(self, name: str) -> list[dict[str, Any]]:
        return [arguments for call, arguments in self.calls if call == name]


def install_recording_vendors(monkeypatch) -> Recording:
    """Replace the four vendor classes the default builders construct with recorders.

    The real builders (`default_builders`) then run unchanged: each records exactly the arguments
    it passes, and returns an in-memory fake that satisfies the Core port. No network, no file.
    """
    recording = Recording()
    calls = recording.calls

    class RecordingApi:
        @classmethod
        def from_service_account_file(cls, path):
            calls.append(("GoogleSheetsApi.from_service_account_file", {"path": path}))
            return cls()

    class RecordingRepository(FakeSalesRepository):
        def __init__(self, api, *, client_id, spreadsheet_id, timezone, tabs, column_mapping):
            calls.append(
                (
                    "GoogleSheetsRepository",
                    {
                        "api_is_the_recorded_api": isinstance(api, RecordingApi),
                        "client_id": str(client_id),
                        "spreadsheet_id": spreadsheet_id,
                        "timezone": str(timezone),
                        "tabs": asdict(tabs) if is_dataclass(tabs) else str(tabs),
                        "column_mapping": dict(column_mapping),
                    },
                )
            )
            super().__init__(client_id)

    class RecordingOpenAI(FakeLLMProvider):
        def __init__(self, **kwargs):
            calls.append(("OpenAIProvider", dict(kwargs)))
            recording.llms.append(self)
            super().__init__(default="fake")

    class RecordingWhatsApp(FakeMessagingChannel):
        def __init__(self, **kwargs):
            calls.append(("WhatsAppChannel", dict(kwargs)))
            super().__init__(Channel.WHATSAPP, clock=FakeClock(NOW))

    monkeypatch.setattr(integrations_module, "GoogleSheetsApi", RecordingApi)
    monkeypatch.setattr(integrations_module, "GoogleSheetsRepository", RecordingRepository)
    monkeypatch.setattr(integrations_module, "OpenAIProvider", RecordingOpenAI)
    monkeypatch.setattr(integrations_module, "WhatsAppChannel", RecordingWhatsApp)
    return recording


def engine_snapshot(runtime) -> dict[str, Any]:
    """How the Engine of a client runtime was built (read from its construction arguments)."""
    engine = runtime.engine
    config, client, inbound = engine._config, engine._client, engine._config.inbound
    return {
        "client": {
            "client_id": client.client_id,
            "display_name": client.display_name,
            "timezone": client.timezone,
            "settings": dict(client.settings),
        },
        "mode": config.mode.value,
        "daily_action_limit": config.daily_action_limit,
        "max_attempts": config.max_attempts,
        "opt_out_keywords": sorted(config.opt_out_keywords),
        "inbound": {
            "spontaneous_conversations": inbound.spontaneous_conversations,
            "unknown_sender": inbound.unknown_sender.value,
            "max_new_per_day": inbound.max_new_per_day,
        },
        "channel": {"class": type(engine._channel).__name__, "kind": engine._channel.channel.value},
        "repository_class": type(engine._repo).__name__,
        "has_facts_reader": engine._facts is not None,
        "has_facts_writer": engine._facts_writer is not None,
        "stagger_seconds": runtime.stagger.total_seconds(),
        "missing": list(runtime.missing),
    }


# ---- the Admin API harness ---------------------------------------------------------------------


class AdminHarness:
    """A full application (all stores in memory) behind the real FastAPI app, logged in as owner."""

    def __init__(
        self,
        root: Path,
        *,
        env: Mapping[str, str],
        builders=None,
        secret_store: InMemorySecretStore | None = None,
        **settings_kw: Any,
    ) -> None:
        self.clock = FakeClock(NOW)
        self.users = InMemoryAdminUserStore()
        self.users.create(
            AdminUser(
                id="id-root",
                username="root",
                password_hash=hash_password(PASSWORD),
                role=AdminRole.OWNER,
                created_at=NOW,
            )
        )
        self.secrets = secret_store if secret_store is not None else InMemorySecretStore()
        self.builders = builders if builders is not None else fake_builders(self.clock)[0]
        self.application: Application = build_application(
            make_settings(root, **settings_kw),
            env=env,
            builders=self.builders,
            clock=self.clock,
            executor=InlineExecutor(),
            refresh_executor=InlineExecutor(),
            config_store=InMemoryConfigStore(),
            admin_user_store=self.users,
            admin_session_store=InMemoryAdminSessionStore(),
            client_profile_store=InMemoryClientProfileStore(),
            client_session_store=InMemoryAdminSessionStore(),
            secret_store=self.secrets,
            secret_audit=InMemorySecretAudit(),
        )
        self.client = TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )
        login = self.client.post(
            "/admin/v1/auth/login", json={"username": "root", "password": PASSWORD}
        )
        assert login.status_code == 200, login.text

    def get(self, path: str) -> dict[str, Any]:
        response = self.client.get(f"/admin/v1{path}")
        return {"status": response.status_code, "body": response.json()}

    def put(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        response = self.client.put(f"/admin/v1{path}", json=dict(body))
        return {"status": response.status_code, "body": response.json()}

    def current_version(self, client_id: str) -> dict[str, Any] | None:
        """The newest stored settings version, without the time it was written."""
        from sales_app.config_store.models import thaw_document

        version = self.application.config_store.current(client_id)
        if version is None:
            return None
        return {
            "version": version.version,
            "parent": version.parent,
            "author": version.author,
            "note": version.note,
            "document": thaw_document(version.document),
        }


__all__ = [
    "PASSWORD",
    "VERIFY_TOKEN",
    "AdminHarness",
    "Recording",
    "assert_golden",
    "engine_snapshot",
    "fixture_json",
    "fixture_text",
    "install_recording_vendors",
    "jsonable",
    "place_yaml",
    "resolve",
    "settings_secret_store",
    "snapshot_config",
    "snapshot_resolution",
    "store_with_document",
]
