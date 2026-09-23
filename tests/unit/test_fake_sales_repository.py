from dataclasses import replace

import pytest
from factories import CLIENT, CONVERSATION, NOW, PROSPECT, make_action, make_message, make_prospect

from sales_core.contracts import (
    ActionId,
    ActionStatus,
    AuditEvent,
    ClientId,
    ConversationId,
    ProspectId,
    ProspectStatus,
)
from sales_core.ports import SalesRepository
from sales_integrations.fakes import FakeSalesRepository

OTHER_CLIENT = ClientId("client-b")


def _repo(**seed) -> FakeSalesRepository:
    return FakeSalesRepository(CLIENT, **seed)


def _event(client_id: ClientId = CLIENT) -> AuditEvent:
    return AuditEvent(timestamp=NOW, client_id=client_id, event_type="x.y", actor="core")


def test_satisfies_the_port_and_is_bound_to_one_client():
    repo = _repo()
    assert isinstance(repo, SalesRepository)
    assert repo.client_id == CLIENT


class TestProspects:
    def test_seeded_prospects_are_listed_in_insertion_order(self):
        first, second = make_prospect(), make_prospect(prospect_id=ProspectId("p-2"))
        repo = _repo(prospects=[first, second])
        assert list(repo.list_prospects()) == [first, second]
        assert repo.get_prospect(ProspectId("p-2")) == second
        assert repo.get_prospect(ProspectId("missing")) is None

    def test_update_persists_new_state(self):
        repo = _repo(prospects=[make_prospect()])
        repo.update_prospects([replace(make_prospect(), status=ProspectStatus.ACTIVE, stage="s1")])
        stored = repo.get_prospect(PROSPECT)
        assert stored is not None
        assert stored.status is ProspectStatus.ACTIVE and stored.stage == "s1"

    def test_update_of_an_unknown_prospect_raises_and_writes_nothing(self):
        repo = _repo(prospects=[make_prospect()])
        changed = replace(make_prospect(), status=ProspectStatus.CLOSED)
        unknown = make_prospect(prospect_id=ProspectId("ghost"))
        with pytest.raises(KeyError):
            repo.update_prospects([changed, unknown])
        assert repo.get_prospect(PROSPECT) == make_prospect()

    def test_seeding_a_duplicate_is_rejected(self):
        with pytest.raises(ValueError, match="already exists"):
            _repo(prospects=[make_prospect(), make_prospect()])

    def test_other_clients_prospects_are_rejected(self):
        foreign = make_prospect(client_id=OTHER_CLIENT)
        with pytest.raises(ValueError, match="client-b"):
            _repo(prospects=[foreign])
        repo = _repo(prospects=[make_prospect()])
        with pytest.raises(ValueError, match="client-b"):
            repo.update_prospects([replace(foreign, prospect_id=PROSPECT)])


class TestActions:
    def test_save_inserts_and_updates_by_action_id(self):
        repo = _repo()
        repo.save_actions([make_action()])
        repo.save_actions([replace(make_action(), status=ActionStatus.APPROVED)])
        actions = repo.list_actions()
        assert len(actions) == 1 and actions[0].status is ActionStatus.APPROVED

    def test_list_filters_by_status(self):
        repo = _repo(
            actions=[
                make_action(action_id=ActionId("a-1"), idempotency_key="k1"),
                make_action(
                    action_id=ActionId("a-2"), idempotency_key="k2", status=ActionStatus.SENT
                ),
            ]
        )
        assert [a.action_id for a in repo.list_actions({ActionStatus.SENT})] == ["a-2"]
        assert len(repo.list_actions({ActionStatus.SENT, ActionStatus.PLANNED})) == 2
        assert len(repo.list_actions()) == 2
        assert repo.list_actions(set()) == []

    def test_find_by_idempotency_key(self):
        repo = _repo(actions=[make_action(idempotency_key="k1")])
        assert repo.find_action_by_idempotency_key("k1") is not None
        assert repo.find_action_by_idempotency_key("nope") is None

    def test_a_second_action_with_the_same_key_is_rejected_atomically(self):
        repo = _repo(actions=[make_action(idempotency_key="k1")])
        fresh = make_action(action_id=ActionId("a-2"), idempotency_key="k2")
        clash = make_action(action_id=ActionId("a-3"), idempotency_key="k1")
        with pytest.raises(ValueError, match="already in use"):
            repo.save_actions([fresh, clash])
        assert [a.action_id for a in repo.list_actions()] == ["a-1"]

    def test_duplicate_keys_inside_one_batch_are_rejected(self):
        repo = _repo()
        batch = [
            make_action(action_id=ActionId("a-1"), idempotency_key="k"),
            make_action(action_id=ActionId("a-2"), idempotency_key="k"),
        ]
        with pytest.raises(ValueError, match="already in use"):
            repo.save_actions(batch)
        assert repo.list_actions() == []

    def test_other_clients_actions_are_rejected(self):
        with pytest.raises(ValueError, match="client-b"):
            _repo().save_actions([make_action(client_id=OTHER_CLIENT)])


