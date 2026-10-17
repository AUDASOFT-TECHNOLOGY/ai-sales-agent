# The e-mail channel

A client talks to its prospects from **its own mailbox**: Revansa sends over SMTP and reads the
replies over IMAP. Nothing is shared between clients: servers, address and password are each
client's own. Which clients may use the channel at all is the Admin's decision.

## What a client has to provide

> **Modes (T1).** Everything below describes the `custom` mode, the historical one: a document with
> no `mode` is `custom` and behaves exactly as before. A second mode, `platform`, exists in
> configuration only; no client screen or route offers it yet, so a client cannot choose or change it.
>
> | | `custom` | `platform` |
> |---|---|---|
> | Servers | the client's settings (below) | the platform's, set by the Admin in Settings → Email servers (`email.platform_*`) |
> | Address and login | `username` / `from_address` | the mailbox assigned to the client (`email.platform_mailbox`, client scope, not secret) |
> | Password | `email.password` | `email.password` (the client's own, written by the Admin) |
> | Settings the document may hold | all of the table below | `mode`, `from_name`, `subject` only (anything technical is refused) |
>
> **One address, one client.** The assigned address is stored in lowercase and the credential store
> refuses it for a second client, atomically (the check and the write happen under one lock, so
> simultaneous attempts cannot both succeed). That guarantee holds **within one process**, which is
> how the application runs (an instance lock refuses a second one); it would not cover a second
> process writing the same secrets file. A client's `mailbox_key` is also compared at load and when
> a runtime is refreshed. Who assigns a mailbox, and how, is not built yet: today it is written
> with the Admin's credential routes (`/admin/v1/clients/{id}/integrations/email.platform_mailbox`
> and `…/email.password`).
>
> Not tested against any real server: the platform servers are checked by Settings → Email
> servers → "Test servers" (an encrypted connection, no login), and nothing has been sent through a
> platform mailbox.

Non-secret settings (`PUT /client/v1/channel`, body `{expected_version, channel: "email", email: {...}}`):

| Setting | Meaning | Default |
|---|---|---|
| `from_address` | the address messages are sent from (and never read as a prospect) | required |
| `from_name` | display name | none |
| `username` | the login of **both** servers (often the address) | required |
| `smtp_host`, `smtp_port`, `smtp_security` | sending server; port 25, 465, 587 or 2525; `starttls` or `ssl` | port 587, `starttls` |
| `imap_host`, `imap_port`, `imap_security` | mailbox server; port 143 or 993; `ssl` or `starttls` | port 993, `ssl` |
| `mailbox` | the folder read for replies | `INBOX` |
| `subject` | the subject of every message sent (the Core's draft has no subject) | `Following up on your enquiry` |

The **password** (`PUT /client/v1/channel/credentials`, body exactly `{"password": "..."}`) is used for
both servers. It is stored encrypted in the client's scope, write-only: no response, error, audit
event or log carries it. With Gmail or Microsoft 365 this must be an *app password* (basic
authentication with the account password is refused by those providers); OAuth is not supported.

`POST /client/v1/channel/test` logs in to both servers and sends and reads nothing.

Connections are always encrypted. A host must resolve only to public addresses and the ports are
limited to the ones above, so a client cannot aim Revansa at the platform's own network.

## What the Admin decides

- `GET/PUT /admin/v1/settings/channels` — the channels every client may use. Until it is set, only
  WhatsApp is allowed. Owner only to write.
- `GET/PUT /admin/v1/clients/{id}/channels` — `{added, removed}` for one client. A refusal wins.

This is judged **every time a client's configuration is resolved**, not only when it chooses: a channel
taken away afterwards makes the client not ready, so it takes no new work and its mailbox is no longer
read. WhatsApp/Meta credentials stay the Admin's; the Client API refuses any such field.

## How replies come back

> **Two ways to read (T2).** What follows describes `consume`, the historical way, for a mailbox that
> exists for Revansa alone. A second way, `observe`, is for a mailbox that belongs to a person and
> holds their own mail (the client's own address, once that can be connected). It is built and
> tested but **no configuration reaches it yet**: nothing sets `read_mode` to `observe`, so every
> mailbox is still read as described below.
>
> In `observe` mode: the folder is opened read-only and no `STORE` is ever sent, so nothing is marked
> read; "new" is a saved position (the UID of the first message not dealt with, and the UIDVALIDITY
> it belongs to) kept in the database (`op_mail_cursor`, migration 0010), per client and mailbox, and
> it only moves forward. The first read starts at the end of the mailbox: no history is touched. For
> each new message only the headers that say who wrote it are read (`BODY.PEEK[HEADER.FIELDS (...)]`,
> never the subject); the whole message is read only when the sender is already a prospect
> (`Engine.knows_inbound_sender`), so the text of anyone else's mail never leaves the server, never
> reaches the Engine and never appears in the audit trail (where the historical way records the text
> of a stranger's message). A message the Engine failed to store holds the position back and is
> offered again; the Engine de-duplicates by `Message-ID`. If the server renumbers the folder
> (new UIDVALIDITY), the reader starts again from the date of the last progress, a day earlier.
> Without a database the reader refuses to read, rather than lose a message or read it twice.
>
> Limits: tested against a double of the server, never a real one. A message the Engine can never
> store holds the position back at that message (as an unread one stays unread in `consume`). Whether
> a sender is a prospect is asked of the client's source for every new message from a stranger; a
> network failure in that lookup is reported as a mailbox read failure (safe, nothing is lost, but
> the sentence says "mailbox"). A server that does not announce its next UID is asked for the last
> one instead, so a message arriving during the very first read may be taken for history.

The scheduler runs an `inbox` job per client every 2 minutes (`INBOX_EVERY_MINUTES`), outside the
outbound window, before the cycle that answers. A message is marked read **only after the Engine has
stored it**; the Engine de-duplicates by `Message-ID`, so a crash anywhere re-offers it harmlessly.
A mailbox that fails backs off on its own (30 s doubling to 15 min) and never stops the sending.
Never read in a dry run. Not turned into a reply: the mailbox's own messages, automatic answers
(`Auto-Submitted`, `Precedence: bulk/list/junk`, list mail, bounces), messages with no text.
Quoted history is cut. A mailbox belongs to one client (same IMAP host and login for two clients is
refused).

## Limits

- Replies are not threaded (`In-Reply-To`/`References` are not sent: the Core's draft carries no reference
  to the message answered). Every message has the client's configured subject.
- Plain text only; attachments are ignored.
- Polling, so a reply is seen within about 2 minutes (more if the mailbox is failing).
- No readiness test is required to start (the source has one): the check is that the channel is
  authorized, the settings are complete and the password is stored.

## Tested by automated tests, and what is left to test for real

Automated (doubles at the socket's edge): `tests/integration/test_mail_channel.py`,
`test_mail_mailbox.py`, `tests/app/test_email_*.py`.

**Not tested against a real server** — to do by hand with a real mailbox:
1. Save a real mailbox's settings and an app password; `POST /client/v1/channel/test` must say `ok`.
2. Send to a real address you own from a Sheets prospect; check delivery, spam folder, `From`, subject.
3. Reply from that address; within ~2 minutes the reply must appear in the Messages tab and the
   message must be marked read in the mailbox.
4. Reply `STOP`; the prospect must become `opted_out` and nothing more is sent.
5. Take the channel away in Admin; no further message is sent or read.
6. Check STARTTLS and SSL ports against the provider, and that a wrong password gives `auth`.
