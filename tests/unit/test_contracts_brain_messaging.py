from datetime import UTC, datetime, timedelta

import pytest
from factories import (
    NAIVE_NOW,
    NOW,
    make_action,
    make_brain_request,
    make_client_context,
    make_conversation,
    make_prospect,
)

from sales_core.contracts import (
    AuditEvent,
    BrainDecision,
    Channel,
    ClientId,
    DecisionType,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMRole,
    MessageDraft,
    OutboundMessage,
    ProspectId,
    SendResult,
    SendStatus,
)


class TestBrainRequest:
    def test_valid_request_exposes_all_context(self):
        request = make_brain_request()
        assert request.client.client_id == request.prospect.client_id
        assert request.conversation.messages == ()

    def test_settings_are_opaque_and_read_only(self):
        client = make_client_context(settings={"tone": "warm"})
        assert client.settings["tone"] == "warm"
        with pytest.raises(TypeError):
            client.settings["tone"] = "cold"  # type: ignore[index]

    @pytest.mark.parametrize(
        "part",
        [
            {"prospect": make_prospect(client_id=ClientId("client-b"))},
            {"conversation": make_conversation(client_id=ClientId("client-b"))},
            {"action": make_action(client_id=ClientId("client-b"))},
        ],
    )
    def test_mixing_clients_is_rejected(self, part):
        with pytest.raises(ValueError, match="one client_id"):
            make_brain_request(**part)

    @pytest.mark.parametrize(
        "part",
        [
            {"conversation": make_conversation(prospect_id=ProspectId("p-2"))},
            {"action": make_action(prospect_id=ProspectId("p-2"))},
        ],
    )
    def test_mixing_prospects_is_rejected(self, part):
        with pytest.raises(ValueError, match="same prospect"):
            make_brain_request(**part)

    def test_now_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="now"):
            make_brain_request(now=NAIVE_NOW)


class TestBrainDecision:
    def test_send_message_requires_a_message(self):
        with pytest.raises(ValueError, match="SEND_MESSAGE"):
            BrainDecision(decision_type=DecisionType.SEND_MESSAGE)

    @pytest.mark.parametrize(
        "decision_type",
        [DecisionType.NO_ACTION, DecisionType.CLOSE, DecisionType.HANDOFF_TO_HUMAN],
    )
    def test_other_decisions_must_not_carry_a_message(self, decision_type):
        with pytest.raises(ValueError, match="SEND_MESSAGE"):
            BrainDecision(decision_type=decision_type, message=MessageDraft(text="hi"))

    def test_schedule_follow_up_requires_a_date(self):
        with pytest.raises(ValueError, match="next_follow_up_at"):
            BrainDecision(decision_type=DecisionType.SCHEDULE_FOLLOW_UP)

    def test_follow_up_date_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="next_follow_up_at"):
            BrainDecision(
                decision_type=DecisionType.SCHEDULE_FOLLOW_UP,
                next_follow_up_at=NAIVE_NOW,
            )

    def test_send_message_may_also_schedule_the_next_follow_up(self):
        decision = BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE,
            message=MessageDraft(text="Hello", language="xx"),
            next_follow_up_at=NOW + timedelta(days=3),
            new_stage="any-client-specific-label",
            rationale="first touch",
        )
        assert decision.new_stage == "any-client-specific-label"

    def test_no_action_needs_nothing_else(self):
        assert BrainDecision(decision_type=DecisionType.NO_ACTION).message is None


class TestOutboundMessageAndSendResult:
    def test_outbound_message_requires_recipient_and_idempotency_key(self):
        base = {
            "client_id": ClientId("client-a"),
            "prospect_id": ProspectId("p-1"),
            "channel": Channel.WHATSAPP,
            "draft": MessageDraft(text="Hi"),
        }
        with pytest.raises(ValueError, match="recipient"):
            OutboundMessage(**base, recipient="", idempotency_key="k")
        with pytest.raises(ValueError, match="idempotency_key"):
            OutboundMessage(**base, recipient="+10000000000", idempotency_key="")

    def test_accepted_result_is_ok_and_not_retryable(self):
        result = SendResult(status=SendStatus.ACCEPTED, timestamp=NOW, external_id="ext-1")
        assert result.ok and not result.retryable

    def test_transient_failure_is_retryable(self):
        result = SendResult(
            status=SendStatus.FAILED_TRANSIENT, timestamp=NOW, error_message="rate limited"
        )
        assert not result.ok and result.retryable

    def test_permanent_failure_is_not_retryable(self):
        result = SendResult(
            status=SendStatus.FAILED_PERMANENT, timestamp=NOW, error_message="invalid number"
        )
        assert not result.ok and not result.retryable

    def test_failure_must_explain_itself(self):
        with pytest.raises(ValueError, match="error_message"):
            SendResult(status=SendStatus.FAILED_PERMANENT, timestamp=NOW)

    def test_timestamp_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timestamp"):
            SendResult(status=SendStatus.ACCEPTED, timestamp=NAIVE_NOW)


class TestAuditEvent:
    def test_valid_event(self):
        event = AuditEvent(
            timestamp=datetime.now(UTC),
            client_id=ClientId("client-a"),
            event_type="action.sent",
            actor="core",
            details={"action_id": "a-1"},
        )
        assert event.details["action_id"] == "a-1"

    def test_actor_and_event_type_are_mandatory(self):
        base = {"timestamp": NOW, "client_id": ClientId("client-a")}
        with pytest.raises(ValueError, match="event_type"):
            AuditEvent(**base, event_type="", actor="core")
        with pytest.raises(ValueError, match="actor"):
            AuditEvent(**base, event_type="x.y", actor="")


class TestLLMContracts:
    def test_request_needs_at_least_one_message(self):
        with pytest.raises(ValueError, match="messages"):
            LLMRequest(messages=())

    def test_request_freezes_messages_and_schema(self):
        schema = {"type": "object"}
        request = LLMRequest(
            messages=[LLMMessage(role=LLMRole.USER, content="hi")], response_schema=schema
        )
        schema["type"] = "array"
        assert isinstance(request.messages, tuple)
        assert request.response_schema == {"type": "object"}

    def test_max_output_tokens_must_be_positive(self):
        with pytest.raises(ValueError, match="max_output_tokens"):
            LLMRequest(messages=[LLMMessage(role=LLMRole.USER, content="hi")], max_output_tokens=0)

    def test_response_defaults(self):
        response = LLMResponse(text="ok")
        assert response.parsed is None and response.model is None
