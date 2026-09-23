"""Contract additions made for the Engine, plus the fake's new port method."""

from dataclasses import replace
from datetime import timedelta

import pytest
from engine_harness import prospect
from factories import CLIENT, NAIVE_NOW, NOW, make_action

from sales_core.contracts import (
    SCHEDULABLE_ACTION_TYPES,
    ActionStatus,
    BrainDecision,
    Channel,
    ContactInfo,
    DecisionType,
    InboundMessage,
    MessageDraft,
    ProspectId,
    SalesActionType,
)
from sales_integrations.fakes import FakeSalesRepository


class TestSalesActionPostSendPlan:
    def test_defaults_mean_keep_the_stage_and_clear_the_schedule(self):
        action = make_action()
        assert action.next_stage is None
        assert action.next_action_type is None and action.next_action_due_at is None

    def test_next_action_type_and_date_come_together(self):
        with pytest.raises(ValueError, match="together"):
            make_action(next_action_type=SalesActionType.FOLLOW_UP)
        with pytest.raises(ValueError, match="together"):
            make_action(next_action_due_at=NOW)

    @pytest.mark.parametrize("kind", [SalesActionType.FIRST_CONTACT, SalesActionType.REPLY])
    def test_only_followup_reminder_and_callback_can_be_scheduled(self, kind):
        with pytest.raises(ValueError, match="cannot be scheduled"):
            make_action(next_action_type=kind, next_action_due_at=NOW)

    def test_next_action_date_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="next_action_due_at"):
            make_action(next_action_type=SalesActionType.FOLLOW_UP, next_action_due_at=NAIVE_NOW)

    def test_schedulable_types(self):
        assert SCHEDULABLE_ACTION_TYPES == {
            SalesActionType.FOLLOW_UP,
            SalesActionType.REMINDER,
            SalesActionType.CALLBACK,
        }

    def test_completed_is_a_status(self):
        assert ActionStatus.COMPLETED.value == "completed"


class TestBrainDecisionNextActionType:
    def _send(self, **kw):
        return BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE, message=MessageDraft(text="Hi"), **kw
        )

    def test_a_date_alone_defaults_to_a_follow_up(self):
        decision = self._send(next_follow_up_at=NOW + timedelta(days=1))
        assert decision.next_action_type is SalesActionType.FOLLOW_UP

    @pytest.mark.parametrize(
        "kind", [SalesActionType.FOLLOW_UP, SalesActionType.REMINDER, SalesActionType.CALLBACK]
    )
    def test_supports_follow_up_reminder_and_callback(self, kind):
        decision = self._send(next_follow_up_at=NOW + timedelta(days=1), next_action_type=kind)
        assert decision.next_action_type is kind

    def test_no_date_means_no_next_action(self):
        assert self._send().next_action_type is None

    def test_a_type_without_a_date_is_rejected(self):
        with pytest.raises(ValueError, match="requires next_follow_up_at"):
            self._send(next_action_type=SalesActionType.REMINDER)

    @pytest.mark.parametrize("kind", [SalesActionType.FIRST_CONTACT, SalesActionType.REPLY])
    def test_other_types_are_rejected(self, kind):
        with pytest.raises(ValueError, match="cannot be scheduled"):
            self._send(next_follow_up_at=NOW + timedelta(days=1), next_action_type=kind)


class TestInboundMessage:
    def test_valid(self):
        message = InboundMessage(
            channel=Channel.WHATSAPP,
            sender="+1",
            external_id="w1",
            text="hi",
            timestamp=NOW,
            metadata={"kind": "text"},
        )
        assert message.metadata["kind"] == "text"

    @pytest.mark.parametrize("field", ["sender", "external_id"])
    def test_required_text_fields(self, field):
        kwargs = {
            "channel": Channel.WHATSAPP,
            "sender": "+1",
            "external_id": "w1",
            "text": "",
            "timestamp": NOW,
        }
        kwargs[field] = " "
        with pytest.raises(ValueError, match=field):
            InboundMessage(**kwargs)

    def test_timestamp_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timestamp"):
            InboundMessage(
                channel=Channel.WHATSAPP,
                sender="+1",
                external_id="w1",
                text="",
                timestamp=NAIVE_NOW,
            )


class TestFakeFindProspectByContact:
    def _repo(self):
        alex = prospect(
            "p1", contact=ContactInfo(phone="+1 (555) 000-0001", email="Alex@Example.test")
        )
        return FakeSalesRepository(CLIENT, prospects=[alex, prospect("p2")])

    def test_phone_matching_ignores_formatting(self):
        repo = self._repo()
        for sender in ("+15550000001", "15550000001", "1 555 000 0001"):
            found = repo.find_prospect_by_contact(phone=sender)
            assert found is not None and found.prospect_id == ProspectId("p1")

    def test_email_matching_ignores_case(self):
        found = self._repo().find_prospect_by_contact(email=" alex@example.TEST ")
        assert found is not None and found.prospect_id == "p1"

    def test_no_match_or_no_criteria_returns_none(self):
        repo = self._repo()
        assert repo.find_prospect_by_contact(phone="+19999999999") is None
        assert repo.find_prospect_by_contact() is None

    def test_a_blank_contact_never_matches(self):
        repo = FakeSalesRepository(CLIENT, prospects=[prospect("p1", contact=ContactInfo())])
        assert repo.find_prospect_by_contact(phone="", email="") is None

    def test_failure_injection_supports_the_new_method(self):
        repo = self._repo()
        repo.fail_next("find_prospect_by_contact")
        with pytest.raises(RuntimeError):
            repo.find_prospect_by_contact(phone="+15550000001")
        assert replace(prospect("p1"), contact=ContactInfo()).contact.phone is None
