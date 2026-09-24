"""Follow-up cadence belongs to the Brain and comes from configuration: the model names the
situation, the settings give the delay, and the Core stores and executes it."""

from datetime import timedelta

import pytest
from brain_support import (
    CALLBACK,
    FOLLOW_UP,
    NOW,
    REPLY,
    answer,
    context_for,
    decide,
    make_brain,
    payload_of,
    request,
    set_at,
    settings_doc,
    turns,
)

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError
from sales_brain_kit.consultative.follow_up import FollowUpPolicy
from sales_core.contracts import DecisionType, SalesActionType

FIRST_CONTACT_SENT = turns(("out", "Hello!"))  # 1 unanswered message
TWO_UNANSWERED = turns(("out", "Hello!"), ("out", "Just checking in"))
THREE_UNANSWERED = turns(("out", "Hello!"), ("out", "Checking in"), ("out", "Last try"))


def refused(*answers, **options) -> str:
    with pytest.raises(BrainOutputError) as error:
        decide(*answers, *([answers[-1]] if len(answers) == 1 else []), **options)
    return str(error.value)


def with_then(then: str):
    return set_at(settings_doc(), "follow_up.situations.no_response.then", then)


def schedule(**follow_up):
    return answer(decision="schedule_follow_up", message=None, language=None, follow_up=follow_up)


