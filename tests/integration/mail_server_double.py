"""A double of an IMAP server, with a journal of every command it was given.

It stands where `imaplib` would: the adapter is given it through the same factory. It speaks only
what the adapter uses (LOGIN, SELECT/EXAMINE, UID SEARCH/FETCH/STORE, the untagged UIDVALIDITY and
UIDNEXT, LOGOUT) and records everything, so a test can say what was *not* asked: no `STORE`, no
write-mode `SELECT`, no body of a stranger's message.

What it cannot do is prove that a real server behaves the same way.
"""

import re
from datetime import UTC, datetime
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as DEFAULT_POLICY

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def raw_mail(
    sender: str,
    text: str,
    *,
    message_id: str,
    subject: str = "Re: your enquiry",
    extra: dict[str, str] | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "sales@example.org"
    message["Subject"] = subject
    message["Message-ID"] = f"<{message_id}>"
    message["Date"] = "Thu, 15 Jan 2026 09:00:00 +0000"
    for name, value in (extra or {}).items():
        message[name] = value
    message.set_content(text)
    return message.as_bytes()


class ImapServerDouble:
    def __init__(self, *, uidvalidity: int = 7, announce_uidnext: bool = True) -> None:
        self.uidvalidity = uidvalidity
        self.announce_uidnext = announce_uidnext
        self.next_uid = 1
        self.messages: dict[int, dict] = {}
        self.log: list[tuple] = []  # ("SELECT", mailbox, readonly) / ("UID", command, *arguments)
        self.bodies_served: list[int] = []  # UIDs whose whole message was handed over
        self.headers_served: list[int] = []
        self.untagged: dict[str, list] = {}
        self.fail_fetch_of: int | None = None
        self.logins: list[tuple[str, str]] = []
        self.logouts = 0
        self.responses_asked: list[str] = []

    # ---- what happens in the mailbox ----------------------------------------------------------

    def deliver(self, raw: bytes, *, seen: bool = False, date: datetime = NOW) -> int:
        uid = self.next_uid
        self.next_uid += 1
        self.messages[uid] = {"raw": raw, "seen": seen, "date": date}
        return uid

    def renumber(self, uidvalidity: int) -> None:
        """The server rebuilt the folder: same messages, new numbers, new UIDVALIDITY."""
        old = [self.messages[uid] for uid in sorted(self.messages)]
        self.uidvalidity = uidvalidity
        self.messages = {i + 1: m for i, m in enumerate(old)}
        self.next_uid = len(old) + 1

    def seen_flags(self) -> dict[int, bool]:
        return {uid: m["seen"] for uid, m in self.messages.items()}

    def commands(self, name: str) -> list[tuple]:
        return [entry for entry in self.log if entry[0] == "UID" and entry[1] == name]

    # ---- the connection the adapter uses ------------------------------------------------------

    def login(self, user: str, password: str):
        self.logins.append((user, password))
        return "OK", [b"logged in"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False):
        self.log.append(("SELECT", mailbox, readonly))
        self.untagged = {"UIDVALIDITY": [str(self.uidvalidity).encode()]}
        if self.announce_uidnext:
            self.untagged["UIDNEXT"] = [str(self.next_uid).encode()]
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code: str):
        self.responses_asked.append(code)
        return code, self.untagged.pop(code, [None])

    def logout(self):
        self.logouts += 1
        return "BYE", [b"bye"]

    def uid(self, command: str, *args):
        self.log.append(("UID", command, *args))
        if command == "SEARCH":
            return "OK", [" ".join(str(u) for u in self._search(args[1:])).encode()]
        if command == "FETCH":
            return self._fetch(int(args[0]), args[1])
        if command == "STORE":
            self.messages[int(args[0])]["seen"] = True
            return "OK", [b""]
        raise AssertionError(f"unexpected command {command}")

    # ---- the commands -------------------------------------------------------------------------

    def _search(self, criteria: tuple) -> list[int]:
        uids = sorted(self.messages)
        name = criteria[0]
        if name == "UNSEEN":
            return [u for u in uids if not self.messages[u]["seen"]]
        if name == "ALL":
            return uids
        if name == "UID":
            low = int(str(criteria[1]).split(":")[0])
            found = [u for u in uids if u >= low]
            # RFC 3501: `n:*` always includes the last message, even when its UID is before n.
            return found or uids[-1:]
        if name == "SINCE":
            day, month, year = str(criteria[1]).split("-")
            since = datetime(int(year), _MONTHS.index(month) + 1, int(day), tzinfo=UTC)
            return [u for u in uids if self.messages[u]["date"] >= since]
        raise AssertionError(f"unexpected search {criteria}")

    def _fetch(self, uid: int, spec: str):
        if self.fail_fetch_of == uid:
            raise OSError("connection reset")
        if uid not in self.messages:
            return "OK", [None]
        raw = self.messages[uid]["raw"]
        if spec == "(INTERNALDATE)":
            stamp = self.messages[uid]["date"].astimezone(UTC).strftime("%d-%b-%Y %H:%M:%S +0000")
            return "OK", [f'{uid} (UID {uid} INTERNALDATE "{stamp}")'.encode()]
        if "BODY.PEEK[HEADER.FIELDS" in spec:
            wanted = re.search(r"HEADER\.FIELDS \(([^)]*)\)", spec).group(1).split()
            headers = BytesParser(policy=DEFAULT_POLICY).parsebytes(raw, headersonly=True)
            lines = [f"{name}: {headers[name]}" for name in wanted if name in headers]
            payload = ("\r\n".join(lines) + "\r\n\r\n").encode()
            self.headers_served.append(uid)
        elif spec == "(BODY.PEEK[])":
            payload = raw
            self.bodies_served.append(uid)
        else:
            raise AssertionError(f"a fetch that is not a peek: {spec}")
        return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(payload)}}}".encode(), payload), b")"]


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
