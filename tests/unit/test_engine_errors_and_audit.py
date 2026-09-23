"""Error isolation, retries, audit trail, and construction rules."""

import pytest
from engine_harness import ScriptedBrain, build, due, prospect, send
from factories import CLIENT, make_client_context

from sales_core.approval import ApprovalMode
from sales_core.contracts import ActionStatus, ClientId
from sales_core.engine import Engine, EngineConfig
from sales_integrations.fakes import FakeClock, FakeMessagingChannel, FakeSalesRepository


def failing_for(*bad: str):
    def fn(request):
        if request.prospect.prospect_id in bad:
            raise RuntimeError(f"model refused {request.prospect.prospect_id}")
        return send()

    return ScriptedBrain(fn)


class TestFailureIsolation:
    def test_one_prospect_failing_does_not_stop_the_others(self):
        h = build(
            prospects=[prospect("p1"), prospect("p2"), prospect("p3")], brain=failing_for("p2")
        )
        report = h.engine.prepare()

        assert h.status("p1") is ActionStatus.PROPOSED
        assert h.status("p3") is ActionStatus.PROPOSED
        assert h.status("p2") is ActionStatus.PLANNED
        assert h.action("p2").attempts == 1
        assert "model refused p2" in h.action("p2").last_error
        assert not report.ok and len(report.errors) == 1
        assert report.errors[0].startswith("client-a:p2:first_contact")
        assert len(h.brain.calls) == 3  # all three were attempted

    def test_the_failure_is_recorded_in_the_audit_trail(self):
        h = build(prospects=[prospect("p1"), prospect("p2")], brain=failing_for("p2"))
        h.engine.prepare()
        (event,) = h.events("action.error")
        assert event.prospect_id == "p2"
        assert "model refused p2" in event.details["error"]
        assert event.details["attempts"] == "1"

    def test_failed_actions_are_retried_next_cycle_and_recover(self):
        bad = {"p2"}
        brain = ScriptedBrain(
            lambda r: (
                (_ for _ in ()).throw(RuntimeError("down"))
                if r.prospect.prospect_id in bad
                else send()
            )
        )
        h = build(prospects=[prospect("p1"), prospect("p2")], brain=brain)
        h.engine.prepare()
        bad.clear()
        report = h.engine.prepare()
        assert report.ok and h.status("p2") is ActionStatus.PROPOSED
        assert h.status("p1") is ActionStatus.PROPOSED
        assert len(h.actions_for("p1")) == 1  # p1 was not asked again

    def test_gives_up_after_max_attempts_and_never_replans_it_the_same_day(self):
        h = build(prospects=[prospect("p1"), prospect("p2")], brain=failing_for("p2"))
        for _ in range(3):
            h.engine.prepare()
        assert h.status("p2") is ActionStatus.FAILED and h.action("p2").attempts == 3
        calls_before = len(h.brain.calls)
        h.engine.prepare()
        assert len(h.brain.calls) == calls_before

    def test_one_dispatch_failure_does_not_stop_the_other_sends(self):
        h = build(prospects=[prospect("p1"), prospect("p2"), prospect("p3")])
        h.engine.prepare()
        h.approve("p1", "p2", "p3")
        h.repo.fail_next("get_prospect")  # hits the first action processed
        report = h.engine.dispatch()
        statuses = sorted(h.status(p).value for p in ("p1", "p2", "p3"))
        assert statuses == ["approved", "sent", "sent"]
        assert not report.ok and len(h.channel.sent) == 2
        h.engine.dispatch()  # the failed one is retried
        assert len(h.channel.sent) == 3

    def test_a_plan_that_cannot_be_reserved_is_raised_not_swallowed(self):
        h = build(prospects=[prospect("p1")])
        h.repo.fail_next("save_actions")
        with pytest.raises(RuntimeError):
            h.engine.prepare()
        assert h.brain.calls == []  # no Brain call without a reserved quota

    def test_failure_to_record_a_failure_is_reported(self):
        h = build(prospects=[prospect("p1")], brain=failing_for("p1"))
        original = h.repo.save_actions
        calls = {"n": 0}

        def flaky_save(actions):
            calls["n"] += 1
            if calls["n"] > 1:  # let the plan through, fail the failure record
                raise ConnectionError("sheet unavailable")
            original(actions)

        h.repo.save_actions = flaky_save  # type: ignore[method-assign]
        report = h.engine.prepare()
        assert any("could not record the failure" in e for e in report.errors)
        assert any("model refused p1" in e for e in report.errors)

    def test_cannot_read_the_repository_means_nothing_is_done(self):
        h = build(prospects=[prospect("p1")])
        h.repo.fail_next("list_prospects")
        with pytest.raises(RuntimeError):
            h.engine.prepare()
        assert h.brain.calls == [] and h.repo.list_actions() == []


class TestAuditTrail:
    def test_full_flow_is_audited_in_order(self):
        h = build(prospects=[due("p1")])
        h.engine.prepare()
        h.approve("p1")
        h.engine.dispatch()
        assert h.event_types() == [
            "action.planned",
            "brain.decision",
            "action.proposed",
            "action.sent",
        ]

    def test_events_identify_client_prospect_action_and_actor(self):
        h = build(prospects=[prospect("p1")])
        h.engine.prepare()
        events = {e.event_type: e for e in h.repo.audit_events}
        assert events["brain.decision"].actor == "brain"
        assert events["action.planned"].actor == "core"
        for event in events.values():
            assert event.client_id == CLIENT
            assert event.prospect_id == "p1"
            assert event.action_id == h.action("p1").action_id
        assert events["brain.decision"].details["decision"] == "send_message"
        assert events["brain.decision"].details["rationale"] == "test"

    def test_autonomous_approval_is_attributed_to_the_core(self):
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        h.engine.prepare()
        (event,) = h.events("action.approved")
        assert event.actor == "core:autonomous"

    def test_audit_write_failure_is_reported_and_events_are_retried(self):
        h = build(prospects=[prospect("p1")])
        h.repo.fail_next("append_audit_events")
        report = h.engine.prepare()
        assert any("audit write failed" in e for e in report.errors)
        # nothing was lost: the retained events were written by a later flush
        assert "action.planned" in h.event_types()
        assert "action.proposed" in h.event_types()

    def test_blocked_and_cancelled_actions_are_audited_with_reasons(self):
        from dataclasses import replace

        p = prospect("p1")
        h = build(prospects=[replace(p, contact=replace(p.contact, phone=None))])
        h.engine.prepare()
        assert h.events("safety.blocked")[0].details["reason"] == "no_contact"
        assert h.events("action.cancelled")[0].details["reason"] == "safety: no_contact"
        assert "prospect.paused" in h.event_types()


class TestConstruction:
    def test_engine_refuses_a_repository_bound_to_another_client(self):
        with pytest.raises(ValueError, match="different client"):
            Engine(
                client=make_client_context(),
                repository=FakeSalesRepository(ClientId("client-b")),
                brain=ScriptedBrain(),
                channel=FakeMessagingChannel(),
                clock=FakeClock(),
                config=EngineConfig(mode=ApprovalMode.APPROVAL),
            )

    def test_the_engine_touches_only_ports(self):
        # A brain, channel and repository that are plain fakes are enough to run a full cycle.
        h = build(prospects=[prospect("p1")], mode=ApprovalMode.AUTONOMOUS)
        assert h.engine.run_cycle().ok
        assert len(h.channel.sent) == 1
