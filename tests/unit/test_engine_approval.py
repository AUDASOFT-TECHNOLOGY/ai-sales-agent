"""A person approves or rejects a proposal through the Engine (what a client without a sheet does)."""

from dataclasses import replace

import pytest
from engine_harness import ScriptedBrain, build, prospect, send

from sales_core.contracts import ActionId, ActionStatus, ProspectStatus
from sales_core.engine import ProposalNotApprovableError, ProposalNotFoundError
from sales_core.engine import events as ev

WHO = "client:alpha"


def opt_out(h):
    h.repo.update_prospects([replace(h.prospect("p1"), status=ProspectStatus.OPTED_OUT)])


def proposed(**kw):
    h = build(
        prospects=[prospect("p1", display_name="Alex")],
        brain=ScriptedBrain(lambda r: send("Hi!")),
        **kw,
    )
    h.engine.prepare()
    assert h.status("p1") is ActionStatus.PROPOSED
    return h, h.action("p1").action_id


def test_the_waiting_proposals_are_listed_with_their_text_and_prospect():
    h, action_id = proposed()
    (waiting,) = h.engine.pending_proposals()
    assert (waiting.action_id, waiting.prospect_name, waiting.text) == (action_id, "Alex", "Hi!")


def test_approving_releases_the_message_and_the_next_dispatch_sends_it_once():
    h, action_id = proposed()
    h.engine.approve_proposal(action_id, actor=WHO)
    assert h.status("p1") is ActionStatus.APPROVED
    assert h.channel.attempts == ()  # approving sends nothing by itself
    assert h.engine.pending_proposals() == ()
    h.engine.dispatch()
    h.engine.dispatch()
    assert h.status("p1") is ActionStatus.SENT
    assert len(h.channel.attempts) == 1


def test_the_approval_is_audited_with_who_did_it():
    h, action_id = proposed()
    h.engine.approve_proposal(action_id, actor=WHO)
    (event,) = h.events(ev.ACTION_APPROVED)
    assert event.actor == WHO and event.action_id == action_id


def test_rejecting_sends_nothing_ever_and_is_audited():
    h, action_id = proposed()
    h.engine.reject_proposal(action_id, actor=WHO)
    h.engine.dispatch()
    assert h.status("p1") is ActionStatus.REJECTED
    assert h.channel.attempts == ()
    (event,) = h.events(ev.ACTION_REJECTED)
    assert event.actor == WHO


@pytest.mark.parametrize("decide", ["approve_proposal", "reject_proposal"])
def test_a_proposal_is_decided_once(decide):
    h, action_id = proposed()
    h.engine.approve_proposal(action_id, actor=WHO)
    with pytest.raises(ProposalNotFoundError):
        getattr(h.engine, decide)(action_id, actor=WHO)
    assert h.status("p1") is ActionStatus.APPROVED


def test_an_unknown_id_is_not_found():
    h, _ = proposed()
    with pytest.raises(ProposalNotFoundError):
        h.engine.approve_proposal(ActionId("nope"), actor=WHO)


def test_a_prospect_who_opted_out_meanwhile_cannot_be_approved_but_can_be_rejected():
    h, action_id = proposed()
    opt_out(h)
    with pytest.raises(ProposalNotApprovableError):
        h.engine.approve_proposal(action_id, actor=WHO)
    assert h.status("p1") is ActionStatus.PROPOSED
    h.engine.reject_proposal(action_id, actor=WHO)
    assert h.status("p1") is ActionStatus.REJECTED


def test_an_approved_message_to_a_prospect_who_opts_out_afterwards_is_still_not_sent():
    h, action_id = proposed()
    h.engine.approve_proposal(action_id, actor=WHO)
    opt_out(h)
    h.engine.dispatch()
    assert h.channel.attempts == ()
