# Commercial journey: automated end-to-end test

`tests/app/test_commercial_journey_e2e.py` (with its harness `tests/app/commercial_support.py`) plays
the whole commercial story of two clients, alpha and beta, on one deployment. It is automatic,
reproducible and needs no network, no account and no `.env`.

## Running it

```
uv run pytest tests/app/test_commercial_journey_e2e.py -q -rs
```

`-rs` prints the reasons of the skipped tests (the limits below). About 15 seconds. Each run builds
its own SQLite database (migrated with Alembic) in a temporary directory; nothing is shared between
runs and no real time is read: the clock is a `FakeClock` that only the test moves.

## The story

| Step | What is checked (on data, not on status codes) |
|------|-----------------------------------------------|
| 1. Admin | Model `gpt-5` assigned to alpha only (beta keeps its own); plan "Pro" created and assigned to both; daily e-mail cap of alpha stored; both clients start their agents through the Client API. |
| 2. Sources | Alpha: two Google Sheets, two circuits ("lanes") on two products (`a` and `b`). Sheet A: a contactable person, a person with no phone and no e-mail, a person who will answer. Sheet B: one person. Beta: one person on its own sheet. |
| 3. Proposals | Approval mode: three proposals for alpha (messages name the lane's product), the person with no coordinates is cancelled with `safety: no_contact`, nothing leaves yet. Beta sees only its own proposal and gets 404 trying to approve alpha's. |
| 4. Sending | After approval through `/client/v1/approvals`: exactly three messages captured on alpha's SMTP double (right recipient, From, login, text), none on beta's; time passing sends nothing twice. Operational store: statuses, lanes, planned follow-up, conversation. Write-back: last contact, next action and due date in the client's own columns, `paused` for the person who cannot be contacted. |
| 5. Replies | An answer and a `STOP` arrive through the IMAP double: messages stored then marked read; the Agent proposes an answer to the first person only; the second becomes `opted_out` in the store **and** in the sheet's status column; a follow-up is planned for the first. |
| 6. Daily report | Built from persisted data for the window 09:00 to 09:00 (Tunis): messages sent, replies, follow-ups, e-mail quota left, `purchase_intents / appointments / conversions` reported as "not tracked" and "nothing here is a confirmed sale"; no prospect address or name in the text; sent through the fake channel (alpha's SMTP double) and taking one place in the SQL counter. Asking again or letting the scheduler pass again sends nothing more. Beta cannot list, read or resend it. |
| 7. Cap reached | When the day's allowance is gone the report is refused (`send_cap_reached`), nothing leaves on the wire, no place is taken, the report is kept as `failed`, to be retried the next UTC day. |
| 8. Channel down | The mail server answers 451: the report stays `failed`, not sent, with the same figures; once the server is back it is delivered once (same text), the place is taken only then. |
| 9. Follow-ups | Monday: the follow-ups come due for the two people still reachable, not for the one who left nor the one with no coordinates. |
| 10. Restart | The application is closed and rebuilt over the same database, sheets and mailboxes: statuses, lanes, schedules, sheets' cells, reports, counters, the model assignment and the cap are identical; no prospect e-mail and no report is sent twice, even after days of ticks. |
| 11. Billing | January closed: invoices generated for alpha and beta (17 days of 31 at 31.00 = 17.00), generation is idempotent; a signed webhook with a wrong amount is rejected and traced, the right one marks the invoice `PAID` with origin `electronic`; replay is a no-op; marking it by hand is 409; unsigned is 401; beta's invoice of the same amount stays `PENDING`; an Admin scoped to beta sees only beta's invoice (alpha's is 404); a client has no billing route. |
| 12. Restart again | Invoices, plan and payment survive; the replay is still a no-op. |

Other tests of the file:

* `test_telegram_a_person_who_has_not_started_the_bot_is_never_contacted` (existing Telegram
  harness): four days with two people who never pressed Start: no action, no message, nobody paused;
  a chat id typed into the sheet proves nothing; the person who presses Start on their personal link
  is the only one contacted, after approval; the other client's bot is untouched; a restart repeats
  nothing.
* `test_the_send_cap_stops_the_real_mail_channel_and_the_count_survives_a_restart`: the real
  `MailChannel` wrapped by `SendCapChannel` over the real SQL counter and the cap stored by the
  Admin: two mails go, the third is refused and nothing reaches SMTP, the count survives a new
  engine, another client's allowance is its own, the next UTC day starts again.

## Real versus simulated

Real: the `Application` and its scheduler; a SQLite database built by the project's migrations
(accounts, sessions, versioned settings, operational store, write-back ledger, mailbox positions,
LLM usage, plans, invoices, reports, send-cap counter); the Admin, Client and payment-webhook HTTP
routes (TestClient); the Engine; the Brain package loading; the real Google Sheets source and
write-back; the real e-mail channel and inbox classes; the consent registry; the report service and
the HMAC-signed webhook.

Simulated: the Google Sheets API (`FakeSheetsApi`, in memory); SMTP and IMAP servers (doubles at the
socket's edge); the model provider (the test Brain never calls it); Telegram's Bot API
(`httpx.MockTransport`); the payment provider (the test signs with the real scheme and a test
secret); the clock. The `MailChannel` is built with the test clock (the production builder leaves it
to the system clock). Nothing here proves that a real Google, mail server, Telegram, OpenAI or
payment provider accepts or delivers anything, nor that PostgreSQL behaves like SQLite.

## Limits (explicit, not hidden)

Nothing is skipped any more. The two former skips are now assertions:

1. **Prospecting e-mail is under the daily cap.** The application puts `SendCapChannel` around the
   e-mail channel it builds for the Engine, so prospect mails, answers and follow-ups take places in
   the same counter as the report. The journey sets alpha's cap through the Admin API (4, with a plan
   quota of 200), sees the four mails of Thursday counted, the report take one place on Friday, and
   the follow-ups of Monday counted; `test_a_prospect_email_over_the_cap_is_deferred_never_lost`
   proves a mail is refused (nothing on the wire) with no cap and once the cap is reached, that the
   action stays approved with its reason and no attempt used, that it goes out the next UTC day, that
   another client's cap is its own, and that removing the cap stops sending again.
2. **The Admin sets the cap over HTTP** (`PUT/GET/DELETE /admin/v1/clients/{id}/send-cap`), tested in
   `test_the_admin_sets_reads_and_removes_the_send_cap_over_http` (validation, plan quota holding a
   larger cap, owner only, other perimeters, persistence across a restart).

Not covered: WhatsApp (no webhook in this journey), Gmail OAuth mailboxes, HubSpot, real
concurrency between processes, the front end (`web/`), real e-mail deliverability, per-call LLM
cost billing.
