"""The IMAP side of the e-mail adapter, against a double of the server.

What is proved: which messages become a prospect's reply and which are dropped (a mailbox's own,
automatic answers, empty ones), what text is kept, that a message is marked read only when it is
acknowledged (so a crash loses nothing), that its id is stable, and that a failure says nothing of
the login. What is not: that a real server's IMAP dialect matches this double.
"""

import imaplib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest

from sales_core.contracts import Channel
from sales_integrations.mail import (
    ImapMailbox,
    ImapSettings,
    MailAuthError,
    MailUnavailableError,
    parse_mail,
)

PASSWORD = "S3cret-imap-pass!"
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
SETTINGS = ImapSettings(
    host="imap.example.org",
    port=993,
    security="ssl",
    username="sales@example.org",
    password=PASSWORD,
    own_addresses=("sales@example.org",),
)


def raw_mail(
    *,
    sender="Alex <alex@example.com>",
    text="Yes, tell me more.",
    message_id="<abc.123@mail.example.com>",
    date="Thu, 15 Jan 2026 08:30:00 +0000",
    headers=None,
    html=None,
):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "sales@example.org"
    message["Subject"] = "Re: your enquiry"
    if message_id is not None:
        message["Message-ID"] = message_id
    if date is not None:
        message["Date"] = date
    for name, value in (headers or {}).items():
        message[name] = value
    if html is not None:
        message.set_content("fallback")
        message.add_alternative(html, subtype="html")
    else:
        message.set_content(text)
    return message.as_bytes()


class FakeImap:
    """A mailbox with unread and read messages, and the commands the adapter uses."""

    def __init__(self, messages=(), *, login_error=None, select_status="OK"):
        self.messages = {i + 1: {"raw": raw, "seen": False} for i, raw in enumerate(messages)}
        self.login_error = login_error
        self.select_status = select_status
        self.logged_in = False
        self.logged_out = False
        self.selected = None
        self.fetches: list[str] = []
        self.fail_on: str | None = None

    def login(self, user, password):
        if self.login_error:
            raise self.login_error
        self.logged_in = True

    def select(self, mailbox="INBOX", readonly=False):
        self.selected = mailbox
        return self.select_status, [b"1"]

    def uid(self, command, *args):
        if self.fail_on == command:
            raise OSError("connection reset")
        if command == "SEARCH":
            unread = [str(uid) for uid, m in sorted(self.messages.items()) if not m["seen"]]
            return "OK", [" ".join(unread).encode()]
        if command == "FETCH":
            uid = int(args[0])
            self.fetches.append(str(uid))
            if uid not in self.messages:
                return "OK", [None]
            raw = self.messages[uid]["raw"]
            return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        if command == "STORE":
            self.messages[int(args[0])]["seen"] = True
            return "OK", [b""]
        raise AssertionError(command)

    def logout(self):
        self.logged_out = True


class _Clock:
    def now(self):
        return NOW


def mailbox(server):
    return ImapMailbox(SETTINGS, factory=lambda settings: server, clock=_Clock())


# ---- what becomes a reply -----------------------------------------------------------------------


