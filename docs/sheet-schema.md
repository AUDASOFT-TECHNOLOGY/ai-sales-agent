# Google Sheet schema

Status: **implemented** by `sales_integrations/google_sheets` (`GoogleSheetsRepository`). This is the
definitive layout of a client's spreadsheet, and the rules the adapter follows while people keep
editing it.

## 0. Principles

1. **One spreadsheet per client, five tabs.** `Prospects`, `Actions`, `Messages`, `Log`, `Facts`.
   Nothing is shared between clients.
2. **The sheet is live and is the only source of truth.** Rows are added, edited, sorted and deleted
   all day, whether or not the agent runs. There is no import step, no local database, no cache and
   no copy that could go stale: every repository call reads the sheet afresh.
3. **Columns are found by header text**, never by position, so a client may reorder columns, add its
   own, and even rename ours (`column_mapping`, section 12).
4. **Rows are found by id**, never by row number, so sorting and inserting rows is safe.
5. **Nothing a person typed is silently overwritten** (section 9.5), and **nothing the agent cannot
   understand is ever acted on** (section 9.5, "bad rows").

## 1. Setting up a client's spreadsheet

1. Create the spreadsheet in the client's Google account, in the client's language and country.
2. Set the spreadsheet's time zone (File, Settings) **to the same zone as `timezone` in
   `client.yaml`**. Dates that Sheets stores itself use the spreadsheet zone.
3. Share it, as *Editor*, with the platform's service account e-mail.
4. Create the tabs and columns. Either build them by hand from this document, or let the adapter do
   it: `GoogleSheetsRepository.initialize_layout()` creates missing tabs and appends missing columns
   **to the right** of the client's own columns without touching existing data. A spreadsheet set up
   before `Facts` existed needs nothing by hand: `verify()` only warns that the tab is missing, reading
   facts from it returns none, and the first recorded fact creates the tab (section 6a).
   `GoogleSheetsRepository.verify()` lists anything still missing.
5. Put the spreadsheet URL or id in `client.yaml` (`sheets.spreadsheet_id`); the id is extracted.
6. Recommended: format phone columns as *plain text*; add a data-validation drop-down on `status`;
   protect the system-managed columns and the `Messages` and `Log` tabs, allowing edits only by the
   service account (section 8).

## 2. Tabs

| Tab | Purpose | Rows created by | Rows changed by |
|---|---|---|---|
| `Prospects` | the client's prospects and where each one stands | **the client** (the agent fills in ids and state) | client and agent, cell by cell |
| `Actions` | one row per commercial action: proposed, approved, sent... | **the agent** | agent, and people approving or editing |
| `Messages` | every inbound and outbound message (append-only) | **the agent** | nobody |
| `Log` | audit trail of what the agent did and why (append-only) | **the agent** | nobody |
| `Facts` | what is known about each prospect: key/value observations (append-only) | **the agent** | nobody |

Tab titles are configurable (`sheets.prospects_tab` and so on). Keep **no blank rows** inside the
system tabs.

## 3. `Prospects` tab

The header row is row 1. A header is **required** if the agent writes that column: a missing
required column is reported (never guessed at) and the tab is not used until it exists.

| Column | Req. | Owner | Format | Meaning |
|---|---|---|---|---|
| `prospect_id` | yes | client, else agent | text, unique | stable identity. Blank on a new row: the agent assigns one (section 9.2) |
| `display_name` | yes | client | text | name shown to humans and passed to the Brain |
| `phone` | yes | client | any phone format | the WhatsApp number; compared by its digits. Blank means "not contactable" |
| `email` | no | client | text | contact e-mail |
| `preferred_channel` | no | client | `whatsapp`, `sms`, `email`, `telegram` | preferred channel |
| `locale` | no | client | text, e.g. `fr`, `ar-TN` | prospect language, passed to the Brain |
| `status` | yes | **shared** | `new`, `active`, `paused`, `opted_out`, `closed` | lifecycle; blank means `new` |
| `stage` | yes | **shared** | free text | the Brain's own funnel label; opaque to the Core |
| `next_action_type` | yes | **shared** | `follow_up`, `reminder`, `callback`, `first_contact` | what is due next |
| `next_action_due_at` | yes | **shared** | date and time | when it is due. Type and date must be set together |
| `conversation_id` | no | agent | text | link to the conversation; derived if blank |
| `telegram_chat_id` | no | agent | digits | the Telegram chat the prospect opened with the client's bot (see [telegram-channel.md](telegram-channel.md)). Written the day they start the bot; **added to the right of your columns if the sheet has none**; never edit it by hand |
| `last_contacted_at` | yes | agent | date and time | last outbound message |
| `last_inbound_at` | yes | agent | date and time | last inbound message |
| `follow_up_count` | yes | agent | whole number | follow-ups already sent |
| *any other column* | n/a | client | text | kept and shown to the Brain as an attribute, never modified |

