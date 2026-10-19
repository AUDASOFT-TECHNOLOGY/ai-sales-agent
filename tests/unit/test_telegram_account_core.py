"""Telegram from the client's own account, in the Core: who may be written to first.

A bot cannot write first; an account can, and Telegram treats unsolicited messages as spam. So the
Core writes first only to a prospect the client marked, in its own source column, as expecting the
contact; any other prospect waits until they write first, exactly as with the bot. Everything here is
simulated: no Telegram.
"""

from datetime import timedelta

from engine_harness import build, prospect
from factories import NOW

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    Channel,
    ContactInfo,
    Direction,
    Message,
    MessageId,
    ProspectId,
    ProspectStatus,
)
from sales_core.safety import BlockReason, check_contactable, has_stated_consent

ACCOUNT = "777001"
COLUMN = "telegram_consent"


def person(pid="p1", *, consent="yes", account=None, phone="+15550000001", **overrides):
    attributes = {COLUMN: consent} if consent is not None else {}
    return prospect(
        pid,
        **(
            {
                "contact": ContactInfo(phone=phone, telegram_chat_id=account),
                "attributes": attributes,
            }
            | overrides
        ),
    )


def harness(*prospects, consent_attribute=COLUMN, **kw):
    h = build(
        prospects=list(prospects),
        mode=ApprovalMode.AUTONOMOUS,
        telegram_consent_attribute=consent_attribute,
        **kw,
    )
    h.channel = type(h.channel)(h.repo, channel=Channel.TELEGRAM, clock=h.clock)
    h.engine = h.new_engine()
    return h


def wrote_first(h, pid, account=ACCOUNT):
    ident = ProspectId(pid)
    h.repo.append_messages(
        [
            Message(
                message_id=MessageId(f"in:telegram:{account}:1"),
                client_id=h.prospect(pid).client_id,
                prospect_id=ident,
                conversation_id=h.repo.get_conversation(ident).conversation_id,
                direction=Direction.INBOUND,
                channel=Channel.TELEGRAM,
                text="hello",
                timestamp=NOW,
                external_id=f"{account}:1",
            )
        ]
    )


class TestConsentWords:
    def test_only_a_clear_yes_is_consent(self):
        for word in ("yes", "YES", " oui ", "true", "1", "ok", "نعم"):
            assert has_stated_consent(person(consent=word), COLUMN), word
        for word in ("", "no", "non", "maybe", "0", "2024-05-01", "?"):
            assert not has_stated_consent(person(consent=word), COLUMN), word

    def test_no_column_no_consent(self):
        assert not has_stated_consent(person(consent=None), COLUMN)
        assert not has_stated_consent(person(consent="yes"), None)
        assert not has_stated_consent(person(consent="yes"), "other_column")


class TestFirstMessage:
    def test_a_consenting_prospect_whose_account_is_known_is_written_to_first(self):
        h = harness(person("p1", account=ACCOUNT))
        h.engine.run_cycle()
        (sent,) = h.channel.sent
        assert sent.recipient == ACCOUNT and sent.first_contact is True

    def test_without_consent_the_prospect_waits_even_with_an_account(self):
        h = harness(person("p1", consent=None, account=ACCOUNT))
        h.engine.run_cycle()
        assert h.channel.attempts == ()
        assert h.prospect("p1").status is not ProspectStatus.PAUSED
        assert [p.prospect_id for p in h.engine.prospects_awaiting_opt_in()] == ["p1"]

    def test_a_client_that_did_not_choose_its_account_never_writes_first(self):
        h = harness(person("p1", account=ACCOUNT), consent_attribute=None)
        h.engine.run_cycle()
        assert h.channel.attempts == ()

    def test_a_prospect_without_an_account_yet_waits_to_be_found(self):
        h = harness(person("p1"))
        h.engine.run_cycle()
        assert h.channel.attempts == ()
        verdict = check_contactable(h.prospect("p1"), Channel.TELEGRAM)
        assert verdict.reason is BlockReason.NOT_LINKED

    def test_a_reply_to_someone_who_wrote_first_is_not_a_first_contact(self):
        h = harness(person("p1", consent=None, account=ACCOUNT))
        wrote_first(h, "p1")
        h.engine.run_cycle()
        (sent,) = h.channel.sent
        assert sent.first_contact is False

    def test_consent_does_not_override_an_opt_out(self):
        h = harness(person("p1", account=ACCOUNT, status=ProspectStatus.OPTED_OUT))
        h.engine.run_cycle()
        assert h.channel.attempts == ()