class TestTheBrainProposesTheNextFollowUp:
    def test_a_first_contact_comes_with_the_first_step_of_the_cadence(self):
        decision = decide(answer())
        assert decision.next_follow_up_at == NOW + timedelta(days=2)
        assert decision.next_action_type is SalesActionType.FOLLOW_UP

    def test_each_follow_up_moves_to_the_next_step(self):
        history = FIRST_CONTACT_SENT
        decision = decide(answer(), action=FOLLOW_UP, messages=history)
        assert decision.next_follow_up_at == NOW + timedelta(days=5)

    def test_once_the_steps_are_used_up_nothing_more_is_scheduled_when_parking(self):
        decision = decide(answer(), action=FOLLOW_UP, messages=TWO_UNANSWERED)
        assert decision.next_follow_up_at is None and decision.next_action_type is None

    def test_a_reply_resets_the_cadence_because_the_conversation_is_alive(self):
        history = turns(("out", "Hello"), ("out", "Checking in"), ("in", "Sorry, busy week"))
        decision = decide(answer(), action=REPLY, messages=history)
        assert decision.next_follow_up_at == NOW + timedelta(days=2)  # back to the first step

    def test_the_situation_chooses_the_cadence(self):
        decision = decide(answer(follow_up={"situation": "after_cta"}))
        assert decision.next_follow_up_at == NOW + timedelta(days=1)
        assert decision.metadata["follow_up_situation"] == "after_cta"

    def test_the_model_can_decide_to_wait_without_writing(self):
        decision = decide(
            schedule(situation="after_cta"),
            action=REPLY,
            messages=turns(("out", "Hi"), ("in", "Call me later")),
        )
        assert decision.decision_type is DecisionType.SCHEDULE_FOLLOW_UP
        assert decision.message is None
        assert decision.next_follow_up_at == NOW + timedelta(days=1)

    def test_a_product_can_have_its_own_cadence(self):
        doc = set_at(
            settings_doc(),
            "catalog.products.0.follow_up",
            {"situations": {"no_response": {"steps": [{"after": "P7D"}], "then": "park"}}},
        )
        from brain_support import CONFIRMED, fact

        facts = [fact("interest", "alpha", CONFIRMED)]
        chosen = decide(answer(selected_product="alpha"), doc=doc, facts=facts)
        assert chosen.next_follow_up_at == NOW + timedelta(days=7)
        generic = decide(answer(), doc=doc)  # no product in focus: the client's cadence
        assert generic.next_follow_up_at == NOW + timedelta(days=2)

    def test_the_client_can_have_no_default_follow_up_at_all(self):
        doc = set_at(
            settings_doc(), "follow_up.situations", {"after_cta": {"steps": [{"after": "P1D"}]}}
        )
        decision = decide(answer(), doc=doc)
        assert decision.next_follow_up_at is None

    def test_the_step_a_follow_up_is_meant_to_serve_reaches_the_model(self):
        doc = set_at(settings_doc(), "follow_up.situations.no_response.steps.0.intent", "add_value")
        brain, llm = make_brain(doc, answer())
        brain.decide(request(action=FOLLOW_UP, messages=FIRST_CONTACT_SENT))
        assert payload_of(llm)["situation"]["follow_up_purpose"] == "add_value"

    def test_the_cadence_options_and_their_dates_are_given_to_the_model_for_information(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        options = {o["situation"]: o for o in payload_of(llm)["follow_up"]["options"]}
        assert options["no_response"]["next_touch_at"] == (NOW + timedelta(days=2)).isoformat()
        assert options["callback_promised"]["needs_requested_at"] is True


class TestTheModelNeverChoosesTheDelay:
    def test_a_requested_time_is_refused_where_the_cadence_decides(self):
        soon = (NOW + timedelta(hours=3)).isoformat()
        bad = answer(follow_up={"situation": "no_response", "requested_at": soon})
        assert "takes its time from configuration" in refused(bad)

    def test_a_situation_the_client_never_configured_is_refused(self):
        assert "is not configured" in refused(answer(follow_up={"situation": "whenever"}))

    def test_scheduling_must_name_the_situation(self):
        assert "must name the situation" in refused(
            schedule(), action=REPLY, messages=turns(("out", "Hi"), ("in", "Later"))
        )

    def test_the_gap_between_touches_can_never_be_shorter_than_the_configured_minimum(self):
        doc = set_at(settings_doc(), "follow_up.situations.no_response.steps.0.after", "PT10M")
        from sales_brain_kit import SettingsInvalidError

        with pytest.raises(SettingsInvalidError):
            parse_settings(doc)  # the schema refuses a cadence tighter than an hour


class TestPromisedCallbacks:
    def callback(self, when):
        return answer(follow_up={"situation": "callback_promised", "requested_at": when})

    def test_the_time_the_prospect_asked_for_becomes_a_callback(self):
        when = NOW + timedelta(days=1, hours=3)
        decision = decide(self.callback(when.isoformat()))
        assert decision.next_follow_up_at == when
        assert decision.next_action_type is SalesActionType.CALLBACK

    def test_the_callback_needs_a_time(self):
        assert "needs follow_up.requested_at" in refused(self.callback(None))

    @pytest.mark.parametrize(
        ("when", "problem"),
        [
            ((NOW - timedelta(hours=1)).isoformat(), "must be in the future"),
            ("2026-02-01T10:00:00", "must include a UTC offset"),  # naive
            ("next tuesday", "not an ISO 8601"),
            ((NOW + timedelta(days=800)).isoformat(), "too far ahead"),
        ],
    )
    def test_an_invalid_time_is_refused(self, when, problem):
        assert problem in refused(self.callback(when))

    def test_a_product_that_needs_notice_refuses_a_callback_that_is_too_soon(self):
        doc = set_at(
            settings_doc(),
            "catalog.products.0.appointment",
            {"supported": True, "kind": "call", "min_notice_hours": 24},
        )
        from brain_support import CONFIRMED, fact

        facts = [fact("interest", "alpha", CONFIRMED)]
        soon = (NOW + timedelta(hours=2)).isoformat()
        assert "needs 24h notice" in refused(
            answer(
                selected_product="alpha",
                follow_up={"situation": "callback_promised", "requested_at": soon},
            ),
            doc=doc,
            facts=facts,
        )
        later = NOW + timedelta(days=2)
        decision = decide(
            answer(
                selected_product="alpha",
                follow_up={"situation": "callback_promised", "requested_at": later.isoformat()},
            ),
            doc=doc,
            facts=facts,
        )
        assert decision.next_follow_up_at == later

    def test_a_reminder_is_scheduled_before_the_appointment(self):
        appointment = NOW + timedelta(days=3)
        decision = decide(
            answer(
                follow_up={
                    "situation": "appointment_reminder",
                    "requested_at": appointment.isoformat(),
                }
            )
        )
        assert decision.next_follow_up_at == appointment - timedelta(days=1)
        assert decision.next_action_type is SalesActionType.REMINDER

    def test_only_the_reminders_still_ahead_are_scheduled(self):
        appointment = NOW + timedelta(hours=20)  # the day-before reminder has passed
        decision = decide(
            answer(
                follow_up={
                    "situation": "appointment_reminder",
                    "requested_at": appointment.isoformat(),
                }
            )
        )
        assert decision.next_follow_up_at == appointment - timedelta(hours=2)
        imminent = NOW + timedelta(hours=1)
        none_left = decide(
            answer(
                follow_up={
                    "situation": "appointment_reminder",
                    "requested_at": imminent.isoformat(),
                }
            )
        )
        assert none_left.next_follow_up_at is None

    def test_a_reminder_needs_the_appointment_time(self):
        assert "needs follow_up.requested_at" in refused(
            answer(follow_up={"situation": "appointment_reminder"})
        )


class TestWhenTheCadenceIsUsedUp:
    def test_a_terminal_park_is_no_action_without_asking_the_model(self):
        brain, llm = make_brain(None)
        decision = brain.decide(request(action=FOLLOW_UP, messages=THREE_UNANSWERED))
        assert decision.decision_type is DecisionType.NO_ACTION
        assert len(llm.calls) == 0  # the configuration decided; no model call was spent

    def test_closing_after_the_last_step_schedules_one_final_check_first(self):
        doc = with_then("close")
        after_last = decide(answer(), doc=doc, action=FOLLOW_UP, messages=TWO_UNANSWERED)
        assert after_last.next_follow_up_at == NOW + timedelta(days=5)  # the final check
        brain, llm = make_brain(doc)
        final = brain.decide(request(action=FOLLOW_UP, messages=THREE_UNANSWERED))
        assert final.decision_type is DecisionType.CLOSE and len(llm.calls) == 0
        assert "follow-up steps are used up" in final.rationale
        assert final.metadata["close_reason"] and final.metadata["terminal"] == "true"

    def test_handing_over_after_the_last_step(self):
        doc = with_then("handoff")
        brain, llm = make_brain(doc)
        final = brain.decide(request(action=FOLLOW_UP, messages=THREE_UNANSWERED))
        assert final.decision_type is DecisionType.HANDOFF_TO_HUMAN and len(llm.calls) == 0
        assert final.metadata["handoff_briefing"].startswith("No reply after 3 message(s)")
        assert final.metadata["handoff_message"] == "A colleague will contact you."

    def test_a_reply_before_the_end_keeps_the_conversation_going(self):
        history = turns(("out", "a"), ("out", "b"), ("out", "c"), ("in", "Hello again"))
        decision = decide(answer(), doc=with_then("close"), action=REPLY, messages=history)
        assert decision.decision_type is DecisionType.SEND_MESSAGE

    def test_a_terminal_decision_passes_the_cores_own_check(self):
        from sales_core.engine.applier import validate_decision

        doc = with_then("close")
        brain, _ = make_brain(doc, answer())
        after_last = brain.decide(request(action=FOLLOW_UP, messages=TWO_UNANSWERED))
        validate_decision(after_last, NOW)

    def test_scheduling_is_refused_when_no_step_is_left(self):
        assert "nothing more can be scheduled" in refused(
            schedule(situation="no_response"), action=FOLLOW_UP, messages=TWO_UNANSWERED
        )


class TestThePolicyItself:
    def policy(self, doc=None):
        return FollowUpPolicy(parse_settings(doc or settings_doc()).follow_up)

    def test_the_action_type_follows_the_situation(self):
        assert FollowUpPolicy.action_type("no_response") is SalesActionType.FOLLOW_UP
        assert FollowUpPolicy.action_type("callback_promised") is SalesActionType.CALLBACK
        assert FollowUpPolicy.action_type("appointment_reminder") is SalesActionType.REMINDER

    def test_the_steps_are_counted_from_the_unanswered_messages(self):
        policy = self.policy()
        due = [
            policy.next_touch("no_response", product=None, unanswered=n, now=NOW).due_at
            for n in (0, 1, 2)
        ]
        assert due == [NOW + timedelta(days=2), NOW + timedelta(days=5), None]

    def test_it_lists_the_situations_available_for_a_product(self):
        assert "no_response" in self.policy().situations()
        assert self.policy().default_situation() == "no_response"

    def test_what_is_due_now_is_never_a_step_in_the_past(self):
        assert self.policy().exhausted_then(1) is None
        assert self.policy().exhausted_then(3) == "park"


class TestPlannerAndCoreStayResponsible:
    def test_the_brain_only_proposes_the_core_owns_execution(self):
        decision = decide(answer())
        assert isinstance(decision.next_follow_up_at, type(NOW))
        assert decision.next_action_type in {
            SalesActionType.FOLLOW_UP,
            SalesActionType.REMINDER,
            SalesActionType.CALLBACK,
        }  # only what the Core lets a Brain schedule
        assert decision.next_action_type is not SalesActionType.REPLY

    def test_a_callback_action_asks_the_brain_to_carry_it_out_not_to_reschedule_it(self):
        ctx = context_for(action=CALLBACK, messages=turns(("out", "We will call"), ("in", "ok")))
        assert ctx.plan.objective.value == "hand_over"
