# Real-integration test procedures (to run later, by a human, with real accounts)

Nothing in this document has been executed. Every automated test in the repository runs on doubles
(fake HTTP transports, fake mail probes, SQLite): none proves that a real Google, HubSpot, OVH, Telegram
or Meta account works. These procedures are what a person runs, once, with real accounts, before a client
relies on an integration. Record the date, the person, the build and the result of each step.

Conventions
- Use a dedicated test client (never a paying client) and a dedicated test account at each provider.
- Never paste a token, a password or a code into a ticket, a chat or a log. Screenshots hide them.
- A step passes only if the *observable result* described is seen. "No error" is not a pass.
- The acceptance criteria referred to as "section 15.3" in the brief are not written in this repository;
  the list below (C1 to C8) is the working version and should be reconciled with the brief.

## Acceptance criteria used below

- C1 Connection: the client reaches the connected state with no technical input (no token, host, port).
- C2 Read: real data from the provider reaches Revansa and is shown correctly.
- C3 Write: the intended write (message, cell, property) appears at the provider, once.
- C4 Idempotence: repeating the action (retry, resume, restart) never duplicates the write.
- C5 Revocation: revoking access at the provider is detected, shown with an action, and nothing more is
  sent or written until the client reconnects.
- C6 Isolation: a second test client never sees or touches the first client's data or credentials.
- C7 Secrecy: no token, password, code or contact appears in logs, API answers or the audit trail.
- C8 Recovery: after a provider outage or a restart, work resumes by itself or by the Admin's single
  documented action, without loss or duplicate.

Before any of them: the deployment runs on PostgreSQL (see "Database" at the end), the public base URL is
HTTPS, and the Admin has set the platform credentials for the provider under Settings.

## 1. Google Sheets (client's own authorisation)

1. Client: Sources, connect Google Sheets. Google's consent page opens; accept. Expect: connected, the
   client's Google address shown to that client only (C1).
2. Create a spreadsheet with the Revansa columns and 3 prospects; the client selects it. Run the
   connection test. Expect the 3 rows listed with the right statuses (C2).
3. Enable write-back with column names; let one prospect change status in Revansa. Expect exactly one
   cell updated, the others untouched (C3). Run the synchronisation again: no second write (C4).
4. Edit that cell by hand in the sheet, then sync. Expect Revansa to leave the human value (conflict) (C4).
5. In the Google account security page, remove Revansa's access. Within the next sync Revansa shows
   "reconnect" to the client and the Admin; no write happens (C5). Reconnect: sync resumes (C8).
6. With a second client connected to its own sheet, confirm neither client's sync touches the other's sheet (C6).
7. Search the application log and the credential audit for the refresh token and the sheet id: absent (C7).

## 2. HubSpot (client's own authorisation, Admin's status mapping)

1. Admin: Settings, HubSpot application credentials. Client: connect HubSpot (consent page). Expect the
   connected state (C1).
2. Admin: client page, Sources, HubSpot. Set the status property and map each Revansa status to the real
   HubSpot values; press "Check with the server": expect acceptance. Save. Try a mapping with no value for
   `new`: expect the server to refuse (the client has no route to this screen: confirm from the client UI).
3. Read: 5 contacts with different lead statuses. Expect unmapped or blank statuses left out and reported (C2).
4. Enable write-back of the status; close one contact in Revansa. Expect the HubSpot property changed once (C3).
5. Make the HubSpot property read-only or remove the write scope, trigger a write. Expect the Admin screen
   to show the failure with its action (check the application scopes), `waiting`/`failed` counts, and no write (C5).
6. Restore the scope, have the client reconnect if the screen says so, press "Resume synchronisation".
   Expect the contact written once; press it again: nothing more is written (C4, C8).
7. Revoke the app in HubSpot (Connected apps). Expect `reauth_required`, the guided steps on the Admin
   screen, the client told to reconnect, nothing read or written meanwhile (C5).
8. Run two Revansa processes against the same database (see "Two processes"): start a HubSpot consent on
   one, finish it on the other: expect success once, and a second use of the same link refused (C1, C8).
9. Logs and audit contain no token or contact (C7). A second test client is untouched (C6).

