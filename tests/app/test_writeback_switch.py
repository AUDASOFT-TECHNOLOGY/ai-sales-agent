"""The platform's write-back switch: on by default for a deployment, the Admin's to change live.

Doubles only (in-memory credential store, no Google, no HubSpot). Proved: the default follows the
deployment; the Admin's decision overrides it and can be withdrawn; an unreadable decision never
switches writing on; the runtime asks the switch each time (no restart); the owner-only route; an
audit line; and that switching it on writes nothing for a source that has no configured block.
"""

from types import SimpleNamespace

import pytest
from test_client_agent_api import World

from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore
from sales_app.settings import Settings
from sales_app.wiring.writeback import WritebackSupport, build_client_writeback, writeback_is_on
from sales_app.writeback_switch import WritebackSwitch

URL = "/admin/v1/settings/writeback"


def test_it_follows_the_deployment_until_the_admin_decides():
    for default in (True, False):
        switch = WritebackSwitch(InMemorySecretStore(), InMemorySecretAudit(), default=default)
        assert switch.enabled() is default and switch.source() == "deployment"
        switch.set(not default, actor="admin:root")
        assert switch.enabled() is (not default) and switch.source() == "admin"
        switch.set(None, actor="admin:root")
        assert switch.enabled() is default and switch.source() == "deployment"


def test_the_deployment_setting_is_on_unless_it_says_otherwise():
    assert Settings().writeback_enabled is True
    assert Settings(writeback_enabled=False).writeback_enabled is False


def test_a_decision_that_cannot_be_read_never_switches_writing_on():
    class Broken(InMemorySecretStore):
        def get(self, scope, key):
            raise RuntimeError("unreadable")

    assert WritebackSwitch(Broken(), None, default=True).enabled() is False


def test_the_runtime_asks_the_switch_every_time():
    switch = WritebackSwitch(InMemorySecretStore(), None, default=True)
    support = WritebackSupport(enabled=switch.enabled)
    assert writeback_is_on(support) is True
    switch.set(False, actor="a")
    assert writeback_is_on(support) is False
    assert writeback_is_on(None) is False and writeback_is_on(WritebackSupport()) is False


def test_every_change_is_audited_and_reaches_the_runtime():
    audit = InMemorySecretAudit()
    seen = []
    switch = WritebackSwitch(InMemorySecretStore(), audit, default=True)
    switch.set_on_change(lambda scope, key: seen.append((scope, key)))
    switch.set(False, actor="admin:root")
    switch.set(None, actor="admin:root")
    assert seen == [("global", "writeback.enabled")] * 2
    assert len(audit._events) == 2 and all(e.actor == "admin:root" for e in audit._events)


def test_switching_on_writes_nothing_for_a_client_with_no_configured_source():
    config = SimpleNamespace(
        effective_mode=SimpleNamespace(),  # not the dry-run mode
        spec=SimpleNamespace(sources=()),
    )
    support = WritebackSupport(enabled=lambda: True)
    writeback, problems = build_client_writeback(config, {}, None, None, support, None)
    assert writeback is None and problems == []


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=InMemorySecretAudit())
    yield w
    w.application.close()


def test_the_admin_reads_and_changes_it_over_http_and_it_is_kept(world):
    admin = world.admin()
    body = admin.get(URL).json()
    assert body["enabled"] is True and body["decided_by"] == "deployment"
    assert body["deployment_default"] is True
    off = admin.put(URL, json={"enabled": False}).json()
    assert off["enabled"] is False and off["decided_by"] == "admin"
    assert world.application.writeback_switch.enabled() is False
    assert admin.get(URL).json()["enabled"] is False
    back = admin.put(URL, json={"enabled": None}).json()
    assert back["enabled"] is True and back["decided_by"] == "deployment"


@pytest.mark.parametrize("body", [{}, {"enabled": "yes"}, {"enabled": True, "x": 1}])
def test_a_malformed_decision_is_refused(world, body):
    assert world.admin().put(URL, json=body).status_code == 422


def test_it_needs_an_administrator(world):
    anonymous = world.browser("alpha")  # a client session is not an admin one
    assert anonymous.get(URL).status_code == 401
    assert anonymous.put(URL, json={"enabled": False}).status_code == 401