**Shared** means both the client and the agent may write the column. Concurrent writes are detected
and never silently overwritten (section 9.5). A blank `status` is `new`: a prospect the agent has
not contacted.

## 4. `Actions` tab

One row per action. The agent creates every row; people **approve, reject and edit** them.

| Column | Owner | Format | Meaning |
|---|---|---|---|
| `action_id` | agent | text, unique | identity of the action |
| `prospect_id` | agent | text | the prospect it concerns |
| `action_type` | agent | `first_contact`, `follow_up`, `reminder`, `callback`, `reply` | why it exists |
| `due_at` | agent | date and time | when it was planned (its day counts toward the daily quota) |
| `priority` | agent | `low`, `normal`, `high`, `urgent` | ordering hint |
| `status` | **shared** | `planned`, `proposed`, `approved`, `rejected`, `sending`, `sent`, `completed`, `failed`, `cancelled` | where it stands |
| `idempotency_key` | agent | text, unique | the guard against doing the same thing twice |
| `attempts` | agent | whole number | failed tries so far |
| `last_error` | agent | text | the last problem, for humans |
| `draft_text` | **shared** | text | the message to send; **editable before approval** |
| `draft_language` | shared | text | language of the draft |
| `draft_template_id` | shared | text | approved WhatsApp template, when one is required |
| `draft_template_params` | shared | JSON object | template variables |
| `next_stage` | **shared** | text | the stage the prospect takes once this is sent |
| `next_action_type` | **shared** | as above | the next action once this is sent |
| `next_action_due_at` | **shared** | date and time | its due date |

Approval workflow: the agent writes a row as `proposed`. A person reads `draft_text`, optionally
edits it (and `next_stage`, `next_action_*`), then sets `status` to `approved` or `rejected`. The
agent sends approved rows, writes `sending` first and `sent` afterwards. Whatever is in the row at
that moment is what is sent and applied.

Allowed human status changes: `proposed` to `approved` or `rejected`; any unsent row to `cancelled`;
a row stuck in `sending` to `sent` or `failed` **after checking WhatsApp** (the agent never resends
it). **Never set a stuck `sending` row back to `approved`**: that would send it again.

Extra columns (for example a name look-up formula) are allowed and ignored.

## 5. `Messages` tab (append-only, system-managed)

| Column | Format | Meaning |
|---|---|---|
| `message_id` | text, unique | `out:<action_id>` for outbound, `in:<channel>:<provider id>` for inbound |
| `conversation_id` | text | one per prospect, stable |
| `prospect_id` | text | the prospect |
| `direction` | `inbound`, `outbound` | |
| `channel` | `whatsapp`, `sms`, `email` | |
| `timestamp` | date and time | |
| `text` | text | message body (empty for media) |
| `external_id` | text | the provider's message id |
| `metadata` | JSON object | for example `{"type": "text"}` |

## 6. `Log` tab (append-only, system-managed)

`timestamp`, `event_type` (for example `action.sent`, `safety.blocked`, `prospect.handoff`),
`actor` (`core`, `brain`, `core:autonomous`), `prospect_id`, `action_id`, `details` (JSON object).
This is where a person finds **why** something happened, including handoff and close reasons.

## 6a. `Facts` tab (append-only, system-managed)

One row per **observation** of a fact about a prospect. Facts are generic key/value pairs: which keys
exist is a Brain's business, so there is no column for any business concept. The tab is part of the
standard layout: it takes no configuration (its title is `Facts`; `column_mapping` accepts
`facts.<field>` like any tab).

| Column | Format | Meaning |
|---|---|---|
| `fact_id` | text, unique | identity of the observation: `fact-` plus 24 hex characters (below) |
| `client_id` | text | the client the row belongs to; rows of another client are ignored |
| `prospect_id` | text | the prospect |
| `fact_key`, `fact_value` | text | the Brain's key and the value |
| `fact_status` | `confirmed`, `discovered`, `hypothesis` | how sure we are |
| `confidence` | number 0 to 1, or blank | mostly for hypotheses |
| `evidence_source` | `prospect`, `client_data`, `conversation`, `system`, `human`, `brain_inference`, or blank | provenance; blank means no evidence |
| `evidence_message_id`, `evidence_excerpt`, `evidence_observed_at`, `evidence_recorded_by` | text / date and time | provenance details (need an `evidence_source`) |
| `recorded_at` | date and time, required | when the observation was recorded |
| `updated_at` | date and time, or blank | |

