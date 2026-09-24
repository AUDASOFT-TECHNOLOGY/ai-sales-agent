"""Slice 6b, the conversion workflow: where a prospect stands is derived from configuration and known
facts; whether registration, payment, an appointment or a handoff involves a person is the client's
choice, step by step; nothing here is specific to a client."""

from copy import deepcopy

import pytest
from brain_support import CALLBACK, DISCOVERED, NOW, closing, context_for, decide, handoff, set_at
from commerce_support import (
    ACCOUNT_NUMBER,
    CONFIRMED,
    ONGOING,
    PAY_URL,
    answer,
    at_payment,
    commerce_doc,
    country,
    interested,
    known,
    make_brain,
    payload_of,
    registered,
    request,
)
from factories import make_brain_request, make_prospect

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError, build_context
from sales_brain_kit.consultative.discovery import DiscoveryStatus
from sales_brain_kit.consultative.strategy import Objective
from sales_brain_kit.consultative.workflow import WorkflowStatus
from sales_core.contracts import DecisionType, FactSource

BOOKING_URL = "https://book.example.org/slots?team=A"


def state_of(*facts, doc=None, **options):
    return context_for(doc or commerce_doc(), facts=list(facts), **options).plan.workflow


def refused(doc, *answers, facts=(), **options) -> str:
    scripted = (*answers, *([answers[-1]] if len(answers) == 1 else []))
    brain, _ = make_brain(doc, *scripted)
    with pytest.raises(BrainOutputError) as error:
        brain.decide(request(facts=list(facts), **{**ONGOING, **options}))
    return str(error.value)


def sale(doc, *steps):
    """`doc` with its workflow's steps replaced."""
    doc = deepcopy(doc)
    doc["conversion"]["workflows"][0]["steps"] = list(steps)
    return doc


def step_ids(state):
    return [(p.step.id, p.done) for p in state.progress]


