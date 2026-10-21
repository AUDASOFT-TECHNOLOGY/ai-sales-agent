"""The central audit log and the notifications by role (spec 12.4, 14).

Covers: redaction (no secret is ever kept), the audit written for sensitive Admin operations without
touching the routes, who may read it and for which clients, idempotent notifications (memory and
SQL), reopening after a resolution, isolation between clients and roles, and the sources wired in
(an authorisation to renew, a mailbox request, a failed write-back).
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_admin_clients_api import NOW, PASSWORD, Fixture, as_alice, as_root, editor, owner

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.admin_auth.passwords import hash_password
from sales_app.audit_log import (
    REDACTED,
    AuditEntry,
    AuditQuery,
    AuditService,
    InMemoryAuditLog,
    redact,
)
from sales_app.client_profiles import ClientProfile
from sales_app.notification_wiring import job_result_hook
from sales_app.notifications import (
    ADMIN,
    CLIENT,
    InMemoryNotificationStore,
    NotificationQuery,
    NotificationService,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.audit_rows import SqlAuditLog
from sales_app.persistence.notification_rows import SqlNotificationStore
from sales_app.plans import InMemoryPlanStore
from sales_app.scheduler import Job, JobResult

SECRET = "sk-live-0123456789abcdefghijklmnop"
CLIENT_PASSWORD = "alpha-secret-123"


# ---- redaction ---------------------------------------------------------------------------------


class TestRedaction:
    def test_secret_keys_and_secret_looking_text_are_replaced(self):
        out = redact(
            {
                "name": "Pro",
                "api_key": "x",
                "password": "hunter2",
                "nested": {"refresh_token": "abc", "note": f"uses {SECRET}"},
                "value": "anything",
                "list": [{"client_secret": "s"}],
            }
        )
        assert out["name"] == "Pro"
        assert out["api_key"] == out["password"] == out["value"] == REDACTED
        assert out["nested"]["refresh_token"] == REDACTED
        assert out["nested"]["note"] == REDACTED
        assert out["list"][0]["client_secret"] == REDACTED
        assert SECRET not in repr(out)

    def test_it_is_bounded(self):
        out = redact({"text": "a b " * 500, "deep": {"a": {"b": {"c": {"d": {"e": 1}}}}}})
        assert len(out["text"]) < 300

    def test_the_service_redacts_before_keeping_and_never_raises(self):
        log = InMemoryAuditLog()
        service = AuditService(log)
        service.record(
            actor="root",
            actor_role="owner",
            action="x",
            target=f"/t/{SECRET}",
            after={"token": SECRET, "ok": 1},
        )
        entries, _ = log.search(AuditQuery())
        assert SECRET not in repr(entries)
        assert entries[0].after == {"token": REDACTED, "ok": 1}

        class Broken:
            def append(self, entry):
                raise RuntimeError("down")

        AuditService(Broken()).record(actor="a", actor_role="owner", action="x")  # no exception


# ---- the stores: memory and SQL behave the same --------------------------------------------------


@pytest.fixture(params=["memory", "sql"])
def stores(request, tmp_path):
    if request.param == "memory":
        return InMemoryAuditLog(), InMemoryNotificationStore()
    engine = create_database_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    upgrade_database(engine)
    return SqlAuditLog(engine), SqlNotificationStore(engine)


def entry(minutes, **kw):
    base = {
        "at": NOW + timedelta(minutes=minutes),
        "actor": "root",
        "actor_role": "owner",
        "client_id": "alpha",
        "action": "client.update",
        "target": "/clients/alpha",
    }
    return AuditEntry(**{**base, **kw})


class TestAuditStore:
    def test_filters_scope_and_pagination(self, stores):
        audit, _ = stores
        audit.append(entry(1))
        audit.append(entry(2, client_id="beta", action="plan.create"))
        audit.append(entry(3, client_id=None, action="settings.channels_set"))
        audit.append(entry(4, actor="alice", outcome="denied"))

        page, total = audit.search(AuditQuery())
        assert total == 4 and [e.at for e in page] == sorted((e.at for e in page), reverse=True)
        assert audit.search(AuditQuery(client_id="beta"))[1] == 1
        assert audit.search(AuditQuery(action="plan.*"))[1] == 1
        assert audit.search(AuditQuery(action="client.update"))[1] == 2
        assert audit.search(AuditQuery(actor="alice"))[1] == 1
        assert audit.search(AuditQuery(since=NOW + timedelta(minutes=3)))[1] == 2
        assert audit.search(AuditQuery(until=NOW + timedelta(minutes=3)))[1] == 2
        # A limited reader never sees another client's entries, nor those without a client.
        scoped, scoped_total = audit.search(AuditQuery(client_ids=frozenset({"alpha"})))
        assert scoped_total == 2 and {e.client_id for e in scoped} == {"alpha"}
        assert audit.search(AuditQuery(client_ids=frozenset()))[1] == 0
        two, total = audit.search(AuditQuery(limit=2, offset=1))
        assert len(two) == 2 and total == 4

    def test_json_documents_round_trip(self, stores):
        audit, _ = stores
        audit.append(entry(1, before={"cap": 1}, after={"cap": 2}))
        (found,), _ = audit.search(AuditQuery())
        assert (found.before, found.after) == ({"cap": 1}, {"cap": 2})


class TestNotifications:
    def service(self, stores):
        return NotificationService(stores[1], now=lambda: NOW)

    def test_notify_is_idempotent_by_dedupe_key(self, stores):
        service = self.service(stores)
        first = service.notify("sync_failure", CLIENT, "alpha", dedupe_key="sync")
        again = service.notify("sync_failure", CLIENT, "alpha", dedupe_key="sync")
        assert first is not None and again is not None and first.id == again.id
        page, total = service.search(NotificationQuery(audience=CLIENT, client_id="alpha"))
        assert total == 1 and page[0].action  # a suggested action
        # The same key for another client or another audience is another notification.
        service.notify("sync_failure", CLIENT, "beta", dedupe_key="sync")
        service.notify("sync_failure", ADMIN, "alpha", dedupe_key="sync")
        assert service.search(NotificationQuery(audience=CLIENT))[1] == 2
        assert service.search(NotificationQuery(audience=ADMIN))[1] == 1

    def test_a_resolved_notification_opens_again_unread(self, stores):
        service = self.service(stores)
        made = service.notify("quota_reached", CLIENT, "alpha")
        query = NotificationQuery(audience=CLIENT, client_id="alpha")
        assert service.mark_read(query, made.id)
        assert service.unread_count(query) == 0
        assert service.resolve(CLIENT, "alpha", "quota_reached") == 1
        assert service.resolve(CLIENT, "alpha", "quota_reached") == 0
        assert service.search(NotificationQuery(audience=CLIENT, include_resolved=False))[1] == 0
        reopened = service.notify("quota_reached", CLIENT, "alpha")
        assert reopened.id == made.id and reopened.resolved_at is None and reopened.read_at is None
        assert service.unread_count(query) == 1

    def test_one_client_cannot_read_or_mark_another_clients_notification(self, stores):
        service = self.service(stores)
        mine = service.notify("quota_reached", CLIENT, "alpha")
        theirs = service.notify("quota_reached", CLIENT, "beta")
        alpha = NotificationQuery(audience=CLIENT, client_id="alpha")
        assert [n.client_id for n in service.search(alpha)[0]] == ["alpha"]
        assert service.mark_read(alpha, theirs.id) is False
        assert service.mark_all_read(alpha) == 1
        beta = NotificationQuery(audience=CLIENT, client_id="beta")
        assert service.unread_count(beta) == 1 and mine.id != theirs.id

    def test_a_client_notification_needs_a_client_and_nothing_raises(self, stores):
        service = self.service(stores)
        assert service.notify("quota_reached", CLIENT, None) is None
        assert service.notify("quota_reached", "nobody", "alpha") is None

    def test_texts_never_keep_a_secret(self, stores):
        service = self.service(stores)
        made = service.notify("config_problem", ADMIN, "alpha", message=f"key {SECRET} failed")
        assert SECRET not in made.message


# ---- the API -----------------------------------------------------------------------------------


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path, "alpha", "beta")
    fixture.users.create(editor(client_ids=frozenset({"alpha"})))
    fixture.users.create(owner())
    fixture.users.create(
        AdminUser(
            id="id-viewer",
            username="vera",
            password_hash=hash_password(PASSWORD),
            role=AdminRole.VIEWER,
            client_ids=frozenset({"alpha"}),
            created_at=NOW,
        )
    )
    fixture.users.create(
        AdminUser(
            id="id-op",
            username="olga",
            password_hash=hash_password(PASSWORD),
            role=AdminRole.OPERATOR,
            client_ids=frozenset({"alpha"}),
            created_at=NOW,
        )
    )
    fixture.application.plans = InMemoryPlanStore()
    for cid in ("alpha", "beta"):
        fixture.application.client_profiles.create(
            ClientProfile(
                client_id=cid,
                login=f"{cid}.login",
                password_hash=hash_password(CLIENT_PASSWORD),
                whatsapp_number="+216 20 000 001" if cid == "alpha" else "+216 20 000 002",
                created_at=NOW,
            )
        )
    return fixture


def client_session(fx, client_id) -> TestClient:
    session = TestClient(fx.client.app, base_url="https://testserver")
    response = session.post(
        "/client/v1/auth/login", json={"login": f"{client_id}.login", "password": CLIENT_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return session


def admin_session(fx, username) -> TestClient:
    session = TestClient(fx.client.app, base_url="https://testserver")
    response = session.post(
        "/admin/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    return session


PLAN = {
    "name": "Pro",
    "monthly_price": "49.90",
    "currency": "usd",
    "included_credits": 1000,
    "daily_quota": 200,
    "active": True,
}


class TestAuditApi:
    def test_it_requires_authentication_and_a_role(self, fx):
        assert fx.client.get("/admin/v1/audit").status_code == 401
        assert admin_session(fx, "vera").get("/admin/v1/audit").status_code == 403
        assert admin_session(fx, "alice").get("/admin/v1/audit").status_code == 403
        assert fx.client.get("/admin/v1/audit/secrets").status_code == 401

    def test_sensitive_operations_are_recorded_without_touching_the_routes(self, fx):
        root = as_root(fx)
        assert root.post("/admin/v1/plans", json=PLAN).status_code == 201
        assert root.put("/admin/v1/plans/ghost", json=PLAN).status_code == 404
        body = root.get("/admin/v1/audit").json()
        by_action = {i["action"]: i for i in body["items"]}
        created = by_action["plan.create"]
        assert (created["actor"], created["actor_role"], created["outcome"]) == (
            "root",
            "owner",
            "success",
        )
        assert created["after"]["name"] == "Pro" and created["target"] == "/plans"
        assert by_action["plan.update"]["outcome"] == "rejected"
        # Reading, and the login, are not operations.
        assert not any(i["action"].startswith("get:") for i in body["items"])
        assert not any("auth" in i["target"] for i in body["items"])

    def test_a_denied_attempt_is_recorded_as_denied(self, fx):
        as_alice(fx).post("/admin/v1/plans", json=PLAN)  # an editor may not
        items = as_root(fx).get("/admin/v1/audit?action=plan.*").json()["items"]
        assert [(i["actor"], i["outcome"]) for i in items] == [("alice", "denied")]

    def test_a_secret_sent_to_an_integration_is_never_kept(self, fx):
        root = as_root(fx)
        root.put("/admin/v1/settings/integrations/openai.api_key", json={"value": SECRET})
        root.put(
            "/admin/v1/clients/alpha/integrations/hubspot.refresh_token", json={"value": SECRET}
        )
        response = root.get("/admin/v1/audit?limit=200")
        assert SECRET not in response.text
        actions = {i["action"] for i in response.json()["items"]}
        assert {"integration.platform_set", "integration.client_set"} <= actions
        assert all(i["after"] is None for i in response.json()["items"] if "integration" in i["action"])

    def test_a_limited_operator_sees_only_its_clients(self, fx):
        root = as_root(fx)
        audit = fx.application.audit
        for client_id in ("alpha", "beta", None):
            audit.record(
                actor="root", actor_role="owner", action="client.update", client_id=client_id
            )
        olga = admin_session(fx, "olga")
        body = olga.get("/admin/v1/audit").json()
        assert {i["client_id"] for i in body["items"]} == {"alpha"}
        assert olga.get("/admin/v1/audit?client_id=beta").status_code == 404
        assert {i["client_id"] for i in root.get("/admin/v1/audit").json()["items"]} >= {
            "alpha",
            "beta",
            None,
        }

    def test_filters_and_pagination(self, fx):
        root = as_root(fx)
        for _ in range(3):
            fx.application.audit.record(
                actor="root", actor_role="owner", action="client.update", client_id="beta"
            )
        page = root.get("/admin/v1/audit?client_id=beta&action=client.update&limit=2").json()
        assert (len(page["items"]), page["total"], page["limit"]) == (2, 3, 2)
        later = datetime(2100, 1, 1, tzinfo=UTC).isoformat().replace("+", "%2B")
        assert root.get(f"/admin/v1/audit?since={later}").json()["total"] == 0
        assert root.get("/admin/v1/audit?limit=0").status_code == 422

    def test_the_credential_register_is_the_owners_and_holds_no_value(self, fx):
        root = as_root(fx)
        root.put("/admin/v1/settings/integrations/openai.api_key", json={"value": SECRET})
        response = root.get("/admin/v1/audit/secrets")
        assert response.status_code == 200 and SECRET not in response.text
        assert any(i["key"] == "openai.api_key" for i in response.json())
        assert admin_session(fx, "olga").get("/admin/v1/audit/secrets").status_code == 403


class TestNotificationsApi:
    def test_admin_scope_and_read_state(self, fx):
        notifications = fx.application.notifications
        notifications.notify("config_problem", ADMIN, "alpha")
        notifications.notify("config_problem", ADMIN, "beta")
        notifications.notify("config_problem", CLIENT, "alpha")  # not an Admin's
        alice = admin_session(fx, "alice")
        root = admin_session(fx, "root")
        body = alice.get("/admin/v1/notifications").json()
        assert [n["client_id"] for n in body["items"]] == ["alpha"]
        assert body["unread_count"] == 1
        assert alice.get("/admin/v1/notifications?client_id=beta").status_code == 404
        beta_id = next(
            n["id"] for n in root.get("/admin/v1/notifications").json()["items"]
            if n["client_id"] == "beta"
        )
        assert alice.post(f"/admin/v1/notifications/{beta_id}/read").status_code == 404
        alpha_id = body["items"][0]["id"]
        assert alice.post(f"/admin/v1/notifications/{alpha_id}/read").status_code == 200
        assert alice.get("/admin/v1/notifications").json()["unread_count"] == 0
        assert alice.post("/admin/v1/notifications/read-all").json() == {"marked": 0}
        assert root.get("/admin/v1/notifications").json()["unread_count"] == 1

    def test_notifications_need_a_session(self, fx):
        assert fx.client.get("/admin/v1/notifications").status_code == 401
        assert TestClient(fx.client.app).get("/client/v1/notifications").status_code == 401

    def test_a_client_sees_only_its_own_and_cannot_mark_anothers(self, fx):
        notifications = fx.application.notifications
        notifications.notify("quota_reached", CLIENT, "alpha")
        beta = notifications.notify("quota_reached", CLIENT, "beta")
        notifications.notify("config_problem", ADMIN, "alpha")
        alpha_session = client_session(fx, "alpha")
        body = alpha_session.get("/client/v1/notifications").json()
        assert [n["kind"] for n in body["items"]] == ["quota_reached"]
        assert body["unread_count"] == 1
        assert alpha_session.post(f"/client/v1/notifications/{beta.id}/read").status_code == 404
        assert "client_id" not in body["items"][0]
        assert alpha_session.post("/client/v1/notifications/read-all").json() == {"marked": 1}
        assert alpha_session.get("/client/v1/notifications").json()["unread_count"] == 0
        beta_body = client_session(fx, "beta").get("/client/v1/notifications").json()
        assert beta_body["unread_count"] == 1

    def test_an_admin_session_does_not_open_the_client_route(self, fx):
        admin = admin_session(fx, "root")
        assert admin.get("/client/v1/notifications").status_code == 401


class TestWiredSources:
    def test_a_mailbox_request_notifies_the_admin_until_it_is_started(self, fx):
        notifications = fx.application.notifications
        notifications.notify("email_mailbox_request", ADMIN, "alpha", dedupe_key="email_activation")
        assert as_root(fx).get("/admin/v1/notifications").json()["unread_count"] == 1
        notifications.resolve(ADMIN, "alpha", "email_activation")
        assert as_root(fx).get("/admin/v1/notifications").json()["unread_count"] == 0

    def test_an_authorisation_to_renew_notifies_the_client_and_the_admin_once(self, fx):
        grants = fx.application.gmail.grants
        grants.save("alpha", "refresh-token-value", "owner@example.com")
        grants.mark_reauth_required("alpha")
        grants.mark_reauth_required("alpha")  # again: still one each
        client_body = client_session(fx, "alpha").get("/client/v1/notifications").json()
        assert [n["kind"] for n in client_body["items"]] == ["authorization_revoked"]
        assert "refresh-token-value" not in str(client_body)
        admin_body = as_root(fx).get("/admin/v1/notifications").json()
        assert [n["client_id"] for n in admin_body["items"]] == ["alpha"]
        assert client_session(fx, "beta").get("/client/v1/notifications").json()["items"] == []
        grants.save("alpha", "refresh-token-value", "owner@example.com")  # reconnected
        assert client_session(fx, "alpha").get("/client/v1/notifications").json()["items"] == []

    def test_a_whatsapp_authorisation_to_renew_notifies(self, fx):
        fx.application.whatsapp.mark_reauth_required("beta")
        body = client_session(fx, "beta").get("/client/v1/notifications").json()
        assert body["items"][0]["title"].startswith("WhatsApp")

    def test_a_failed_write_back_and_its_recovery(self, fx):
        hook = job_result_hook(fx.application.notifications)
        hook(JobResult("alpha", Job.SYNC, ok=False, errors=("write-back failed",)))
        hook(JobResult("alpha", Job.SYNC, ok=False, errors=("write-back failed",)))
        mine = client_session(fx, "alpha").get("/client/v1/notifications").json()
        assert [n["kind"] for n in mine["items"]] == ["sync_failure"]
        hook(JobResult("alpha", Job.SYNC, ok=True))
        assert client_session(fx, "alpha").get("/client/v1/notifications").json()["items"] == []
        hook(JobResult("alpha", Job.REPORT, ok=False, errors=("report failed",)))
        kinds = [n["kind"] for n in as_root(fx).get("/admin/v1/notifications").json()["items"]]
        assert "report_failure" in kinds


class TestAuditBeforeAndLogins:
    def test_a_sensitive_change_keeps_the_state_before_it(self, fx):
        root = as_root(fx)
        created = root.post("/admin/v1/plans", json=PLAN)
        assert created.status_code == 201
        plan_id = created.json()["plan_id"]
        changed = {**PLAN, "monthly_price": "59.90"}
        assert root.put(f"/admin/v1/plans/{plan_id}", json=changed).status_code == 200
        entries = root.get("/admin/v1/audit?action=plan.update").json()["items"]
        assert len(entries) == 1
        assert entries[0]["before"]["monthly_price"] in ("49.90", 49.9)
        assert entries[0]["after"]["monthly_price"] == "59.90"
        creation = root.get("/admin/v1/audit?action=plan.create").json()["items"][0]
        assert creation["before"] is None  # a creation has no before

    def test_a_refused_admin_and_client_login_is_audited_without_the_password(self, fx):
        bad = fx.client.post("/admin/v1/auth/login", json={"username": "root", "password": "wrong-pw-1"})
        assert bad.status_code == 401
        bad = fx.client.post(
            "/client/v1/auth/login", json={"login": "alpha.login", "password": "wrong-pw-2"}
        )
        assert bad.status_code == 401
        response = as_root(fx).get("/admin/v1/audit?limit=200")
        items = {i["action"]: i for i in response.json()["items"]}
        assert items["admin.login_refused"]["actor"] == "root"
        assert items["admin.login_refused"]["outcome"] == "denied"
        assert items["client.login_refused"]["actor"] == "alpha.login"
        assert "wrong-pw" not in response.text
