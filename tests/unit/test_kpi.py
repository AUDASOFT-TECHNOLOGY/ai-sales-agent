from factories import make_action

from sales_core.contracts import ActionStatus
from sales_core.kpi import prospects_contacted


def test_counts_each_prospect_once_and_only_when_a_message_was_sent():
    actions = [
        make_action(
            action_id="a1", prospect_id="p1", idempotency_key="k1", status=ActionStatus.SENT
        ),
        make_action(
            action_id="a2", prospect_id="p1", idempotency_key="k2", status=ActionStatus.SENT
        ),
        make_action(
            action_id="a3", prospect_id="p2", idempotency_key="k3", status=ActionStatus.SENT
        ),
        make_action(
            action_id="a4", prospect_id="p3", idempotency_key="k4", status=ActionStatus.PROPOSED
        ),
        make_action(
            action_id="a5", prospect_id="p4", idempotency_key="k5", status=ActionStatus.COMPLETED
        ),
    ]
    assert prospects_contacted(actions) == 2


def test_no_actions_means_zero():
    assert prospects_contacted([]) == 0
