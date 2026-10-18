# The e-mail channel

A client talks to its prospects from **its own mailbox**: Revansa sends over SMTP and reads the
replies over IMAP. Nothing is shared between clients: the address and the password are each
client's own. Which clients may use the channel at all is the Admin's decision.

## How a client gets Email (the `platform` mode)

A client that chooses Email gets a **mailbox Revansa provides** (`platform` mode). The client types
nothing technical: its API and screens take no server, port, login, address or password
(`PUT /client/v1/channel` accepts only `email: {from_name, subject}`; there is no client route to
store, test or remove a credential). `GET /client/v1/channel` answers `status` (`ready`,
`waiting_platform`, `action_required`) with a sentence that mentions nothing technical, plus
`email.mode` (`platform` or `custom`), `email.sender_name_confirmed` and `sender_name_proposal` (its
business name).

The steps, and who does each:

| # | Step | Who | Where |
|---|---|---|---|
| 1 | Servers of the mailboxes (SMTP/IMAP host, port, security) and a test login for each | Admin, once | Settings → Email servers |
| 2 | Both logins accepted (`Test SMTP login`, `Test IMAP login`; re-run after every save) | Admin | same; state `Ready` |
| 3 | **Email for new clients** switched on (needs state `Ready`, else 409) | Admin, once | same; `PUT /admin/v1/settings/integrations/email/new-clients` |
| 4 | Each client created afterwards is given Email access as its own exception (`+email`); the global list and existing clients are **not** changed | automatic, at creation | `POST /admin/v1/clients` |
| 4b | The client asks for activation (Sender Name confirmed first); the Admin starts it | Client, then Admin | Channel page; client page → Email activation |
| 5 | A **mailbox is assigned** to the client: its address and password (the *Activate* button does steps 5–6 together) | Admin, per client | client page → Email mailbox |
| 6 | Both logins of that mailbox accepted (tested right after saving) | Admin | same |
| 7 | The client **confirms its Sender Name** (the business name is only proposed; saving the name is the confirmation) | Client | Channel page |
| 8 | The agent can be started | Client | Agent page |

Until 3–7 are all true, the channel is not ready and the agent does not start; the client sees
"Action required" (name to confirm) or "waiting" (the rest) and what to do, never a technical reason.

**Mailboxes are not created by Revansa.** Revansa has no integration with the provider's API
(OVH today): nothing creates a mailbox or its password. A client that may use Email and has no
mailbox is listed in Settings → Email servers as *waiting for a mailbox*, and an administrator
creates the mailbox at the provider and assigns it in step 5. Automating step 5 needs the provider's
provisioning API and credentials, which are not available here.

**Eligibility.** A new client is eligible when step 3 is on. Plans do not carry channel entitlements
today, so the offer does not change who gets Email; per-plan access would need a plan capability.

> **One address, one client.** The assigned address is stored in lowercase and the credential store
> refuses it for a second client, atomically (the check and the write happen under one lock). That
> guarantee holds **within one process**, which is how the application runs (an instance lock
> refuses a second one). A client's `mailbox_key` is also compared at load and when a runtime is
> refreshed.

> **Where the proof lives.** "Ready" is the last accepted login of the client's own mailbox with its
> current server, address and password (`Application.mail_auth`, in memory): any change voids it, and
> after a restart the platform mailboxes are checked again in the background (login only). The
> Sender Name is not part of it, so changing the name never invalidates a login.

