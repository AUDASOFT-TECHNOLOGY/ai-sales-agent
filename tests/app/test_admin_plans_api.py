"""Admin API: plans (create, read, change, deactivate), a client's plan, and plan at creation."""

import pytest
from test_admin_clients_api import as_alice, as_root, fx, new_client  # noqa: F401 - fx is a fixture

from sales_app.plans import InMemoryPlanStore

PLANS = "/admin/v1/plans"
BODY = {
    "name": "Pro",
    "monthly_price": "49.90",
    "currency": "usd",
    "included_credits": 1000,
    "daily_quota": 200,
    "active": True,
}


@pytest.fixture(autouse=True)
def with_plans(fx):  # noqa: F811
    fx.application.plans = InMemoryPlanStore()


def create(client, **change):
    return client.post(PLANS, json={**BODY, **change})


class TestPlans:
    def test_it_requires_authentication(self, fx):  # noqa: F811
        assert fx.client.get(PLANS).status_code == 401
        assert fx.client.post(PLANS, json=BODY).status_code == 401

    def test_an_owner_creates_reads_changes_and_deactivates_a_plan(self, fx):  # noqa: F811
        client = as_root(fx)
        made = create(client)
        assert made.status_code == 201
        plan = made.json()
        assert (plan["plan_id"], plan["currency"], plan["monthly_price"]) == ("pro", "USD", "49.90")
        assert (plan["client_count"], plan["updated_by"], plan["active"]) == (0, "root", True)
        assert client.get(f"{PLANS}/pro").json()["name"] == "Pro"
        changed = client.put(
            f"{PLANS}/pro", json={**BODY, "name": "Pro+", "monthly_price": 59, "active": False}
        )
        assert changed.status_code == 200
        assert (changed.json()["name"], changed.json()["monthly_price"]) == ("Pro+", "59.00")
        assert changed.json()["active"] is False and changed.json()["plan_id"] == "pro"
        assert [p["plan_id"] for p in client.get(PLANS).json()["plans"]] == ["pro"]

    def test_invalid_terms_are_refused_and_nothing_is_kept(self, fx):  # noqa: F811
        client = as_root(fx)
        for change in (
            {"name": ""},
            {"monthly_price": "-1"},
            {"currency": "dollars"},
            {"included_credits": -5},
            {"daily_quota": 0},
        ):
            assert create(client, **change).status_code == 422
        assert client.get(PLANS).json() == {"plans": []}

    def test_a_name_already_taken_is_a_conflict(self, fx):  # noqa: F811
        client = as_root(fx)
        create(client)
        assert create(client, name="PRO").status_code == 409
        create(client, name="Max")
        assert client.put(f"{PLANS}/max", json={**BODY, "name": "pro"}).status_code == 409

    def test_two_names_with_one_slug_get_different_ids(self, fx):  # noqa: F811
        client = as_root(fx)
        assert create(client, name="A B").json()["plan_id"] == "a-b"
        assert create(client, name="a-b").json()["plan_id"] == "a-b-2"

    def test_an_unknown_plan_is_a_404(self, fx):  # noqa: F811
        client = as_root(fx)
        assert client.get(f"{PLANS}/ghost").status_code == 404
        assert client.put(f"{PLANS}/ghost", json=BODY).status_code == 404

    def test_a_scoped_editor_reads_but_cannot_change_plans(self, fx):  # noqa: F811
        create(as_root(fx))
        client = as_alice(fx)
        assert len(client.get(PLANS).json()["plans"]) == 1
        assert client.get(f"{PLANS}/pro").status_code == 200
        assert create(client, name="Other").status_code == 403
        assert client.put(f"{PLANS}/pro", json=BODY).status_code == 403

    def test_without_a_database_it_says_so(self, fx):  # noqa: F811
        fx.application.plans = None
        client = as_root(fx)
        assert client.get(PLANS).status_code == 503
        assert create(client).status_code == 503


