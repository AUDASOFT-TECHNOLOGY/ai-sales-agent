import pytest
from factories import CONVERSATION, NAIVE_NOW, make_conversation, make_message

from sales_core.contracts import ClientId, Direction, MessageDraft, ProspectId


class TestMessage:
    def test_carries_direction_channel_text_timestamp_and_metadata(self):
        message = make_message(direction=Direction.INBOUND, metadata={"type": "text"})
        assert message.direction is Direction.INBOUND
        assert message.metadata["type"] == "text"

    def test_metadata_is_read_only(self):
        with pytest.raises(TypeError):
            make_message().metadata["x"] = "y"  # type: ignore[index]

    def test_naive_timestamp_is_rejected(self):
        with pytest.raises(ValueError, match="timestamp"):
            make_message(timestamp=NAIVE_NOW)

    def test_empty_text_is_allowed_for_non_text_inbound_content(self):
        assert make_message(direction=Direction.INBOUND, text="").text == ""


class TestMessageDraft:
    def test_plain_text_draft_needs_text(self):
        with pytest.raises(ValueError, match="text"):
            MessageDraft(text="")

    def test_template_draft_may_have_no_free_text(self):
        draft = MessageDraft(text="", template_id="welcome_v1", template_params={"name": "Alex"})
        assert draft.template_params["name"] == "Alex"


class TestConversation:
    def test_empty_history_is_valid(self):
        assert make_conversation().messages == ()

    def test_messages_are_stored_as_a_tuple_in_given_order(self):
        first, second = make_message(message_id="m-1"), make_message(message_id="m-2")
        conversation = make_conversation(messages=[first, second])
        assert conversation.messages == (first, second)

    def test_rejects_a_message_from_another_conversation(self):
        stray = make_message(conversation_id="other")
        with pytest.raises(ValueError, match="does not belong"):
            make_conversation(messages=[stray])

    def test_rejects_a_message_from_another_client(self):
        stray = make_message(client_id=ClientId("client-b"), conversation_id=CONVERSATION)
        with pytest.raises(ValueError, match="does not belong"):
            make_conversation(messages=[stray])

    def test_rejects_a_message_from_another_prospect(self):
        stray = make_message(prospect_id=ProspectId("p-2"))
        with pytest.raises(ValueError, match="does not belong"):
            make_conversation(messages=[stray])
