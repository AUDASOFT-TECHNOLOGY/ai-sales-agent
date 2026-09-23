"""Builders for valid contract objects. Deliberately use invented, client-neutral data."""

from datetime import UTC, datetime

from sales_core.contracts import (
    ActionId,
    BrainRequest,
    Channel,
    ClientContext,
    ClientId,
    ContactInfo,
    Conversation,
    ConversationId,
    Direction,
    Message,
    MessageId,
    Prospect,
    ProspectId,
    SalesAction,
    SalesActionType,
)

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
NAIVE_NOW = NOW.replace(tzinfo=None)  # for tests that expect naive datetimes to be rejected
CLIENT = ClientId("client-a")
PROSPECT = ProspectId("p-1")
CONVERSATION = ConversationId("c-1")


def make_prospect(**overrides: object) -> Prospect:
    fields: dict[str, object] = {
        "client_id": CLIENT,
        "prospect_id": PROSPECT,
        "display_name": "Alex Example",
        "contact": ContactInfo(phone="+10000000000", preferred_channel=Channel.WHATSAPP),
    }
    return Prospect(**(fields | overrides))  # type: ignore[arg-type]


def make_action(**overrides: object) -> SalesAction:
    fields: dict[str, object] = {
        "action_id": ActionId("a-1"),
        "client_id": CLIENT,
        "prospect_id": PROSPECT,
        "action_type": SalesActionType.FIRST_CONTACT,
        "due_at": NOW,
        "idempotency_key": "client-a:p-1:first_contact:2026-01-15",
    }
    return SalesAction(**(fields | overrides))  # type: ignore[arg-type]


def make_message(**overrides: object) -> Message:
    fields: dict[str, object] = {
        "message_id": MessageId("m-1"),
        "client_id": CLIENT,
        "prospect_id": PROSPECT,
        "conversation_id": CONVERSATION,
        "direction": Direction.OUTBOUND,
        "channel": Channel.WHATSAPP,
        "text": "Hello",
        "timestamp": NOW,
    }
    return Message(**(fields | overrides))  # type: ignore[arg-type]


def make_conversation(**overrides: object) -> Conversation:
    fields: dict[str, object] = {
        "conversation_id": CONVERSATION,
        "client_id": CLIENT,
        "prospect_id": PROSPECT,
    }
    return Conversation(**(fields | overrides))  # type: ignore[arg-type]


def make_client_context(**overrides: object) -> ClientContext:
    fields: dict[str, object] = {"client_id": CLIENT, "display_name": "Client A"}
    return ClientContext(**(fields | overrides))  # type: ignore[arg-type]


def make_brain_request(**overrides: object) -> BrainRequest:
    fields: dict[str, object] = {
        "client": make_client_context(),
        "prospect": make_prospect(),
        "conversation": make_conversation(),
        "action": make_action(),
        "now": NOW,
    }
    return BrainRequest(**(fields | overrides))  # type: ignore[arg-type]
