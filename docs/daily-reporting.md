# Daily reporting

Each client can receive one report a day about what its agent did. The report is built from data that
is stored, kept whatever happens to its delivery, and sent through a channel that is not the
prospecting channel's recipient.

## What a report says

Read through the client's own Engine (read-only), the send-cap counter and the agent's health:

- the period: the 24 hours that end at the client's chosen time of day, in the client's chosen IANA
  zone (wall clock, so a day with a clock change is 23 or 25 hours long);
- prospects: total and by status; follow-ups scheduled, and how many are due within 24 hours of the
  report;
- activity in the period: messages sent, prospects reached, new contacts (the first message of a
  conversation), follow-ups (our message right after our own message), answers (our message right
  after the prospect's), replies received;
- the e-mail quota left for the UTC day (`not_configured` or `unavailable` when it cannot be read,
  never a made-up number);
- the agent's health as counters only (failures of the daily cycle, of the mailbox, of the
  write-back); never the text of an error;
- actions required of the client: prospects waiting for a person (paused), send cap not set or
  reached, agent degraded, mailbox or write-back failing.

**Not tracked, said as such.** The platform does not record purchase intentions, appointments or
conversions: the report carries `{"tracked": false}` for each and says so in words. Nothing in a
report is presented as a confirmed sale. `sales_core.kpi.prospects_contacted` counts all-time SENT actions
and actions carry no sent time, so a per-period figure cannot come from it: the report counts stored
messages by timestamp instead (it does not recount that all-time KPI, which stays the dashboard's).

## One report per client and day

`daily_reports` has the primary key (client, day): the database refuses a second report for the
same day, so a restart or a second process cannot create or send a duplicate. A report is made once
and never rewritten with newer figures. It is made even when it cannot be delivered.

## Settings (per client, persisted)

`client_report_settings`: on/off, channel (`email` or `telegram`), the client's own recipient (an
address, or a Telegram chat number), time `HH:MM`, an explicit IANA time zone (validated, never
guessed from the browser or the server), language (`en`, `fr`, `ar`). WhatsApp is refused as
unsupported. The channel must also be open to the account by the Admin (channel policy): by default
only WhatsApp is, so the Admin has to open Email or Telegram for the report to be sendable.

## Delivery and its honest states

| status | meaning |
|---|---|
| `pending` | made, not sent yet |
| `sending` | claimed by one sender (a conditional UPDATE) |
| `sent` | the channel accepted it |
| `failed` | not delivered; retried with backoff (60 s doubling to 1 h, 6 attempts) unless the outcome is unknown |
| `undeliverable` | no valid setting, channel not authorized, or a permanent refusal |

- No usable setting: the report is kept as `undeliverable` (`report_settings_missing` /
  `report_disabled`) and is sent by itself once the settings are fixed.
- Channel down or not buildable: `failed`, retried; never marked sent.
- An e-mail report is an outgoing e-mail: it goes through `SendCapChannel` and takes one place in the
  client's daily cap. No cap, cap reached, or no guard wired: nothing is sent (`failed` with the
  cap's code; a refusal does not count as an attempt, and a reached cap is retried after UTC
  midnight). Telegram does not use the e-mail cap.
- If the channel raises, or the process dies while `sending`, the outcome is unknown: the report is
  `failed` with `send_unconfirmed` and is **not** retried by itself (that could send twice). Only the
  client's "send again" retries it.
- Only a short code is stored as the error, never what a server answered. The recipient is stored
  masked (`o***@example.com`).
- The e-mail channel has a fixed subject (its setting); the report's title is its first line.

## Scheduler

`Job.REPORT` in `sales_app/scheduler.py`: looked at every 5 minutes per client, on its own clock and
backoff (like `Job.SYNC`): it never waits for, and never delays, the jobs that send, and a failure
does not paint the client as degraded. `due()` is true when the newest report day has no report yet
or a recent one (last 3 days) can be retried. A client whose agent is paused or disabled gets no
report.

## Client API (`/client/v1`, session cookie only, `Cache-Control: no-store`)

- `GET /reporting`: settings (null until saved), channels (`available`, with `not_supported` or
  `not_authorized`), languages, newest report;
- `PUT /reporting/settings`: 422 with a code (`unsupported_channel`, `invalid_timezone`,
  `invalid_time`, `invalid_recipient`, `invalid_language`, `unknown_channel`), 403 for a channel not
  open to the account;
- `GET /reports?limit=`, `GET /reports/{day}`: the reports and their figures and text (404 for a day
  without a report, and for another client's: the same words);
- `POST /reports/generate`: makes the newest due report if missing (never a second) and tries to send
  it; `POST /reports/{day}/resend`: 409 if already sent or being sent.

No Admin route: the Admin's levers are the channel policy and the send cap.

## Frontend

Page `/reporting` ("Reporting"/"التقارير") in the client app: settings form (the time zone is typed,
with the browser's zone list only as suggestions), the list of reports with their real status and the
reason when not delivered, preview, "Generate now", "Send again". English and Arabic (no French
catalog exists in the app).

## Deployment

Migration `0013_daily_reports` (after `0012`) creates `client_report_settings` and `daily_reports`.
Without a database there is no report service: the routes answer 503 and the scheduler skips the job.

## Limits

- The figures are only as good as the stored messages: no timestamp is kept for the moment an action
  was sent, so activity is counted from messages, and a prospect's opt-out has no date.
- Health counters are in memory (since the last restart); a restart resets them.
- Purchase intentions, appointments and conversions are not tracked.
- The prospect source's own failures (Sheets/HubSpot) are visible only through the health counters.
- Production channels for a report are built from the client's configured providers (same
  credentials as prospecting); a client with no e-mail or Telegram configuration gets `failed`
  (`channel_unavailable`) until it has one. Telegram reports need the client's own chat to have
  been opened with the bot.
- Real delivery over SMTP/Telegram was not exercised: tests use fake channels only.