## 3. Gmail (client's own Google mailbox)

1. Client: choose Gmail, connect. Expect the connected state with the client's own address (C1).
2. Admin sets the daily send limit; send one first message to a mailbox you control. Expect it in that
   mailbox, from the client's Gmail, once (C3).
3. Reply from that mailbox. Expect the reply read and attached to the prospect (C2).
4. Revoke access in the Google account. Expect `reauth_required`, sending stopped, a clear client message (C5).
5. Reconnect: sending resumes; replies received during the gap are read once (C4, C8).
6. Another client's Gmail never appears in this client's status or inbox (C6). No token in logs (C7).

## 4. OVH mailbox (independent mailbox, Mode B)

1. Admin creates the mailbox at OVH by hand. Settings: platform servers (never shown to clients). Client
   asks for activation; Admin starts, then activates with the address and password. Expect both logins
   really tried and accepted before anything is stored (C1).
2. Client: open the mailbox card. Expect status "active" and the address. Expect no login or password unless
   the Admin enabled communication; then expect the login, and the password only after pressing "Show", for
   about a minute, at most 3 times an hour (C7). Try a fourth: expect the limit message.
3. Send a first message to a mailbox you control: arrives once, from the OVH address (C3). Reply: read once (C2).
4. Change the mailbox password at OVH. Expect the next login check to fail and the card to read "needs
   attention" (or the Admin's mailbox state to read not authenticated); no message sent (C5). The Admin
   enters the new password via activation: sending resumes (C8).
5. End the mailbox (deactivate): the client loses the login immediately; revealing is refused (C5).
6. Confirm no SMTP/IMAP host, port or security mode appears in any client screen or answer (C7), and that a
   second client cannot read the first one's mailbox (C6).

## 5. Telegram (client's own account, MTProto)

1. Client: connect the Telegram account with its phone, the code and (if set) the 2FA password. Expect
   connected, with the consent gate shown before any first message (C1).
2. Send a message to a person who agreed (your own second account); it appears once (C3). Reply is read (C2).
3. Terminate the session in Telegram (Devices). Expect `reauth_required` and sending stopped (C5); sign in again (C8).
4. Provoke a limit (do not spam real people): confirm the "limited" state blocks first messages (C5).
5. Another client's session is never used (C6). No code, password or session string in logs (C7).

## 6. WhatsApp (Embedded Signup)

1. Client enters only the number, then completes Meta's Embedded Signup. Expect connected, server-side code
   exchange, no token in the browser (inspect network and storage) (C1, C7).
2. Send an approved template to your own number: delivered once; delivery statuses update (sent, delivered,
   read) (C3). Reply: the 24-hour window opens; free text is accepted inside it and refused outside it (C2).
3. Submit a template; confirm pending then approved or rejected statuses follow Meta's review (C2).
4. Remove the app from the Meta business settings. Expect `reauth_required` and sending stopped (C5); reconnect (C8).
5. Start the signup on one process, complete it on another: success once; reuse of the same state refused.
6. Another client's WhatsApp account is never touched (C6).

## Two processes (resilience, section 12.5)

Needs PostgreSQL and the 0023 migration. Start two processes on the same database, each with its own
`SALES_AGENT_LOCK_FILE` (the default single lock file refuses a second process; there is no other switch,
so giving each process its own lock path is the deliberate step).

1. Start a consent (any provider) on process A, finish it on process B: success once; a second callback with
   the same state is refused.
2. With a client due for a job, watch both logs: only one process logs the job as started; the other logs
   "held elsewhere". Kill the holder mid-job: the other takes over after the lease time (30 minutes) without
   a duplicate send (C4, C8).

## Database

Run the suite against PostgreSQL with `SALES_AGENT_TEST_DATABASE_URL` pointing at a disposable, migrated
database (the concurrency test `test_two_writers_over_one_database_never_repeat_or_skip_a_number` then uses
it). Review: JSON columns use JSONB on PostgreSQL; sequences are application-numbered under a primary key
that refuses duplicates (no database sequence is relied on); timestamps are timezone-aware; the append-only
consent table has a PostgreSQL trigger (migration 0017). None of this was run against a live PostgreSQL in
this change.
