"""A new client from creation to a started agent, with no folder and no `client.yaml` at any point.

The whole journey goes through the real routes on a SQLite database: the Admin creates the account,
renames it and gives it a quota; the client signs in, writes its settings and its Sheet; the platform
puts it in force without a restart; the client starts its agent. Nothing but the historical client
(`alpha`, which a deployment needs to start) has a folder.
"""

from datetime import UTC, datetime

import pytest
from app_support import (
    InlineExecutor,
    declare_models,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)
from fastapi.testclient import TestClient
from kit_support import minimal
from test_admin_clients_api import PASSWORD, owner
from test_client_agent_api import AGENT, LOGIN, READINESS, SETTINGS, START, mark_source_tested

from sales_app.admin_auth import InMemoryAdminUserStore
from sales_app.api import create_app
from sales_app.api.admin_clients import publish_client_settings
from sales_app.bootstrap import build_application
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.secret_store import generate_master_key
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
TEMP_PASSWORD = "temp-pass-123"


@pytest.fixture
def world(tmp_path):
    url = f"sqlite:///{(tmp_path / 'revansa.db').as_posix()}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    engine.dispose()
    write_client(tmp_path, "alpha")  # a deployment starts with one enabled (historical) client
    builders, _made = fake_builders(FakeClock(NOW))
    application = build_application(
        make_settings(
            tmp_path,
            database_url=url,
            secrets_dir=tmp_path / "_secrets",
            secrets_key=generate_master_key(),
        ),
        env=env_for("alpha"),
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
        refresh_executor=InlineExecutor(),
    )
    declare_models(application.credentials, "gpt-4o")
    application.account_stores.admin_users.create(owner())
    yield application
    application.close()


def browser(application) -> TestClient:
    return TestClient(create_app(application, run_scheduler=False), base_url="https://testserver")


def folders(root) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))


def test_a_new_client_goes_from_creation_to_a_started_agent_without_any_file(world, tmp_path):
    admin = browser(world)
    admin.post("/admin/v1/auth/login", json={"username": "root", "password": PASSWORD})

    # -- creation: the database only
    created = admin.post(
        "/admin/v1/clients",
        json={
            "display_name": "Acme Corp",
            "login": "acme.admin",
            "temporary_password": TEMP_PASSWORD,
            "llm_provider": "openai",
            "llm_model": "gpt-4o",
            "status": "active",
            "whatsapp_number": "+216 22 768 307",
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["client_id"]
    assert cid == "acme-corp" and folders(tmp_path) == ["alpha"]
    record = world.client_records.get(cid)
    assert (record.display_name, record.enabled, record.engine) == (
        "Acme Corp",
        True,
        "consultative",
    )
    assert cid in [c["client_id"] for c in admin.get("/admin/v1/clients").json()]
    assert admin.get(f"/admin/v1/clients/{cid}").json()["llm_model"] == "gpt-4o"

    # -- name and quota: a published settings version, not a file
    patched = admin.patch(
        f"/admin/v1/clients/{cid}", json={"display_name": "Acme Group", "daily_action_limit": 40}
    )
    assert patched.status_code == 200, patched.text
    detail = admin.get(f"/admin/v1/clients/{cid}").json()
    assert (detail["display_name"], detail["daily_action_limit"]) == ("Acme Group", 40)
    assert detail["llm_model"] == "gpt-4o" and folders(tmp_path) == ["alpha"]
    assert {v.author for v in world.config_store.history(cid)} == {"admin:root"}

    # -- the client signs in and sees what its agent still needs
    client = browser(world)
    login = client.post(LOGIN, json={"login": "acme.admin", "password": TEMP_PASSWORD})
    assert login.status_code == 200, login.text
    checks = {c["id"]: c["ok"] for c in client.get(READINESS).json()["checks"]}
    assert checks["enabled_by_administrator"] and checks["brain_assigned"]
    assert checks["model_assigned"] and not checks["settings_valid"]

    # -- its configuration: business settings, then its Sheet
    version = world.config_store.current(cid).version
    saved = client.put(
        SETTINGS, json={"expected_version": version, "settings": {"commercial": minimal()}}
    )
    assert saved.status_code == 200, saved.text
    publish_client_settings(
        world,
        cid,
        {"sheets": {"spreadsheet_id": "sheet-acme"}},
        expected_version=world.config_store.current(cid).version,
        author="client:acme-corp",
        top_keys=(),
        sections={"sheets": ("spreadsheet_id", "prospects_tab")},
    )
    mark_source_tested(world, cid)

    # -- in force without a restart, and nothing was ever written to disk
    world.runtime_manager.refresh_client(cid).result(timeout=10)
    assert world.runtime_manager.status(cid).state == "applied", world.runtime_manager.status(cid)
    assert world.registry.get(cid) is not None
    assert world.registry.get_config(cid).spec.brain.agent == "consultative"
    assert folders(tmp_path) == ["alpha"]

    # -- the agent is prepared and started by the client
    readiness = client.get(READINESS).json()
    assert readiness["ready"] is True, readiness
    started = client.post(START)
    assert started.status_code == 200, started.text
    assert client.get(AGENT).json()["active"] is True
    assert world.agent_may_work(cid) is True


def test_a_deployment_without_a_database_still_creates_the_file(tmp_path):
    """The files stay the mechanism of a deployment with no database (and of historical clients)."""
    write_client(tmp_path, "alpha")
    users = InMemoryAdminUserStore()
    users.create(owner())
    builders, _made = fake_builders(FakeClock(NOW))
    application = build_application(
        make_settings(
            tmp_path,
            admin_dir=tmp_path / "_admin",
            profiles_dir=tmp_path / "_profiles",
            config_dir=tmp_path / "_config",
            secrets_dir=tmp_path / "_secrets",
            secrets_key=generate_master_key(),
        ),
        env=env_for("alpha"),
        builders=builders,
        clock=FakeClock(NOW),
        executor=InlineExecutor(),
        admin_user_store=users,
    )
    try:
        assert application.client_records is None
        admin = browser(application)
        admin.post("/admin/v1/auth/login", json={"username": "root", "password": PASSWORD})
        response = admin.post(
            "/admin/v1/clients",
            json={
                "display_name": "Acme Corp",
                "login": "acme.admin",
                "temporary_password": TEMP_PASSWORD,
                "status": "inactive",
                "whatsapp_number": "+216 22 768 307",
            },
        )
        assert response.status_code == 201, response.text
        assert (tmp_path / "acme-corp" / "client.yaml").is_file()
    finally:
        application.close()