class TestWhereTheProspectStands:
    def test_without_an_interest_the_workflow_is_still_at_the_recommendation(self):
        state = state_of()
        assert state.current.id == "recommend" and not state.advancing
        assert state.status is WorkflowStatus.IN_PROGRESS

    def test_a_confirmed_interest_moves_it_to_registration(self):
        ctx = context_for(commerce_doc(), facts=[interested()])
        state = ctx.plan.workflow
        assert state.current.id == "register" and state.advancing
        assert ctx.plan.objective is Objective.ADVANCE_WORKFLOW
        assert step_ids(state)[:2] == [("recommend", True), ("register", False)]

    def test_registration_asks_only_for_what_it_collects(self):
        plan = context_for(commerce_doc(), facts=[interested()]).plan
        assert plan.workflow.ask_keys == ("full_name",)
        assert plan.discovery.status is DiscoveryStatus.ASK
        assert plan.discovery.aim == "advance" and plan.discovery.target_keys == ("full_name",)

    def test_what_is_already_known_is_not_asked_again(self):
        plan = context_for(commerce_doc(), facts=[interested(), known("full_name", "Alex")]).plan
        assert plan.workflow.ask_keys == () and plan.discovery.status is DiscoveryStatus.NOT_NEEDED

    def test_registration_done_moves_it_to_payment(self):
        state = state_of(*registered())
        assert state.current.id == "pay" and state.status is WorkflowStatus.IN_PROGRESS
        # the product's own price is in USD, so the card (USD, EUR) and the transfer apply
        assert [m.method.id for m in state.methods] == ["online-card", "bank-transfer"]

    def test_payment_received_moves_it_to_confirmation(self):
        state = state_of(*registered(), known("payment_status", "received"))
        assert state.current.id == "confirm"

    def test_a_confirmed_payment_ends_at_the_closing_step(self):
        state = state_of(*registered(), known("payment_status", "confirmed"))
        assert state.current.id == "done"
        assert state.forced == {DecisionType.CLOSE}

    def test_the_stage_of_the_prospect_can_be_the_completion(self):
        doc = sale(
            commerce_doc(),
            {"id": "recommend", "kind": "recommend"},
            {"id": "register", "kind": "register", "completes_when": {"stage": "won"}},
            {"id": "done", "kind": "close"},
        )
        doc["stages"] = [{"id": "new", "label": "New"}, {"id": "won", "label": "Won"}]
        request_ = make_brain_request(prospect=make_prospect(stage="won"), facts=(interested(),))
        ctx = build_context(request_, parse_settings(doc))
        assert ctx.plan.workflow.current.id == "done"

    def test_only_a_confirmed_fact_completes_a_step_unless_the_client_accepts_known_ones(self):
        discovered = known("registration_status", "registered", status=DISCOVERED)
        assert state_of(interested(), discovered).current.id == "register"
        doc = deepcopy(commerce_doc())
        condition = doc["conversion"]["workflows"][0]["steps"][1]["completes_when"]["facts"][0]
        condition["min_status"] = "known"
        assert state_of(interested(), discovered, doc=doc).current.id == "pay"

    def test_a_value_that_is_not_the_configured_one_does_not_complete_a_step(self):
        state = state_of(interested(), known("registration_status", "pending"))
        assert state.current.id == "register"

    def test_a_guess_never_completes_a_step(self):
        from brain_support import fact

        from sales_core.contracts import FactStatus

        guess = fact("registration_status", "registered", FactStatus.HYPOTHESIS, confidence=0.99)
        assert state_of(interested(), guess).current.id == "register"

    def test_a_workflow_that_is_complete_has_no_current_step(self):
        doc = sale(commerce_doc(), {"id": "recommend", "kind": "recommend"})
        state = state_of(interested(), doc=doc)
        assert state.status is WorkflowStatus.COMPLETED and state.current is None
        assert not state.advancing

    def test_without_any_workflow_nothing_changes(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"] = {}
        ctx = context_for(doc, facts=[interested()])
        assert ctx.plan.workflow is None and ctx.plan.objective is Objective.PROPOSE_NEXT_STEP


class TestPrerequisitesAndTheNextStep:
    def test_a_step_waits_for_a_fact_it_needs(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][2]["requires"]["facts"] = ["full_name"]
        state = state_of(interested(), known("registration_status", "registered"), doc=doc)
        assert state.status is WorkflowStatus.BLOCKED and state.current.id == "pay"
        assert state.blocked_by == ("full_name",) and state.ask_keys == ("full_name",)
        ready = state_of(*registered(), doc=doc)
        assert ready.status is WorkflowStatus.IN_PROGRESS and ready.blocked_by == ()

    def test_a_blocked_step_can_be_told_apart_in_what_the_model_sees(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][2]["requires"]["facts"] = ["full_name"]
        brain, llm = make_brain(doc, answer())
        brain.decide(
            request(facts=[interested(), known("registration_status", "registered")], **ONGOING)
        )
        workflow = payload_of(llm)["workflow"]
        assert workflow["status"] == "blocked" and workflow["waiting_for_facts"] == ["full_name"]

    def test_a_required_step_that_is_not_done_comes_first(self):
        doc = sale(
            commerce_doc(),
            {"id": "recommend", "kind": "recommend"},
            {"id": "pay", "kind": "payment", "requires": {"steps": ["register"]},
             "completes_when": {"facts": [{"key": "payment_status"}]}},
            {"id": "register", "kind": "register",
             "completes_when": {"facts": [{"key": "registration_status"}]}},
        )  # fmt: skip
        assert state_of(interested(), doc=doc).current.id == "register"

    def test_an_explicit_next_step_skips_the_ones_in_between(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][1]["next"] = "confirm"
        state = state_of(*registered(), doc=doc)
        assert state.current.id == "confirm"
        assert ("pay", False) in step_ids(state)  # never reached, never done

    def test_a_step_can_be_skipped_when_its_resources_are_missing(self):
        doc = deepcopy(commerce_doc())
        doc["payment"]["methods"] = [
            {**m, "applies_to": {"countries": ["Country B"]}} for m in doc["payment"]["methods"]
        ]
        pay = doc["conversion"]["workflows"][0]["steps"][2]
        pay["fallback"] = "skip"
        state = state_of(*registered(), country("Country A"), doc=doc)
        assert state.current.id == "confirm"

    def test_or_send_the_prospect_to_another_step(self):
        doc = deepcopy(commerce_doc())
        doc["payment"]["methods"] = [
            {**m, "applies_to": {"countries": ["Country B"]}} for m in doc["payment"]["methods"]
        ]
        doc["conversion"]["workflows"][0]["steps"][2]["fallback"] = "register"
        doc["conversion"]["workflows"][0]["steps"][1]["completes_when"]["facts"][0]["equals"] = [
            "never"
        ]
        # register never completes, so the fallback (register) keeps being the current step
        assert state_of(*registered(), country("Country A"), doc=doc).current.id == "register"


class TestRegistration:
    def test_registration_can_be_completed_without_a_person(self):
        decision = decide(
            answer(message="Could you tell me your full name?", discovery_key="full_name"),
            doc=commerce_doc(),
            facts=[interested()],
            **ONGOING,
        )
        assert decision.decision_type is DecisionType.SEND_MESSAGE
        assert decision.metadata["workflow_step"] == "register"
        assert decision.metadata["discovery_key"] == "full_name"

    def test_the_agent_may_only_ask_what_registration_collects(self):
        message = refused(
            commerce_doc(),
            answer(message="What sector are you in?", discovery_key="sector"),
            facts=[interested()],
        )
        assert "'sector' is not needed for advance" in message

    def test_registration_can_require_a_person(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][1]["human"] = "required"
        state = state_of(interested(), doc=doc)
        assert state.forced == {DecisionType.HANDOFF_TO_HUMAN} and state.human == "required"
        assert "requires handoff_to_human" in refused(doc, answer(), facts=[interested()])
        accepted = decide(handoff(), doc=doc, facts=[interested()], **ONGOING)
        assert accepted.decision_type is DecisionType.HANDOFF_TO_HUMAN

    def test_human_assisted_registration_hands_over(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][1]["registration"] = {"mode": "human_assisted"}
        assert state_of(interested(), doc=doc).forced == {DecisionType.HANDOFF_TO_HUMAN}

    def test_self_service_registration_points_to_the_configured_link(self):
        doc = deepcopy(commerce_doc())
        url = "https://register.example.org/form?id=7"
        doc["conversion"]["workflows"][0]["steps"][1]["registration"] = {
            "mode": "self_service", "url": url}  # fmt: skip
        ok = decide(
            answer(message=f"Register here: {url}"), doc=doc, facts=[interested()], **ONGOING
        )
        assert url in ok.message.text
        message = refused(
            doc,
            answer(message="Register at https://register.example.org/other"),
            facts=[interested()],
        )
        assert "is not a configured link" in message

    def test_the_step_objective_and_stage_reach_the_model(self):
        doc = deepcopy(commerce_doc())
        doc["stages"] = [{"id": "registering", "label": "Registering"}]
        doc["conversion"]["workflows"][0]["steps"][1]["stage"] = "registering"
        brain, llm = make_brain(doc, answer())
        brain.decide(request(facts=[interested()], **ONGOING))
        current = payload_of(llm)["workflow"]["current_step"]
        assert current["objective"] == "Collect what registration needs."
        assert current["suggested_stage"] == "registering" and current["kind"] == "register"


class TestPayment:
    def test_online_payment_gives_the_configured_link_exactly(self):
        decision = decide(
            answer(message="You can pay now.\n[[payment:online-card]]", language="fr"),
            doc=commerce_doc(),
            facts=at_payment("Country A"),
            **ONGOING,
        )
        assert decision.message.text == f"You can pay now.\nPay securely here:\n{PAY_URL}"
        assert decision.metadata["exact_values"] == "[[payment:online-card]]"
        assert decision.metadata["workflow_step"] == "pay"

    def test_offline_payment_gives_the_configured_instructions_exactly(self):
        decision = decide(
            answer(message="Please transfer.\n[[payment:bank-transfer]]", language="fr"),
            doc=commerce_doc(),
            facts=at_payment("Country A"),
            **ONGOING,
        )
        assert decision.message.text.endswith(f"Compte: {ACCOUNT_NUMBER}")
        assert "Acme  Test SARL" in decision.message.text

    def test_several_methods_can_be_offered_together(self):
        decision = decide(
            answer(message="[[payment:online-card]]\nor\n[[payment:bank-transfer]]", language="fr"),
            doc=commerce_doc(),
            facts=at_payment("Country A"),
            **ONGOING,
        )
        assert PAY_URL in decision.message.text and ACCOUNT_NUMBER in decision.message.text

    def test_a_method_that_does_not_apply_is_refused(self):
        message = refused(
            commerce_doc(),
            answer(message="Pay cash: [[payment:cash]]", language="fr"),
            facts=at_payment("Country A"),
        )
        assert "[[payment:cash]] is not available now" in message
        assert "online-card" in message and "bank-transfer" in message  # what is available

    def test_but_it_is_available_where_it_applies(self):
        decision = decide(
            answer(message="[[payment:cash]]", language="ar"),
            doc=commerce_doc(),
            facts=at_payment("Country B"),
            **ONGOING,
        )
        assert decision.message.text == "Bring the amount to the office."

    def test_payment_details_are_not_available_before_the_payment_step(self):
        message = refused(
            commerce_doc(),
            answer(message="[[payment:bank-transfer]]"),
            facts=[interested()],
        )
        assert "is not available now" in message

    def test_nor_after_it(self):
        message = refused(
            commerce_doc(),
            answer(message="[[payment:bank-transfer]]"),
            facts=[*registered(), known("payment_status", "confirmed")],
        )
        assert "not available" in message or "requires close" in message

    def test_what_a_payment_step_needs_is_visible_to_the_model_without_the_values(self):
        brain, llm = make_brain(
            commerce_doc(), answer(message="[[payment:bank-transfer]]", language="fr")
        )
        brain.decide(request(facts=at_payment("Country A"), **ONGOING))
        methods = payload_of(llm)["workflow"]["payment_methods"]
        assert [m["id"] for m in methods] == ["online-card", "bank-transfer"]
        assert methods[1]["confirmation"] == {
            "mode": "manual", "note": "A person confirms the transfer."}  # fmt: skip
        assert methods[0]["confirmation"]["mode"] == "provider"

    def test_payment_confirmation_can_be_left_to_a_person_or_the_provider(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][3]["human"] = "required"
        state = state_of(*registered(), known("payment_status", "received"), doc=doc)
        assert state.current.id == "confirm" and state.forced == {DecisionType.HANDOFF_TO_HUMAN}

    def test_no_applicable_method_falls_back_to_the_configured_fallback(self):
        doc = deepcopy(commerce_doc())
        doc["payment"]["methods"] = [
            {**m, "applies_to": {"countries": ["Country B"]}} for m in doc["payment"]["methods"]
        ]
        state = state_of(*registered(), country("Country A"), doc=doc)
        assert state.status is WorkflowStatus.UNAVAILABLE and state.fallback == "handoff"
        assert state.forced == {DecisionType.HANDOFF_TO_HUMAN}
        assert "no payment method applies" in state.reason

    @pytest.mark.parametrize(
        ("fallback", "decision"),
        [("close", DecisionType.CLOSE), ("park", DecisionType.NO_ACTION)],
    )
    def test_the_fallback_can_close_or_wait(self, fallback, decision):
        doc = deepcopy(commerce_doc())
        doc["payment"]["methods"] = [
            {**m, "applies_to": {"countries": ["Country B"]}} for m in doc["payment"]["methods"]
        ]
        doc["conversion"]["workflows"][0]["steps"][2]["fallback"] = fallback
        state = state_of(*registered(), country("Country A"), doc=doc)
        assert decision in state.forced

    def test_with_handoffs_switched_off_the_default_fallback_is_to_wait(self):
        doc = deepcopy(commerce_doc())
        doc["payment"]["methods"] = [
            {**m, "applies_to": {"countries": ["Country B"]}} for m in doc["payment"]["methods"]
        ]
        doc["handoff"] = {"enabled": False}
        doc["conversation"]["intents"] = {}
        doc["incoming"] = {"out_of_scope": {"reply": "polite_redirect", "then": "none"}}
        for product in doc["catalog"]["products"]:
            for rule in (product.get("qualification") or {}).get("disqualifiers", []):
                rule["effect"] = "not_a_fit"
        state = state_of(*registered(), country("Country A"), doc=doc)
        assert state.fallback == "park"


class TestAppointments:
    def appointment_doc(self, appointment, kind="appointment"):
        return sale(
            commerce_doc(),
            {"id": "recommend", "kind": "recommend"},
            {
                "id": "book",
                "kind": kind,
                "appointment": appointment,
                "completes_when": {"facts": [{"key": "registration_status", "equals": ["booked"]}]},
            },
            {"id": "done", "kind": "close"},
        )

    def test_an_appointment_by_a_configured_booking_link(self):
        doc = self.appointment_doc(
            {"supported": True, "mode": "booking_url", "booking_url": BOOKING_URL}
        )
        decision = decide(
            answer(message="Pick a slot: [[booking:book]]"),
            doc=doc,
            facts=[interested()],
            **ONGOING,
        )
        assert decision.message.text == f"Pick a slot: {BOOKING_URL}"
        assert decision.metadata["exact_values"] == "[[booking:book]]"

    def test_the_booking_link_is_never_shown_to_the_model_and_never_typed_by_it(self):
        doc = self.appointment_doc(
            {"supported": True, "mode": "booking_url", "booking_url": BOOKING_URL}
        )
        brain, llm = make_brain(doc, answer(message="ok [[booking:book]]"))
        brain.decide(request(facts=[interested()], **ONGOING))
        assert BOOKING_URL not in "\n".join(m.content for m in llm.calls[0].messages)
        message = refused(doc, answer(message=f"Book at {BOOKING_URL}"), facts=[interested()])
        assert "is not a configured link" in message

    def test_a_callback_the_prospect_asks_for_is_scheduled_for_the_time_they_gave(self):
        doc = self.appointment_doc({"supported": True, "mode": "callback"}, kind="callback")
        when = NOW.replace(hour=15).replace(day=NOW.day + 2).isoformat()
        decision = decide(
            answer(follow_up={"situation": "callback_promised", "requested_at": when}),
            doc=doc,
            facts=[interested()],
            **ONGOING,
        )
        assert decision.next_action_type.value == "callback"
        assert decision.metadata["workflow_step"] == "book"

    def test_a_callback_is_handed_to_a_person_by_default_when_it_falls_due(self):
        doc = self.appointment_doc({"supported": True, "mode": "callback"}, kind="callback")
        ctx = context_for(doc, facts=[interested()], action=CALLBACK, messages=ONGOING["messages"])
        assert ctx.plan.objective is Objective.HAND_OVER

    def test_but_the_client_can_have_the_agent_carry_the_callback_out(self):
        doc = self.appointment_doc(
            {"supported": True, "mode": "callback", "handled_by": "agent"}, kind="callback"
        )
        doc["conversion"]["appointment"] = {"supported": True, "handled_by": "agent"}
        ctx = context_for(doc, facts=[interested()], action=CALLBACK, messages=ONGOING["messages"])
        assert ctx.plan.objective is not Objective.HAND_OVER
        assert ctx.plan.discovery.status is not None

    def test_a_person_can_arrange_the_appointment_when_the_client_says_so(self):
        doc = self.appointment_doc({"supported": True, "mode": "human_scheduling"})
        state = state_of(interested(), doc=doc)
        assert state.forced == {DecisionType.HANDOFF_TO_HUMAN}

    def test_the_clients_default_appointment_applies_to_a_step_that_names_none(self):
        doc = self.appointment_doc({"supported": True, "mode": "callback"}, kind="callback")
        doc["conversion"]["workflows"][0]["steps"][1].pop("appointment")
        doc["conversion"]["appointment"] = {"supported": True}
        assert state_of(interested(), doc=doc).appointment is not None

    def test_a_product_whose_appointment_is_not_supported_uses_the_clients_default(self):
        doc = self.appointment_doc({"supported": True, "mode": "callback"}, kind="callback")
        doc["conversion"]["workflows"][0]["steps"][1].pop("appointment")
        doc["conversion"]["appointment"] = {"supported": True}
        doc = set_at(doc, "catalog.products.0.appointment", {"supported": False})
        assert state_of(interested(), doc=doc).appointment.supported

    def test_a_product_can_bring_its_own_appointment_settings(self):
        doc = self.appointment_doc({"supported": True, "mode": "callback"}, kind="callback")
        doc["conversion"]["workflows"][0]["steps"][1].pop("appointment")
        doc["conversion"]["appointment"] = {"supported": True, "mode": "callback"}
        doc = set_at(doc, "catalog.products.0.appointment", {
            "supported": True, "mode": "booking_url", "booking_url": BOOKING_URL})  # fmt: skip
        state = state_of(interested(), doc=doc)
        assert state.appointment.mode == "booking_url"


class TestHandoffIsConfigurable:
    def test_a_step_can_involve_no_person(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][1]["human"] = "none"
        message = refused(doc, handoff(), facts=[interested()])
        assert "involves no person" in message

    def test_unless_the_prospect_asks_for_one(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"][0]["steps"][1]["human"] = "none"
        decision = decide(
            handoff(analysis={"intent": "human_requested"}),
            doc=doc,
            facts=[interested()],
            **ONGOING,
        )
        assert decision.decision_type is DecisionType.HANDOFF_TO_HUMAN

    def test_a_step_can_permit_a_person_without_requiring_one(self):
        state = state_of(interested())  # register: human optional by default
        assert state.human == "optional" and not state.forced
        assert decide(
            handoff(), facts=[interested()], **ONGOING, doc=commerce_doc()
        ).decision_type is (DecisionType.HANDOFF_TO_HUMAN)
        assert decide(
            answer(), facts=[interested()], **ONGOING, doc=commerce_doc()
        ).decision_type is (DecisionType.SEND_MESSAGE)

    def test_a_handoff_step_hands_the_conversation_over(self):
        doc = sale(
            commerce_doc(),
            {"id": "recommend", "kind": "recommend"},
            {"id": "person", "kind": "handoff"},
        )
        state = state_of(interested(), doc=doc)
        assert state.forced == {DecisionType.HANDOFF_TO_HUMAN} and state.human == "required"

    def test_handoff_can_be_disabled_altogether(self):
        doc = deepcopy(commerce_doc())
        doc["handoff"] = {"enabled": False}
        doc["conversation"]["intents"] = {}
        doc["incoming"] = {"out_of_scope": {"reply": "polite_redirect", "then": "none"}}
        for product in doc["catalog"]["products"]:
            for rule in (product.get("qualification") or {}).get("disqualifiers", []):
                rule["effect"] = "not_a_fit"
        ctx = context_for(doc, facts=[interested()])
        assert DecisionType.HANDOFF_TO_HUMAN not in ctx.plan.allowed_decisions
        assert "not open in this situation" in refused(doc, handoff(), facts=[interested()])

    def test_a_close_step_ends_the_conversation(self):
        state = state_of(*registered(), known("payment_status", "confirmed"))
        assert "requires close" in refused(
            commerce_doc(), answer(), facts=[*registered(), known("payment_status", "confirmed")]
        )
        accepted = decide(
            closing(),
            doc=commerce_doc(),
            facts=[*registered(), known("payment_status", "confirmed")],
            **ONGOING,
        )
        assert accepted.decision_type is DecisionType.CLOSE and state.current.kind == "close"

    def test_a_person_asking_outranks_a_step_that_would_close(self):
        facts = [*registered(), known("payment_status", "confirmed")]
        decision = decide(
            handoff(analysis={"intent": "human_requested"}),
            doc=commerce_doc(),
            facts=facts,
            **ONGOING,
        )
        assert decision.decision_type is DecisionType.HANDOFF_TO_HUMAN


class TestWhichWorkflowApplies:
    def two_workflows(self):
        doc = deepcopy(commerce_doc())
        doc["conversion"]["workflows"].append(
            {
                "id": "short",
                "steps": [{"id": "recommend", "kind": "recommend"}, {"id": "end", "kind": "close"}],
            }
        )
        doc["conversion"]["workflows"].append(
            {"id": "shortest", "steps": [{"id": "only", "kind": "close"}]}
        )
        return doc

    def test_the_default_workflow(self):
        assert state_of(interested(), doc=self.two_workflows()).workflow.id == "sale"

    def test_a_markets_workflow_overrides_the_default(self):
        doc = set_at(self.two_workflows(), "markets.markets.0.workflow", "short")
        assert state_of(interested(), country("Country A"), doc=doc).workflow.id == "short"
        assert state_of(interested(), country("Country B"), doc=doc).workflow.id == "sale"
        assert state_of(interested(), doc=doc).workflow.id == "sale"  # place unknown: the default

    def test_a_products_workflow_overrides_the_markets(self):
        doc = set_at(self.two_workflows(), "markets.markets.0.workflow", "short")
        doc = set_at(doc, "catalog.products.0.workflow", "shortest")
        assert state_of(interested(), country("Country A"), doc=doc).workflow.id == "shortest"

    def test_no_default_and_no_pointer_means_no_workflow(self):
        doc = self.two_workflows()
        doc["conversion"].pop("default_workflow")
        assert state_of(interested(), doc=doc) is None

    def test_the_workflow_is_named_in_the_decision_for_the_audit_trail(self):
        decision = decide(answer(), doc=commerce_doc(), facts=[interested()], **ONGOING)
        assert decision.metadata["workflow"] == "sale"


class TestTheShortcutStillSkipsTheLadderNotTheWorkflow:
    def test_a_confirmed_interest_skips_recommending_but_registration_still_asks(self):
        # settings_doc has the shortcut "interest confirmed: propose the next step"
        plan = context_for(commerce_doc(), facts=[interested()]).plan
        assert plan.discovery.status is DiscoveryStatus.ASK and plan.discovery.aim == "advance"


class TestDeterminism:
    def test_the_same_facts_always_give_the_same_state(self):
        first = state_of(*at_payment("Country A"))
        second = state_of(*reversed(at_payment("Country A")))
        assert (first.current.id, [m.method.id for m in first.methods]) == (
            second.current.id, [m.method.id for m in second.methods]
        )  # fmt: skip

    def test_facts_from_any_source_complete_a_step_when_they_hold(self):
        for source in (FactSource.HUMAN, FactSource.SYSTEM, FactSource.PROSPECT):
            done = known("registration_status", "registered", CONFIRMED, source)
            assert state_of(interested(), done).current.id == "pay"
