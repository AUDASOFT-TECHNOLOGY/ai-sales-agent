# The e-mail channel

A client talks to its prospects from **its own mailbox**: Revansa sends over SMTP and reads the
replies over IMAP. Nothing is shared between clients: servers, address and password are each
client's own. Which clients may use the channel at all is the Admin's decision.

## What a client has to provide

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