| | `custom` (historical) | `platform` |
|---|---|---|
| Servers | the client's settings (below) | the platform's, in Settings → Email servers |
| Address and login | `username` / `from_address` | the mailbox assigned to the client (`email.platform_mailbox`, client scope, not secret) |
| Password | `email.password` | `email.password` (the client's own, written by the Admin) |
| Sender name | `from_name`, the client's own field | `from_name`, confirmed by the client; until then the agent does not run |
| Settings the document may hold | all of the table below | `mode`, `from_name`, `subject` only (anything technical is refused) |
| Login proof required | no (as before) | yes |

A document with no `mode` is `custom` and behaves exactly as before: the Admin wires it, the client
can only change its wording.

Settings of a `custom` mailbox (written by the Admin; the client can change only `from_name` and `subject`):

| Setting | Meaning | Default |
|---|---|---|
| `from_address` | the address messages are sent from (and never read as a prospect) | required |
| `from_name` | display name | none |
| `username` | the login of **both** servers (often the address) | required |
| `smtp_host`, `smtp_port`, `smtp_security` | sending server; port 25, 465, 587 or 2525; `starttls` or `ssl` | port 587, `starttls` |
| `imap_host`, `imap_port`, `imap_security` | mailbox server; port 143 or 993; `ssl` or `starttls` | port 993, `ssl` |
| `mailbox` | the folder read for replies | `INBOX` |
| `subject` | the subject of every message sent (the Core's draft has no subject) | `Following up on your enquiry` |

The **password** is stored encrypted in the client's scope, write-only, by the Admin
(`PUT /admin/v1/clients/{id}/integrations/email.password`): no response, error, audit event or log
carries it. With Gmail or Microsoft 365 this must be an *app password*; OAuth is not supported.

Logins are checked by the Admin: `POST /admin/v1/clients/{id}/integrations/email/test` (a client's
mailbox), `POST /admin/v1/settings/integrations/email/test-smtp` and `…/test-imap` (the platform's test
login). They log in and send and read nothing. `…/email/test` on the settings route only reaches the
servers over an encrypted connection and never logs in. States: `GET …/email/status`.

Connections are always encrypted. A host must resolve only to public addresses and the ports are
limited to the ones above, so a client cannot aim Revansa at the platform's own network.

## The activation request (client asks, Admin answers by hand)

Revansa creates no mailbox and calls no provider API. A client on the `platform` mode **asks** for its
email to be activated and an administrator does the work at the provider, then records it.

| Status | Meaning | Who moves it |
|---|---|---|
| `pending` | asked, nobody on it yet | Client: `POST /client/v1/channel/email-activation` |
| `in_progress` | an administrator took it | Admin: `POST /admin/v1/clients/{id}/email-activation/start` |
| `rejected` | refused, with a reason the client reads (a deactivation ends here too) | Admin: `…/reject` or `…/deactivate`, reason required |
| `active` | mailbox assigned, both logins accepted | Admin: `…/activate` |

- **Asking** needs: Email allowed to this client by the Admin (the request is *not* that permission),
  the channel set to Email in `platform` mode, and a **confirmed Sender Name** (saved by the client).
  One open or active request at most (409); after `rejected` the client may ask again, which replaces
  the rejected record (the audit trail keeps the history). A `custom` (historical) mailbox has nothing
  to activate (409).
- **The Admin sees** (`GET /admin/v1/email-activations`, `?status=all` for every status): client, the
  Sender Name as it was confirmed (a display name, never taken for an address), date, status. In the
  Admin UI: Settings → Email servers → *Email activation requests*, and the client's page → *Email
  activation*.
- **Activating** (`…/activate` with `{address, password}`, owner only, request `in_progress`): checks
  Email is allowed, the channel is Email on the platform mode, a Sender Name is confirmed and the
  servers are set; **really logs in to SMTP and IMAP first** (no message sent or read); only if both
  are accepted are the address and the password stored (encrypted, write-only; the address is unique
  across clients) and the request made `active`. If a login fails the answer is 422 with a fixed
  sentence per protocol (`problems`), **nothing is stored** and the request stays `in_progress`. The
  password is read by hand from the body, so no error repeats it, and it is in no response.
- **Deactivating** (`…/deactivate`, reason required) removes the mailbox and its password from the
  client; it sees `Rejected` with the reason and the agent stops sending.
- **Sending is blocked** while a request exists and is not `active`: the loader reports `Email
  activation has not been approved yet`, so the client is not ready and its agent takes no work. A
  client that **never asked** is left as it was (a mailbox assigned directly through the integrations
  routes keeps working), which keeps existing clients unchanged.
- **Stored** in the credential store, client scope, key `email.activation` (a JSON record, not in the
  credential catalog, never resolved into a client's secrets), audited with the new status as its
  reason. No table and no migration.

## What the Admin decides

- `GET/PUT /admin/v1/settings/channels` — the channels every client may use. Until it is set, only
  WhatsApp is allowed. Owner only to write. Turning Email on here gives it to **every existing client
  at once**; to give it only to clients created from now on, use *Email for new clients* (above).
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

## The daily sending limit (cap)

Every e-mail a client sends is counted in **one counter** per client, channel and UTC day: first
contacts, answers, follow-ups and the daily report, in the `platform` (dedicated mailbox) mode and
for a Gmail mailbox alike. It is applied where the application builds a client's e-mail channel for
the Engine (`wiring/runtime.py`, `_metered`: `SendCapChannel` around the channel), not in each
adapter, so a new mode cannot forget it. The daily report builds its own channel and takes its place
in the same counter (`report_wiring.py`).

- **No cap, no e-mail.** There is no default and no "unlimited". The cap is set by an Admin
  (owner) per client: `PUT /admin/v1/clients/{id}/send-cap` with `{"daily_cap": n}`, `n` a whole number
  from 1 to 100000 (anything else is a 422), `GET` to read it, `DELETE` to remove it (the client then
  sends nothing). Any Admin who reaches the client may read; others get 404. The response gives the
  cap, the plan's quota, the **effective cap**, today's use and a status (`ok`, `not_configured`,
  `limit_reached`). Who set it and when are stored with it (`client_send_caps.updated_by/at`).
- **Effective cap = min(Admin cap, daily quota of the client's current plan).** A plan is *not*
  required to send (nothing in the application requires a client to have one; billing simply skips a
  client without a plan): a client on no plan is held by its Admin cap alone. A client on a plan can
  never send more than that plan's `daily_quota`, even with a larger cap; the plan's *present* quota
  applies (moving the client to another plan, or editing the quota, takes effect at once). If the plan
  cannot be read, the send is refused.
- **Refused** when the cap is missing or invalid, when it is reached, or when the database cannot be
  read. A deployment with no database has no cap store: the e-mail channel sends nothing.
- **Certain failure gives the place back; an ambiguous one does not.** If the channel *returns* a
  failure (host unreachable, login refused, recipient refused: nothing left), the place is released.
  If it *raises* (connection dropped after the handoff, timeout), the message may have left and the
  place stays used. Over-counting is safe, exceeding the cap is not.
- **What the Engine does with a refusal.** The send comes back as a transient failure marked
  `deferred`: the action stays `approved`, its `last_error` says why (`the daily send cap is reached`
  or `is not configured`), and the attempt is **not** counted, so a day of refusals does not burn
  its attempts. It is offered again at the next dispatch (once per dispatch: no tight loop) and goes
  out when a place is free (the next UTC day, or when the Admin raises the cap).
- **The client sees why**, without anything technical: `GET /client/v1/channel` carries
  `email.sending` (`ok`, `not_set`, `reached`, `unavailable`) shown on the channel page.
- The Admin's client page has a "Daily email limit" card (cap, plan quota, effective cap, sent today).

The Gmail adapter (`OAuthMailChannel`) keeps its own legacy guard as an option; when it is built
through the provider table it must be given `CapEnforcedByWiring` so a message is counted once.
(Gmail is not yet built through the provider table: the same wrapper will apply as soon as it is.)

## Limits

- Replies are not threaded (`In-Reply-To`/`References` are not sent: the Core's draft carries no reference
  to the message answered). Every message has the client's configured subject.
- Plain text only; attachments are ignored.
- Polling, so a reply is seen within about 2 minutes (more if the mailbox is failing).
- To start, a `platform` mailbox needs its login proven and a confirmed Sender Name; a `custom` one
  only needs the channel authorized, the settings complete and the password stored.

## Tested by automated tests, and what is left to test for real

Automated (doubles at the socket's edge): `tests/integration/test_mail_channel.py`,
`test_mail_mailbox.py`, `tests/app/test_email_*.py`, `test_admin_email_*.py`.

**Not tested against a real server** — to do by hand with a real mailbox, only with explicit approval:
1. Settings → Email servers: real hosts and ports, a real test login; `Test SMTP login` and
   `Test IMAP login` must be accepted (a wrong password must say the server refused the login).
2. Turn on *Email for new clients*; create a test client; check it may use Email.
3. Create the mailbox at the provider, assign it on the client's page; its test must be accepted.
4. As the client: choose Email, confirm the Sender Name, start the agent.
5. Send to a real address you own from a prospect; check delivery, spam folder, `From` (the confirmed
   name and the assigned address), subject.
6. Reply from that address; within ~2 minutes the reply must appear in the Messages tab and the
   message must be marked read in the mailbox.
7. Reply `STOP`; the prospect must become `opted_out` and nothing more is sent.
8. Take the channel away in Admin; no further message is sent or read.
9. Check that the provider accepts the assigned address as the sender.