**Identity and retries.** `fact_id` is a hash of the client, prospect, key, value, status,
confidence, evidence and `recorded_at` (`sales_core.contracts.fact_identity`, the same in every
backend). It is not `prospect_id + key`: a key may take another value later and each observation is a
row, so `manager` then `director` are two rows and the newest `recorded_at` is the current one.
Recording the very same fact again (a retry) has the same identity and writes nothing, so a retry
after an append whose outcome is unknown is always safe. A retry must therefore carry the same
`recorded_at`: whoever records a fact stamps it once, before the first attempt. A fact without
`recorded_at` is refused.

**Reading** returns the prospect's observations oldest first (ties in row order), from the live tab.
A row that cannot be read (unknown status, no id, bad confidence, evidence without a source, a
repeated `fact_id`...) is excluded and reported in `problems` like any other tab; the rest load.

**Writing** appends only: rows are never edited or removed, existing rows are never rewritten, and
an identity found on the sheet (even in an unreadable row, or one of another client) is never
written again. The prospect must exist in this client's `Prospects` tab. A first write to a
spreadsheet without the tab, or with a blank one, creates the tab and its headers first.

## 7. Value formats

| Kind | Written by the agent | Accepted when a person types it |
|---|---|---|
| Date and time | `2026-01-20T09:00:00+01:00`, in the client's time zone | ISO with or without offset, `2026-01-20 09:00`, a date alone (midnight, local), a Sheets date cell |
| Phone | as received | any formatting; matched by digits only; `+216 12 345 678` = `21612345678` |
| Statuses, types, channels | lowercase words, as in the tables | any case, surrounding spaces ignored |
| Numbers | plain text digits | a number or numeric text |
| JSON columns | compact JSON object | not meant to be typed |
| Text | exactly as written | up to 49,000 characters per cell |

The agent writes **every value as literal text** (Sheets' `RAW` input): a message beginning with `=`
is stored as text, never evaluated, and a phone number is never turned into a number.

## 8. Who may edit what, and what must never be modified

| | Prospects | Actions | Messages | Log |
|---|---|---|---|---|
| **Client edits freely** | `display_name`, `phone`, `email`, `preferred_channel`, `locale`, own columns, new rows | `draft_*`, `next_*` (before sending) | none | none |
| **Client edits, agent also writes** | `status`, `stage`, `next_action_*` | `status` | none | none |
| **System only, never edit** | `conversation_id`, `last_contacted_at`, `last_inbound_at`, `follow_up_count` | `action_id`, `prospect_id`, `action_type`, `due_at`, `priority`, `idempotency_key`, `attempts`, `last_error` | everything | everything |
| **Never do** | change `prospect_id` of a known prospect; rename required headers without `column_mapping` | **delete or add rows**; edit `idempotency_key` | delete, edit or add rows | delete or edit rows |

Why the "never" items matter: deleting an `Actions` row erases the idempotency record that stops an
action being repeated; changing a `prospect_id` makes the agent see a different person and orphans
that person's history. Use Google's *protected ranges* to enforce the system-only items.

## 9. A live spreadsheet

### 9.1 How a newly added prospect is detected

There is no import and no notification: **every read of the `Prospects` tab reads the whole tab**.
A row a person adds (typically at the bottom) is part of the next read, in whatever order and at
whatever time it was added. The planner then treats it like any other prospect:

- a row with `status` `new` (or blank) and nothing scheduled is a **new prospect**, planned in the
  lowest-priority tier (after due follow-ups, reminders and callbacks);
- a row with `status` `active` and a due `next_action_*` is planned in that action's tier.

It is picked up by the **next planning pass**: the daily cycle, or the additional passes the
scheduler runs during the working day (`schedule.prepare_every_minutes`, default 60 minutes, between
`daily_at` and the end of the day, on working days). A prospect added at 11:00 is therefore eligible
by about noon, if the client's **daily quota** (default 20 outreach actions per local day, shared by
all passes) has room; otherwise it waits for the next day's quota. A row that cannot be understood
(section 9.5) is skipped and reported, and becomes eligible on the first read after it is fixed.

### 9.2 How stable `ProspectId` values are assigned and preserved

