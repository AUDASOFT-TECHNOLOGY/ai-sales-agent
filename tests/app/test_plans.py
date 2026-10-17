"""Commercial plans: migration 0008, validation, and the SQL and in-memory stores (one contract)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.plan_rows import SqlPlanStore
from sales_app.persistence.schema import current_revision
from sales_app.persistence.tables import metadata
from sales_app.plans import (
    InMemoryPlanStore,
    InvalidPlanError,
    Plan,
    PlanInactiveError,
    PlanNotFoundError,
    clean_terms,
    slugify,
)
from sales_app.sourcing.errors import StoreUnavailableError

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)
T2 = T0 + timedelta(days=2)


def make_plan(plan_id="pro", name="Pro", price="49.90", credits=1000, quota=200, active=True):
    return Plan(plan_id, name, Decimal(price), "USD", credits, quota, active, T0, T0, "root")


# ---- migration 0008 -----------------------------------------------------------------------------


def test_0008_adds_the_plan_tables_and_nothing_else():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0007")
    before = set(inspect(engine).get_table_names())
    upgrade_database(engine, "0008")
    assert current_revision(engine) == "0008"
    after = set(inspect(engine).get_table_names())
    assert after - before == {"plans", "client_plan_assignments"}
    assert after - {"alembic_version"} < set(metadata.tables)


# ---- validation ---------------------------------------------------------------------------------


def test_valid_terms_are_normalised():
    assert clean_terms("  Pro   Plus ", "49.9", " usd", 0, 1) == (
        "Pro Plus",
        Decimal("49.90"),
        "USD",
        0,
        1,
    )
    assert clean_terms("Free", 0, "EUR", 0, 10)[1] == 0  # a free plan is a real zero price


@pytest.mark.parametrize(
    "args",
    [
        ("", 1, "USD", 1, 1),
        ("x" * 81, 1, "USD", 1, 1),
        ("!!!", 1, "USD", 1, 1),
        ("A", -1, "USD", 1, 1),
        ("A", "abc", "USD", 1, 1),
        ("A", True, "USD", 1, 1),
        ("A", "NaN", "USD", 1, 1),
        ("A", 1, "dollars", 1, 1),
        ("A", 1, "USD", -1, 1),
        ("A", 1, "USD", 1, 0),
    ],
)
def test_invalid_terms_are_refused(args):
    with pytest.raises(InvalidPlanError):
        clean_terms(*args)


def test_a_slug_comes_from_the_name():
    assert slugify("Pro  Plus!") == "pro-plus"


# ---- the stores ---------------------------------------------------------------------------------


@pytest.fixture(params=["sql", "memory"])
def store(request):
    if request.param == "memory":
        return InMemoryPlanStore()
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return SqlPlanStore(engine)


def test_plans_are_created_listed_by_name_and_updated(store):
    store.create_plan(make_plan("pro", "Pro"))
    store.create_plan(make_plan("basic", "basic", price="9"))
    assert [p.plan_id for p in store.list_plans()] == ["basic", "pro"]
    store.update_plan(Plan("pro", "Pro 2", Decimal(59), "EUR", 5, 6, False, T2, T1, "other"))
    kept = store.get_plan("pro")
    assert (kept.name, kept.monthly_price, kept.currency, kept.active) == (
        "Pro 2",
        Decimal("59.00"),
        "EUR",
        False,
    )
    assert (kept.included_credits, kept.daily_quota, kept.updated_by) == (5, 6, "other")
    assert kept.created_at == T0  # creation is not rewritten
    assert store.get_plan("nope") is None


def test_a_name_is_unique_ignoring_case_and_an_unknown_plan_cannot_be_updated(store):
    store.create_plan(make_plan("pro", "Pro"))
    with pytest.raises(InvalidPlanError):
        store.create_plan(make_plan("pro-2", "PRO"))
    with pytest.raises(InvalidPlanError):
        store.create_plan(make_plan("pro", "Other"))
    store.create_plan(make_plan("max", "Max"))
    with pytest.raises(InvalidPlanError):
        store.update_plan(make_plan("max", "pro"))
    with pytest.raises(PlanNotFoundError):
        store.update_plan(make_plan("ghost", "Ghost"))


def test_a_client_is_put_on_a_plan_with_a_snapshot_of_its_terms(store):
    store.create_plan(make_plan())
    assert store.current_assignment("alpha") is None
    created = store.assign("alpha", "pro", at=T0, by="root")
    assert (created.plan_id, created.monthly_price, created.started_at) == (
        "pro",
        Decimal("49.90"),
        T0,
    )
    assert store.current_assignment("alpha") == created
    assert store.clients_on("pro") == ["alpha"]
    assert store.clients_on("other") == []


def test_editing_a_plan_does_not_change_what_an_assignment_recorded(store):
    store.create_plan(make_plan())
    store.assign("alpha", "pro", at=T0, by="root")
    store.update_plan(Plan("pro", "Pro", Decimal(99), "EUR", 1, 1, True, T0, T1, "root"))
    current = store.current_assignment("alpha")
    assert (current.monthly_price, current.currency, current.included_credits) == (
        Decimal("49.90"),
        "USD",
        1000,
    )


def test_changing_plan_ends_the_old_assignment_and_keeps_the_history(store):
    store.create_plan(make_plan("pro", "Pro"))
    store.create_plan(make_plan("max", "Max", price="99"))
    store.assign("alpha", "pro", at=T0, by="root")
    store.assign("alpha", "max", at=T1, by="root")
    history = store.assignment_history("alpha")
    assert [(a.plan_id, a.ended_at) for a in history] == [("max", None), ("pro", T1)]
    assert store.current_assignment("alpha").plan_id == "max"
    assert store.clients_on("pro") == [] and store.clients_on("max") == ["alpha"]


def test_assigning_the_same_plan_again_changes_nothing(store):
    store.create_plan(make_plan())
    first = store.assign("alpha", "pro", at=T0, by="root")
    again = store.assign("alpha", "pro", at=T1, by="other")
    assert again == first
    assert len(store.assignment_history("alpha")) == 1


def test_a_client_can_be_taken_off_its_plan(store):
    store.create_plan(make_plan())
    store.assign("alpha", "pro", at=T0, by="root")
    assert store.assign("alpha", None, at=T1, by="root") is None
    assert store.current_assignment("alpha") is None
    assert store.assign("alpha", None, at=T2, by="root") is None  # nothing to end
    assert [a.ended_at for a in store.assignment_history("alpha")] == [T1]


def test_an_unknown_or_inactive_plan_cannot_be_given_and_nothing_changes(store):
    store.create_plan(make_plan("pro", "Pro"))
    store.create_plan(make_plan("old", "Old", active=False))
    store.assign("alpha", "pro", at=T0, by="root")
    with pytest.raises(PlanNotFoundError):
        store.assign("alpha", "ghost", at=T1, by="root")
    with pytest.raises(PlanInactiveError):
        store.assign("alpha", "old", at=T1, by="root")
    assert store.current_assignment("alpha").plan_id == "pro"
    assert store.current_assignment("alpha").ended_at is None


def test_a_deactivated_plan_keeps_the_clients_already_on_it(store):
    store.create_plan(make_plan())
    store.assign("alpha", "pro", at=T0, by="root")
    store.update_plan(make_plan(active=False))
    assert store.clients_on("pro") == ["alpha"]
    assert store.current_assignment("alpha").plan_id == "pro"


def test_clients_never_see_each_others_assignments(store):
    store.create_plan(make_plan())
    store.assign("alpha", "pro", at=T0, by="root")
    assert store.current_assignment("beta") is None
    assert store.assignment_history("beta") == []


def test_the_database_refuses_a_second_current_assignment():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    sql = SqlPlanStore(engine)
    sql.create_plan(make_plan())
    sql.assign("alpha", "pro", at=T0, by="root")
    with pytest.raises(Exception) as caught, engine.begin() as connection:
        connection.execute(
            text(
                "insert into client_plan_assignments (client_id, plan_id, plan_name, monthly_price,"
                " currency, included_credits, daily_quota, started_at, assigned_by)"
                " values ('alpha', 'pro', 'Pro', 1, 'USD', 1, 1, '2026-01-06 00:00:00', 'x')"
            )
        )
    assert "UNIQUE" in str(caught.value).upper()


def test_an_unreachable_database_is_a_fixed_sentence():
    broken = SqlPlanStore(create_database_engine("sqlite://"))  # no migration
    for call in (
        broken.list_plans,
        lambda: broken.get_plan("pro"),
        lambda: broken.create_plan(make_plan()),
        lambda: broken.current_assignment("alpha"),
        lambda: broken.assign("alpha", "pro", at=T0, by="root"),
        lambda: broken.clients_on("pro"),
    ):
        with pytest.raises(StoreUnavailableError):
            call()
