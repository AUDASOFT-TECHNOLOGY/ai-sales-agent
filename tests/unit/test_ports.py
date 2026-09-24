"""Ports are Protocols: any object with the right shape qualifies, nothing else is required.

The classes below are minimal test doubles built only from contracts. They also show that a
Brain and a store can be written without any client, vendor or integration import.
"""

from collections.abc import Collection, Sequence
from datetime import datetime

import pytest
from factories import NOW, make_brain_request, make_prospect

from sales_core.contracts import (
    ActionStatus,
    AuditEvent,
    BrainDecision,
    BrainRequest,
    Channel,
    ClientId,
    Conversation,
    DecisionType,
    LLMRequest,
    LLMResponse,
    Message,
    MessageDraft,
    OutboundMessage,
    Prospect,
    ProspectId,
    SalesAction,
    SendResult,
    SendStatus,
)
from sales_core.ports import (
    Brain,
    BrainSettingsSpec,
    Clock,
    LLMProvider,
    MessagingChannel,
    SalesRepository,
)


class EchoBrain:
    def decide(self, request: BrainRequest) -> BrainDecision:
        return BrainDecision(
            decision_type=DecisionType.SEND_MESSAGE,
            message=MessageDraft(text=f"Hello {request.prospect.display_name}"),
        )


class InMemoryStore:
    def __init__(self, client_id: ClientId) -> None:
        self._client_id = client_id
        self.prospects: dict[ProspectId, Prospect] = {}
        self.actions: dict[str, SalesAction] = {}
        self.messages: list[Message] = []
        self.audit: list[AuditEvent] = []

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    def list_prospects(self) -> Sequence[Prospect]:
        return list(self.prospects.values())

    def get_prospect(self, prospect_id: ProspectId) -> Prospect | None:
        return self.prospects.get(prospect_id)

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        return None

    def update_prospects(self, prospects: Sequence[Prospect]) -> None:
        self.prospects.update({p.prospect_id: p for p in prospects})

    def list_actions(
        self, statuses: Collection[ActionStatus] | None = None
    ) -> Sequence[SalesAction]:
        return [a for a in self.actions.values() if statuses is None or a.status in statuses]

    def find_action_by_idempotency_key(self, key: str) -> SalesAction | None:
        return next((a for a in self.actions.values() if a.idempotency_key == key), None)

    def save_actions(self, actions: Sequence[SalesAction]) -> None:
        self.actions.update({a.action_id: a for a in actions})

    def get_conversation(self, prospect_id: ProspectId) -> Conversation:
        mine = tuple(m for m in self.messages if m.prospect_id == prospect_id)
        return Conversation(
            conversation_id=f"conv-{prospect_id}",  # type: ignore[arg-type]
            client_id=self._client_id,
            prospect_id=prospect_id,
            messages=mine,
        )

    def append_messages(self, messages: Sequence[Message]) -> None:
        self.messages.extend(messages)

    def append_audit_events(self, events: Sequence[AuditEvent]) -> None:
        self.audit.extend(events)


class RecordingChannel:
    channel = Channel.WHATSAPP  # a plain attribute satisfies a read-only property member

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        return SendResult(status=SendStatus.ACCEPTED, timestamp=NOW, external_id="ext-1")


class CannedLLM:
    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="canned")


class FixedClock:
    def now(self) -> datetime:
        return NOW


def test_conforming_objects_satisfy_their_ports():
    assert isinstance(EchoBrain(), Brain)
    assert isinstance(InMemoryStore(ClientId("client-a")), SalesRepository)
    assert isinstance(RecordingChannel(), MessagingChannel)
    assert isinstance(CannedLLM(), LLMProvider)
    assert isinstance(FixedClock(), Clock)


def test_objects_missing_a_method_do_not_satisfy_the_port():
    class NotABrain:
        def think(self, request: BrainRequest) -> BrainDecision: ...

    class HalfStore:
        client_id = ClientId("client-a")

        def list_prospects(self) -> Sequence[Prospect]:
            return []

    assert not isinstance(NotABrain(), Brain)
    assert not isinstance(HalfStore(), SalesRepository)
    assert not isinstance(object(), MessagingChannel)


def test_a_brain_works_from_contracts_alone():
    decision = EchoBrain().decide(make_brain_request())
    assert decision.decision_type is DecisionType.SEND_MESSAGE
    assert decision.message == MessageDraft(text="Hello Alex Example")


def test_store_round_trips_prospects_actions_history_and_audit():
    from factories import make_action, make_message

    store = InMemoryStore(ClientId("client-a"))
    store.update_prospects([make_prospect()])
    store.save_actions([make_action()])
    store.append_messages([make_message(conversation_id="conv-p-1")])
    store.append_audit_events(
        [AuditEvent(timestamp=NOW, client_id=store.client_id, event_type="a.b", actor="core")]
    )

    assert store.get_prospect(ProspectId("p-1")) == make_prospect()
    assert store.get_prospect(ProspectId("missing")) is None
    assert len(store.list_actions()) == 1
    assert store.list_actions({ActionStatus.SENT}) == []
    assert store.find_action_by_idempotency_key("client-a:p-1:first_contact:2026-01-15")
    assert store.find_action_by_idempotency_key("nope") is None
    assert len(store.get_conversation(ProspectId("p-1")).messages) == 1
    assert len(store.audit) == 1


def test_channel_reports_structured_results():
    channel = RecordingChannel()
    result = channel.send(
        OutboundMessage(
            client_id=ClientId("client-a"),
            prospect_id=ProspectId("p-1"),
            channel=Channel.WHATSAPP,
            recipient="+10000000000",
            draft=MessageDraft(text="Hi"),
            idempotency_key="k-1",
        )
    )
    assert result.ok and result.external_id == "ext-1"
    assert len(channel.sent) == 1


class TestBrainSettingsSpec:
    """A settings spec is a shape any Brain family can satisfy; nothing else is required."""

    class MinimalSpec:
        family = "minimal"
        schema_version = 1

        def json_schema(self):
            return {"type": "object"}

        def validate(self, settings, version):
            return [] if "ok" in settings else ["ok: is required"]

        def prepare(self, settings, version):
            if "ok" not in settings:
                raise ValueError("ok: is required")
            return dict(settings)

    def test_any_object_with_the_right_shape_qualifies(self):
        assert isinstance(self.MinimalSpec(), BrainSettingsSpec)

    def test_objects_missing_a_part_do_not(self):
        class NoPrepare:
            family = "x"
            schema_version = 1

            def json_schema(self):
                return {}

            def validate(self, settings, version):
                return []

        assert not isinstance(NoPrepare(), BrainSettingsSpec)
        assert not isinstance("a string", BrainSettingsSpec)
        assert not isinstance(None, BrainSettingsSpec)

    def test_the_contract_is_only_about_settings_and_versions(self):
        spec = self.MinimalSpec()
        assert spec.validate({}, 1) == ["ok: is required"]
        assert spec.prepare({"ok": 1}, 1) == {"ok": 1}
        with pytest.raises(ValueError):
            spec.prepare({}, 1)
