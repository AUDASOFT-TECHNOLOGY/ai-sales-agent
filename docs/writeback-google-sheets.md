# Writing results back into a client's Google Sheet

Status: **built and tested against a simulated spreadsheet only. Off by default, and to stay off
until it has been validated against a real Google Sheet** (see "Before turning it on").

## What it does

Revansa keeps what it does for each prospect (status, last contact, last reply, follow-ups, next
action) in its own database. For a client that runs lanes, it can also copy five of those into
columns of the client's own spreadsheet, so the client sees them where they work:

| Field | Written only if the cell is | Never |
|---|---|---|
| Status | less strict than Revansa's restriction (pause, closure, opt-out if asked) | loosened; `new`/`active` are never written |
| Last contact, last reply | empty or a strictly earlier moment | replaced by an equal or later one |
| Follow-up count | empty or a strictly smaller number | replaced by an equal or larger one |
| Next action (type + due date) | empty, or still exactly what Revansa last wrote | overwritten when a person put it there |

Names, phone numbers, e-mail addresses, the language and every column the client added are never
written. No column is ever created.

## Turning it on (three steps, all deliberate)

1. The deployment: `SALES_AGENT_WRITEBACK_ENABLED=true` (default `false`). Needs the SQL database:
   the attempts are kept in `op_writeback_ledger` (migration 0005).
2. The source, in its settings: the client chooses the columns in `PUT /client/v1/lanes/{lane}/sync` (Results button), or the operator writes them:

   ```yaml
   sources:
     - id: sheet_a
       provider: google_sheets
       settings:
         spreadsheet_id: ...
         writeback:
           columns:            # every field written names an existing column, one by one
             status: "Statut"
             last_contacted_at: "Dernier contact"
             last_inbound_at: "Dernière réponse"
             follow_up_count: "Relances"
             next_action_type: "Prochaine action"   # the two next-action columns go together
             next_action_due_at: "Échéance"
           propagate_opt_out: false
   ```
3. The client's agent must be running, and the client must not be in the global `dry_run` mode
   (then no syncer exists: the source is neither read nor written by the write-back).

A source without a `writeback` block is never written, whatever the deployment says.

## How it runs

One syncer per source, run by the `sync` job every 10 minutes, on its own clock and backoff. Before
each pass the source is verified; if a chosen column is missing or appears twice (the spreadsheet
was changed), that source is skipped, not read for values and not written. A source that fails
stops only itself: the others, the sending and the replies go on. `/health/clients` shows counters
only (`writeback`: sources, applied, conflicts, not_ready, stopped, ...), never a name, a contact
or a cell value.

## The limit that cannot be removed

**Google Sheets has no atomic compare-and-set.** Before writing a cell Revansa re-reads its row and
refuses if the cell no longer holds what it expected; the write then goes out in a separate
request. A person who edits that cell between the check and the write is overwritten. The check
narrows the window to one API round trip; it is not a guarantee. A test records this limit
(`test_a_person_editing_between_the_check_and_the_write_is_still_overwritten`).

## Before turning it on: manual validation with a real Google Sheet

Not done. At least:

- a test spreadsheet shared with the platform's service account, with the six columns named;
- one pass with a test prospect: each of the five fields lands in the right cell, nothing else
  changes, a second pass writes nothing;
- edit a cell by hand (a later date, a larger count, your own next action): it must survive;
- remove or rename a chosen column: the pass must be skipped and nothing written;
- watch the quota (about five reads and one write per source and per pass: verification, the list, the mirrored columns, and the check before a write) and the time of a pass;
- restart the application between two passes and check that nothing is written twice.
