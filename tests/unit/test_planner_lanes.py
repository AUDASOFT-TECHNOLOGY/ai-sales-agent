"""The Planner with several channels: each prospect is judged on the channel of its own lane."""

from factories import NOW, make_client_context, make_prospect

from sales_core.contracts import Channel, ContactInfo, LaneRoute, ProspectId
from sales_core.planner import plan_daily_actions

WHATSAPP = LaneRoute(lane_id="wa", channel=Channel.WHATSAPP, product_ids=("a",))
TELEGRAM = LaneRoute(lane_id="tg", channel=Channel.TELEGRAM, product_ids=("b",))


def person(pid, **contact):
    return make_prospect(prospect_id=ProspectId(pid), contact=ContactInfo(**contact))


def plan(prospects, route_of, **kw):
    return plan_daily_actions(
        client=make_client_context(), prospects=prospects, existing_actions=[], now=NOW,
        route_of=route_of, **kw,
    )  # fmt: skip


def test_each_action_carries_the_lane_and_channel_of_its_prospect():
    routes = {"a1": WHATSAPP, "b1": TELEGRAM}
    prospects = [person("a1", phone="+10000000001"), person("b1", telegram_chat_id="42")]
    actions = {a.prospect_id: a for a in plan(prospects, lambda p: routes[p.prospect_id]).actions}
    assert (actions["a1"].lane_id, actions["a1"].channel) == ("wa", Channel.WHATSAPP)
    assert (actions["b1"].lane_id, actions["b1"].channel) == ("tg", Channel.TELEGRAM)


def test_a_prospect_who_must_speak_first_is_skipped_only_on_the_lane_that_needs_it():
    routes = {"a1": WHATSAPP, "b1": TELEGRAM}
    prospects = [person("a1", phone="+10000000001"), person("b1", phone="+10000000002")]
    planned = plan(prospects, lambda p: routes[p.prospect_id]).actions
    assert [a.prospect_id for a in planned] == ["a1"]  # b1 has not started the Telegram chat


def test_a_prospect_in_no_lane_is_never_planned():
    prospects = [person("a1", phone="+10000000001"), person("z1", phone="+10000000003")]
    planned = plan(prospects, lambda p: WHATSAPP if p.prospect_id == "a1" else None).actions
    assert [a.prospect_id for a in planned] == ["a1"]


def test_the_daily_quota_stays_shared_by_all_lanes():
    prospects = [person(f"a{i}", phone=f"+1000000000{i}") for i in range(1, 6)]
    assert len(plan(prospects, lambda p: WHATSAPP, daily_limit=3).actions) == 3


def test_without_routes_nothing_changes_and_nothing_is_pinned():
    planned = plan_daily_actions(
        client=make_client_context(),
        prospects=[person("a1", phone="+10000000001")],
        existing_actions=[],
        now=NOW,
        channel=Channel.WHATSAPP,
    ).actions
    assert [(a.lane_id, a.channel) for a in planned] == [(None, None)]