class TestClientPlan:
    def test_an_owner_puts_a_client_on_a_plan_and_moves_it(self, fx):  # noqa: F811
        client = as_root(fx)
        create(client)
        create(client, name="Max", monthly_price="99")
        assert client.get("/admin/v1/clients/alpha/plan").json()["current"] is None
        first = client.put("/admin/v1/clients/alpha/plan", json={"plan_id": "pro"})
        assert first.status_code == 200
        assert first.json()["current"]["plan_name"] == "Pro"
        assert first.json()["current"]["assigned_by"] == "root"
        moved = client.put("/admin/v1/clients/alpha/plan", json={"plan_id": "max"}).json()
        assert moved["current"]["plan_id"] == "max"
        assert [h["plan_id"] for h in moved["history"]] == ["max", "pro"]
        assert moved["history"][1]["ended_at"] is not None
        assert client.get(f"{PLANS}/max").json()["client_count"] == 1
        assert client.get(f"{PLANS}/pro").json()["client_count"] == 0

    def test_the_assignment_keeps_the_terms_of_the_day(self, fx):  # noqa: F811
        client = as_root(fx)
        create(client)
        client.put("/admin/v1/clients/alpha/plan", json={"plan_id": "pro"})
        client.put(f"{PLANS}/pro", json={**BODY, "monthly_price": "999"})
        current = client.get("/admin/v1/clients/alpha/plan").json()["current"]
        assert current["monthly_price"] == "49.90"

    def test_a_client_can_be_taken_off_its_plan(self, fx):  # noqa: F811
        client = as_root(fx)
        create(client)
        client.put("/admin/v1/clients/alpha/plan", json={"plan_id": "pro"})
        body = client.put("/admin/v1/clients/alpha/plan", json={"plan_id": None}).json()
        assert body["current"] is None and len(body["history"]) == 1

    def test_an_inactive_or_unknown_plan_cannot_be_assigned(self, fx):  # noqa: F811
        client = as_root(fx)
        create(client, active=False)
        assert (
            client.put("/admin/v1/clients/alpha/plan", json={"plan_id": "pro"}).status_code == 409
        )
        assert client.put("/admin/v1/clients/alpha/plan", json={"plan_id": "x"}).status_code == 404
        assert client.get("/admin/v1/clients/alpha/plan").json()["current"] is None

    def test_only_an_owner_assigns_and_only_within_reach(self, fx):  # noqa: F811
        create(as_root(fx))
        as_root(fx).put("/admin/v1/clients/beta/plan", json={"plan_id": "pro"})
        client = as_alice(fx)  # reaches alpha (and naga), not beta
        assert (
            client.put("/admin/v1/clients/alpha/plan", json={"plan_id": "pro"}).status_code == 403
        )
        assert client.get("/admin/v1/clients/alpha/plan").status_code == 200
        assert client.get("/admin/v1/clients/beta/plan").status_code == 404

    def test_a_scoped_admin_counts_only_the_clients_it_reaches(self, fx):  # noqa: F811
        root = as_root(fx)
        create(root)
        root.put("/admin/v1/clients/alpha/plan", json={"plan_id": "pro"})
        root.put("/admin/v1/clients/beta/plan", json={"plan_id": "pro"})
        assert root.get(f"{PLANS}/pro").json()["client_count"] == 2
        assert as_alice(fx).get(f"{PLANS}/pro").json()["client_count"] == 1

    def test_an_unknown_client_is_a_404(self, fx):  # noqa: F811
        client = as_root(fx)
        create(client)
        assert client.get("/admin/v1/clients/ghost/plan").status_code == 404
        assert (
            client.put("/admin/v1/clients/ghost/plan", json={"plan_id": "pro"}).status_code == 404
        )


class TestPlanAtCreation:
    def test_a_new_client_starts_on_the_chosen_plan(self, fx):  # noqa: F811
        client = as_root(fx)
        create(client)
        response = client.post("/admin/v1/clients", json=new_client(plan_id="pro"))
        assert response.status_code == 201
        client_id = response.json()["client_id"]
        current = client.get(f"/admin/v1/clients/{client_id}/plan").json()["current"]
        assert (current["plan_id"], current["assigned_by"]) == ("pro", "root")

    def test_without_a_plan_the_client_has_none(self, fx):  # noqa: F811
        client = as_root(fx)
        client_id = client.post("/admin/v1/clients", json=new_client()).json()["client_id"]
        assert client.get(f"/admin/v1/clients/{client_id}/plan").json()["current"] is None

    @pytest.mark.parametrize("plan_id", ["ghost", "old"])
    def test_a_bad_plan_creates_nothing(self, fx, tmp_path, plan_id):  # noqa: F811
        client = as_root(fx)
        create(client, name="Old", active=False)
        response = client.post("/admin/v1/clients", json=new_client(plan_id=plan_id))
        assert response.status_code == 400
        assert not (tmp_path / "acme-corp").exists()
        assert all(c["display_name"] != "Acme Corp" for c in client.get("/admin/v1/clients").json())

    def test_a_plan_without_a_database_creates_nothing(self, fx, tmp_path):  # noqa: F811
        fx.application.plans = None
        response = as_root(fx).post("/admin/v1/clients", json=new_client(plan_id="pro"))
        assert response.status_code == 503
        assert not (tmp_path / "acme-corp").exists()