class TestWhatBecomesAReply:
    def test_a_plain_reply(self):
        message = parse_mail(raw_mail(), own_addresses=("sales@example.org",), now=NOW)
        assert message.channel is Channel.EMAIL
        assert message.sender == "alex@example.com"
        assert message.text == "Yes, tell me more."
        assert message.external_id == "abc.123@mail.example.com"
        assert message.timestamp == datetime(2026, 1, 15, 8, 30, tzinfo=UTC)

    def test_the_sender_is_the_address_in_lowercase_not_the_display_name(self):
        message = parse_mail(raw_mail(sender='"Alex Doe" <Alex.Doe@Example.COM>'), now=NOW)
        assert message.sender == "alex.doe@example.com"

    def test_what_was_quoted_is_not_part_of_the_reply(self):
        text = (
            "Sounds good, send the details.\n\n"
            "On Thu, 15 Jan 2026 at 09:00, Naga Sales <sales@example.org> wrote:\n"
            "> Hello Alex, we would like to follow up.\n"
            "> Best regards"
        )
        assert parse_mail(raw_mail(text=text), now=NOW).text == "Sounds good, send the details."

    def test_a_french_quote_marker_too(self):
        text = (
            "D'accord.\n\nLe 15 janv. 2026 à 09:00, Naga <sales@example.org> a écrit :\n> Bonjour"
        )
        assert parse_mail(raw_mail(text=text), now=NOW).text == "D'accord."

    def test_quoted_lines_alone_are_dropped(self):
        text = "> an old message\nMy answer\n> another quote"
        assert parse_mail(raw_mail(text=text), now=NOW).text == "My answer"

    def test_a_html_only_reply_is_turned_into_text(self):
        message = EmailMessage()
        message["From"] = "alex@example.com"
        message["Message-ID"] = "<h1@x>"
        message.set_content(
            "<p>Hello <b>there</b></p><style>p{color:red}</style><p>Bye</p>", subtype="html"
        )
        text = parse_mail(message.as_bytes(), now=NOW).text
        assert "Hello there" in text and "Bye" in text and "color" not in text and "<" not in text

    def test_a_very_long_text_is_cut(self):
        assert len(parse_mail(raw_mail(text="word " * 5000), now=NOW).text) <= 5000

    def test_a_date_in_the_future_is_not_believed(self):
        future = (NOW + timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        assert parse_mail(raw_mail(date=future), now=NOW).timestamp == NOW

    def test_a_missing_or_unreadable_date_is_now(self):
        assert parse_mail(raw_mail(date=None), now=NOW).timestamp == NOW
        assert parse_mail(raw_mail(date="not a date"), now=NOW).timestamp == NOW

    def test_with_no_message_id_the_id_comes_from_the_content_and_is_stable(self):
        raw = raw_mail(message_id=None)
        first, again = parse_mail(raw, now=NOW), parse_mail(raw, now=NOW)
        other = parse_mail(raw_mail(message_id=None, text="another"), now=NOW)
        assert first.external_id == again.external_id != other.external_id
        assert first.external_id.startswith("raw-")


class TestWhatIsNeverAReply:
    @pytest.mark.parametrize(
        "headers",
        [
            {"Auto-Submitted": "auto-replied"},
            {"Auto-Submitted": "auto-generated"},
            {"Precedence": "bulk"},
            {"Precedence": "junk"},
            {"X-Failed-Recipients": "someone@example.com"},
            {"List-Id": "<news.example.com>"},
        ],
    )
    def test_an_automatic_message(self, headers):
        assert parse_mail(raw_mail(headers=headers), now=NOW) is None

    @pytest.mark.parametrize(
        "sender",
        ["MAILER-DAEMON@example.com", "postmaster@example.com", "no-reply@example.com"],
    )
    def test_a_system_sender(self, sender):
        assert parse_mail(raw_mail(sender=sender), now=NOW) is None

    def test_a_message_the_mailbox_sent_itself(self):
        raw = raw_mail(sender="Naga Sales <Sales@Example.org>")
        assert parse_mail(raw, own_addresses=("sales@example.org",), now=NOW) is None

    def test_a_message_with_no_usable_sender(self):
        assert parse_mail(raw_mail(sender="undisclosed-recipients:;"), now=NOW) is None
        assert parse_mail(raw_mail(sender=""), now=NOW) is None

    def test_a_message_with_nothing_but_a_quote(self):
        assert parse_mail(raw_mail(text="> only a quote"), now=NOW) is None

    def test_bytes_that_are_not_a_message(self):
        assert parse_mail(b"\x00\x01 not mail at all", now=NOW) is None


# ---- the mailbox --------------------------------------------------------------------------------


class TestPolling:
    def test_the_unread_replies_are_returned_oldest_first(self):
        server = FakeImap(
            [
                raw_mail(message_id="<m1@x>", text="first"),
                raw_mail(message_id="<m2@x>", text="second"),
            ]
        )
        with mailbox(server) as box:
            fetched = box.fetch()
        assert [f.message.text for f in fetched] == ["first", "second"]
        assert [f.handle for f in fetched] == ["1", "2"]
        assert server.logged_in and server.selected == "INBOX" and server.logged_out

    def test_fetching_does_not_mark_anything_read(self):
        server = FakeImap([raw_mail()])
        with mailbox(server) as box:
            box.fetch()
        assert server.messages[1]["seen"] is False  # a crash now loses nothing

    def test_a_message_is_marked_read_only_when_acknowledged(self):
        server = FakeImap([raw_mail(message_id="<m1@x>"), raw_mail(message_id="<m2@x>")])
        with mailbox(server) as box:
            first, second = box.fetch()
            box.acknowledge(first.handle)
        assert server.messages[1]["seen"] is True and server.messages[2]["seen"] is False
        with mailbox(server) as box:
            assert [f.handle for f in box.fetch()] == [second.handle]

    def test_a_message_that_is_not_acknowledged_is_offered_again_with_the_same_id(self):
        server = FakeImap([raw_mail()])
        with mailbox(server) as box:
            (once,) = box.fetch()
        with mailbox(server) as box:
            (twice,) = box.fetch()
        assert once.message.external_id == twice.message.external_id

    def test_what_is_not_a_reply_is_marked_read_and_never_offered_again(self):
        server = FakeImap(
            [
                raw_mail(headers={"Auto-Submitted": "auto-replied"}),
                raw_mail(sender="sales@example.org", message_id="<own@x>"),
                raw_mail(message_id="<real@x>"),
            ]
        )
        with mailbox(server) as box:
            fetched = box.fetch()
        assert [f.message.external_id for f in fetched] == ["real@x"]
        assert server.messages[1]["seen"] and server.messages[2]["seen"]
        assert not server.messages[3]["seen"]

    def test_only_up_to_the_limit_is_taken_and_the_rest_waits(self):
        server = FakeImap([raw_mail(message_id=f"<m{i}@x>") for i in range(5)])
        with mailbox(server) as box:
            assert len(box.fetch(limit=2)) == 2
        assert server.fetches == ["1", "2"]

    def test_a_message_that_vanished_between_search_and_fetch_is_skipped(self):
        server = FakeImap([raw_mail(message_id="<m1@x>"), raw_mail(message_id="<m2@x>")])
        original = server.uid

        def uid(command, *args):
            if command == "FETCH" and args[0] == "1":
                return "OK", [None]
            return original(command, *args)

        server.uid = uid
        with mailbox(server) as box:
            assert [f.handle for f in box.fetch()] == ["2"]

    def test_an_empty_mailbox_is_an_empty_list(self):
        with mailbox(FakeImap()) as box:
            assert box.fetch() == []

    def test_the_folder_is_the_one_asked_for(self):
        server = FakeImap()
        settings = replace(SETTINGS, mailbox="Prospects")
        with ImapMailbox(settings, factory=lambda s: server, clock=_Clock()) as box:
            box.fetch()
        assert server.selected == "Prospects"


class TestFailures:
    def test_a_refused_login_is_an_auth_error_that_says_nothing_of_it(self):
        server = FakeImap(login_error=imaplib.IMAP4.error(f"AUTHENTICATIONFAILED {PASSWORD}"))
        with pytest.raises(MailAuthError) as raised, mailbox(server) as box:
            box.fetch()
        assert PASSWORD not in str(raised.value) and "example" not in str(raised.value)
        assert raised.value.__cause__ is None and server.logged_out

    def test_a_server_that_drops_the_connection_is_unavailable(self):
        server = FakeImap([raw_mail()])
        server.fail_on = "SEARCH"
        with pytest.raises(MailUnavailableError), mailbox(server) as box:
            box.fetch()

    def test_a_connection_error_while_logging_in_is_unavailable(self):
        server = FakeImap(login_error=OSError("timed out"))
        with pytest.raises(MailUnavailableError), mailbox(server) as box:
            box.fetch()

    def test_a_folder_that_cannot_be_opened_is_unavailable(self):
        with pytest.raises(MailUnavailableError), mailbox(FakeImap(select_status="NO")) as box:
            box.fetch()

    def test_acknowledging_on_a_dead_connection_is_unavailable(self):
        server = FakeImap([raw_mail()])
        with mailbox(server) as box:
            (item,) = box.fetch()
            server.fail_on = "STORE"
            with pytest.raises(MailUnavailableError):
                box.acknowledge(item.handle)

    def test_closing_twice_or_after_a_failure_is_harmless(self):
        box = mailbox(FakeImap())
        box.close()
        box.close()

    def test_the_password_is_in_no_repr(self):
        assert PASSWORD not in repr(SETTINGS) and PASSWORD not in repr(mailbox(FakeImap()))

    def test_a_fetched_message_prints_nothing_personal(self):
        server = FakeImap([raw_mail(text="my private words")])
        with mailbox(server) as box:
            (item,) = box.fetch()
        assert "private" not in repr(item) and "alex" not in repr(item)