class TestConversationHistory:
    def test_no_history_gives_an_empty_conversation_with_a_stable_id(self):
        repo = _repo()
        conversation = repo.get_conversation(PROSPECT)
        assert conversation.messages == ()
        assert conversation.client_id == CLIENT and conversation.prospect_id == PROSPECT
        assert repo.get_conversation(PROSPECT).conversation_id == conversation.conversation_id

    def test_uses_the_prospects_conversation_reference_when_set(self):
        repo = _repo(prospects=[make_prospect(conversation_id=ConversationId("conv-x"))])
        assert repo.get_conversation(PROSPECT).conversation_id == "conv-x"

    def test_append_is_ordered_and_scoped_per_prospect(self):
        p2 = ProspectId("p-2")
        m1 = make_message(message_id="m-1")
        m2 = make_message(message_id="m-2", prospect_id=p2, conversation_id="c-2")
        m3 = make_message(message_id="m-3")
        repo = _repo()
        repo.append_messages([m1, m2])
        repo.append_messages([m3])
        assert repo.get_conversation(PROSPECT).messages == (m1, m3)
        assert repo.get_conversation(p2).messages == (m2,)
        assert repo.get_conversation(PROSPECT).conversation_id == CONVERSATION
        assert repo.messages == (m1, m2, m3)

    def test_duplicate_message_ids_are_rejected_atomically(self):
        repo = _repo(messages=[make_message(message_id="m-1")])
        with pytest.raises(ValueError, match="already exists"):
            repo.append_messages([make_message(message_id="m-2"), make_message(message_id="m-1")])
        assert len(repo.messages) == 1

    def test_a_prospect_keeps_one_conversation_id(self):
        repo = _repo(messages=[make_message(message_id="m-1")])
        with pytest.raises(ValueError, match="uses"):
            repo.append_messages([make_message(message_id="m-2", conversation_id="other")])

    def test_message_must_match_the_prospects_conversation_reference(self):
        repo = _repo(prospects=[make_prospect(conversation_id=ConversationId("conv-x"))])
        with pytest.raises(ValueError, match="conv-x"):
            repo.append_messages([make_message()])

    def test_other_clients_messages_are_rejected(self):
        with pytest.raises(ValueError, match="client-b"):
            _repo().append_messages([make_message(client_id=OTHER_CLIENT)])


class TestAudit:
    def test_events_are_appended_in_order(self):
        repo = _repo()
        first = _event()
        second = replace(first, event_type="x.z")
        repo.append_audit_events([first])
        repo.append_audit_events([second])
        assert repo.audit_events == (first, second)

    def test_other_clients_events_are_rejected_atomically(self):
        repo = _repo()
        with pytest.raises(ValueError, match="client-b"):
            repo.append_audit_events([_event(), _event(OTHER_CLIENT)])
        assert repo.audit_events == ()


class TestFailureInjection:
    def test_next_call_raises_then_recovers(self):
        repo = _repo(prospects=[make_prospect()])
        repo.fail_next("list_prospects")
        with pytest.raises(RuntimeError, match="list_prospects"):
            repo.list_prospects()
        assert len(repo.list_prospects()) == 1

    def test_custom_error_and_repeat_count(self):
        repo = _repo()
        repo.fail_next("save_actions", ConnectionError("quota"), times=2)
        for _ in range(2):
            with pytest.raises(ConnectionError):
                repo.save_actions([make_action()])
        repo.save_actions([make_action()])
        assert len(repo.list_actions()) == 1

    def test_failed_write_leaves_state_untouched(self):
        repo = _repo()
        repo.fail_next("append_messages")
        with pytest.raises(RuntimeError):
            repo.append_messages([make_message()])
        assert repo.messages == ()

    def test_unknown_method_names_are_rejected(self):
        with pytest.raises(ValueError, match="unknown"):
            _repo().fail_next("drop_table")