class TestFindingTheAccount:
    def lookups(self, h, answers, limit=5):
        asked = []

        def resolve(phone, name):
            asked.append((phone, name))
            return answers.get(phone)

        return h.engine.link_consented_telegram(resolve, limit=limit), asked

    def test_the_account_of_a_consenting_prospect_is_found_and_kept(self):
        h = harness(person("p1"))
        linked, asked = self.lookups(h, {"+15550000001": ACCOUNT})
        assert linked == 1 and len(asked) == 1
        assert h.prospect("p1").contact.telegram_chat_id == ACCOUNT
        assert [e for e in h.events("prospect.linked")]

    def test_only_consenting_active_prospects_with_a_number_are_looked_up(self):
        h = harness(
            person("a", consent=None),
            person("b", status=ProspectStatus.PAUSED),
            person("c", phone=None),
            person("d", account="999"),
            person("e"),
        )
        _, asked = self.lookups(h, {})
        assert [phone for phone, _ in asked] == ["+15550000001"] and len(asked) == 1
        assert h.prospect("e").contact.telegram_chat_id is None

    def test_a_number_telegram_does_not_know_is_not_asked_again_for_a_day(self):
        h = harness(person("p1"))
        self.lookups(h, {})
        _, again = self.lookups(h, {})
        assert again == []
        h.clock.advance(timedelta(hours=25))
        _, later = self.lookups(h, {})
        assert len(later) == 1

    def test_the_lookups_per_call_are_limited(self):
        h = harness(*(person(f"p{i}", phone=f"+1555000{i:04d}") for i in range(8)))
        _, asked = self.lookups(h, {}, limit=3)
        assert len(asked) == 3

    def test_an_account_already_held_by_another_prospect_is_refused(self):
        h = harness(person("p1", account=ACCOUNT), person("p2", phone="+15550000002"))
        linked, _ = self.lookups(h, {"+15550000002": ACCOUNT})
        assert linked == 0 and h.prospect("p2").contact.telegram_chat_id is None

    def test_nobody_is_looked_up_when_the_client_did_not_choose_its_account(self):
        h = harness(person("p1"), consent_attribute=None)
        _, asked = self.lookups(h, {"+15550000001": ACCOUNT})
        assert asked == []


class TestKnownSenders:
    def test_a_linked_account_is_a_known_sender(self):
        h = harness(person("p1", account=ACCOUNT))
        assert h.engine.knows_inbound_sender(Channel.TELEGRAM, ACCOUNT)
        assert not h.engine.knows_inbound_sender(Channel.TELEGRAM, "123")

    def test_an_unlinked_account_is_known_by_the_number_telegram_reports(self):
        h = harness(person("p1", consent=None))
        assert h.engine.knows_inbound_sender(Channel.TELEGRAM, ACCOUNT, "+15550000001")
        assert not h.engine.knows_inbound_sender(Channel.TELEGRAM, ACCOUNT, "+15559999999")
        assert not h.engine.knows_inbound_sender(Channel.TELEGRAM, ACCOUNT)

    def test_the_number_does_not_make_an_email_sender_known(self):
        h = harness(person("p1"))
        assert not h.engine.knows_inbound_sender(
            Channel.EMAIL, "someone@example.com", "+15550000001"
        )