- `prospect_id` is the prospect's identity everywhere: in `Actions`, in `Messages`, and in the Core.
- **If the client supplies one** (their own CRM id, for instance) it is used as is.
- **If the cell is blank and the row has a name, phone or e-mail**, the agent assigns `p-` plus ten
  random hexadecimal characters and writes it into that cell (only when blank). This happens on the
  first read that sees the row, and is the only time the agent writes a client-owned column.
  Rows with data only in the client's own columns are ignored.
- The agent **never rewrites** an existing `prospect_id`. It is not derived from the row number, the
  name or the phone, so sorting, inserting, hiding rows, correcting a name or changing a phone number
  cannot change it. A restart forgets nothing, because the id lives in the sheet.
- The one thing that changes an identity is a person editing `prospect_id`. Don't: the agent would
  see a new prospect and the old id's history and actions would be orphaned.

### 9.3 How duplicate prospects are prevented

| Risk | Prevention |
|---|---|
| Reading the sheet repeatedly multiplies prospects | Reads create nothing. A prospect is its row; ids are stable (9.2) |
| The same `prospect_id` on two rows | **Both rows are excluded**, reported as a problem, and never contacted (the agent will not guess which is right) |
| The same person added twice with different ids | Rows sharing a phone number (compared by digits): **the first row is kept, later rows are excluded** and reported. An opt-out on the first row therefore also protects the person |
| Writing a change creates a new row | Prospect writes update the row found **by id**, cell by cell. The agent never appends to `Prospects` |
| The same action planned twice | `action_id` and `idempotency_key` are unique across **every** `Actions` row, including rows the agent cannot read; the Core's planner also allows one outreach per prospect per day |
| The same message stored twice | `message_id` is unique; appending checks the current tab first; repeated rows are ignored when read |

E-mail addresses are not de-duplicated in the MVP (WhatsApp is the channel).

### 9.4 Client-added rows versus system-managed rows

| Tab | Client adds rows? | Agent adds rows? |
|---|---|---|
| `Prospects` | **Yes: this is how prospects arrive.** Fill at least `display_name` and `phone`. Leave the system columns blank | **No, never.** It only fills a blank `prospect_id` and updates cells of existing rows |
| `Actions` | No. To schedule a contact yourself, set `next_action_type` and `next_action_due_at` on the prospect | Yes, only by planning. New rows are appended after the last row, so a row added a moment earlier is never overwritten |
| `Messages`, `Log` | No | Yes, append-only |

A hand-made row in `Actions` or `Messages` is read like any other if it is valid and unique, but it
is unsupported.

### 9.5 Rows modified manually by the client

**Change detection.** When the agent writes, it changes **only the cells whose value it is
changing**, after re-reading the row and checking that each of those cells still holds what the agent
last saw. If not, nothing at all is written and the operation fails with a conflict; the Engine
records it and decides again from fresh data. A person's edit to any *other* cell of the same row is
never touched.

| Situation | Result |
|---|---|
| A person rejects a proposal while the agent is about to send it | The agent's `sending` write is refused, **nothing is sent**, the rejection stands |
| A person types `opted_out` while a message is in flight | The message already sent stays sent; the agent's follow-up write to `status` is refused, `opted_out` **stands**; other bookkeeping is reported |
| A person edits `draft_text` before approving | The edited text is what is sent |
| A person changes `phone`, `display_name`, `locale` or their own columns | Seen on the next read; the history stays with the same `prospect_id` |
| A person sorts, filters, inserts or hides rows | Harmless: rows and columns are found by id and header |
| A person deletes a prospect row | The prospect disappears from reads. Pending actions for it are cancelled by the safety check (an unknown prospect is never contacted). Its history is kept; adding the same `prospect_id` back restores it |
| A person types an invalid value (`acitve`, a bad date, a type without a date) | The row is **excluded from every read and reported**, never guessed, so it is never contacted. It is used again on the first read after the fix |
| A person edits a system-only column | Not supported: the agent reads what is there. Don't |
| A person deletes an `Actions` row | Not supported: it erases the idempotency record |

**Bad rows** are reported through `GoogleSheetsRepository.problems` (row, tab, reason) and logged
once each as warnings. An `Actions` row with an unreadable status is excluded from lists **but its
idempotency key stays reserved**, and looking it up by key raises an error instead of answering "not
found" (so a duplicate can never be created around it).

### 9.6 History and actions stay consistent while prospects change

- Everything is keyed by **`prospect_id`**, never by row number, phone or name. `Messages` and
  `Actions` rows reference the id, so they follow the prospect through sorts, edits and renames.
- A prospect has **one `conversation_id`**, taken from its first message (or `conversation_id` on
  its row, or `conv-<prospect_id>` for a new one). Appending a message under a different
  conversation id is refused.
- Inbound messages are matched to the **current** phone in the sheet. If a number is changed, replies
  from the old number are "unknown sender" (stored in the audit log, not attached to anyone).
- Proposals carry their own next stage and next action, so an approved action applies what was
  proposed even if the prospect row was edited or the process restarted meanwhile.
- The final safety check reads the prospect **again just before sending**: a prospect paused, closed,
  opted out, deleted or left without a phone since the proposal is not contacted.
- If two rows turn out to be the same person, keep one and delete the other row only after its
  actions are finished; history stays under each id.

## 10. Guarantees and limits

**The adapter guarantees:** every read reflects the sheet at that moment; a write goes to the row
with that id; a change is applied only if the cell still holds what was read; a batch is validated
completely before its first write; idempotency keys and message ids stay unique; new system rows are
appended, never written to a guessed row number; unreadable data is excluded and reported.

**It does not, and cannot:**
- make several API calls one transaction. Between the final re-read and the write of one call there
  is a window of tens of milliseconds; a person inserting or sorting rows in exactly that window
  could misplace one write. The change-detection and the tests bound the damage; protected ranges
  and the single-writer rule remove most of the risk;
- stop someone with edit rights deleting rows or editing system columns (protect them);
- serve more than one agent process per spreadsheet (the application enforces one process).

The in-memory state the adapter keeps is only a **baseline for change detection**; it is never used
to answer a read and is dropped for rows that disappear.

## 11. API usage and retries

Google's default limit is about 60 read requests per minute per user. Calls per operation:

| Operation | Calls |
|---|---|
| list prospects / get one / find by phone | 1 read (+1 write only when ids are assigned) |
| update prospects | 1 read + 1 write (0 writes if nothing changed) |
| list actions / find by key | 1 read |
| save actions | 1 read + 1 append (new rows) + 1 write (changed cells) |
| get conversation | 1 read (+1 read if the prospect has no history yet) |
| append messages | 2 reads (+1 for a first message) + 1 append |
| append audit events | 1 read (header) + 1 append |
| get facts | 1 read (0 more if the tab does not exist yet) |
| append facts | 2 reads (prospects, facts) + 1 append; nothing more when every fact is already recorded (first write only: create the tab) |

Roughly ten calls per message sent; 20 a day is far inside the limit. Reads and explicit-range
writes are retried on 429, 5xx and network errors with backoff (1 s doubling to 30 s, 5 attempts).
An **append is retried only on 429**: a retry after a lost success would add the rows twice, and a
retried `append_messages` would in any case find the rows already present and refuse.

## 12. Column mapping

To keep its own headers, a client maps ours to theirs in `client.yaml`, with `<tab>.<field>` keys:

```yaml
sheets:
  column_mapping:
    prospects.display_name: Nom
    prospects.phone: Téléphone
    actions.status: État
```

Unknown keys, and empty headers, are refused at startup. Matching ignores case and extra spaces.

## 13. Which Core field lives where

| Core object | Field | Column |
|---|---|---|
| `Prospect` | `prospect_id`, `display_name`, `contact.phone/email/preferred_channel`, `locale`, `status`, `stage`, `conversation_id` | same-named columns of `Prospects` |
| `Prospect.schedule` | `next_action_type`, `next_action_due_at`, `last_contacted_at`, `last_inbound_at`, `follow_up_count` | same-named columns of `Prospects` |
| `Prospect.attributes` | the client's own columns | every non-canonical header of `Prospects` |
| `SalesAction` | every field; `draft` is spread over `draft_*`; the post-send plan over `next_*` | `Actions` |
| `Message` | every field | `Messages` |
| `AuditEvent` | every field | `Log` |
| `Fact` | `key`, `value`, `status`, `confidence`, `recorded_at`, `updated_at` | `fact_key`, `fact_value`, `fact_status`, `confidence`, `recorded_at`, `updated_at` of `Facts` |
| `Fact.evidence` | `source`, `message_id`, `excerpt`, `observed_at`, `recorded_by` | `evidence_*` columns of `Facts` |
| *fact identity, client and prospect* | not on `Fact` | `fact_id`, `client_id`, `prospect_id` of `Facts` |
| *client_id* | not a column (except in `Facts`, where it is a guard) | the spreadsheet itself belongs to one client |
