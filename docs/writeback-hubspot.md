# Writing results back into a client's HubSpot account

Status: **built and tested against a simulated HubSpot only (`httpx.MockTransport`). Off by default,
and to stay off until it has been validated against a real HubSpot account** (see "Before turning it
on"). It reuses the mechanism of the Google Sheets write-back (`docs/writeback-google-sheets.md`):
the same syncer, the same ledger (`op_writeback_ledger`, migration 0005), the same `sync` job and the
same rules. Only the HubSpot adapter and the operator's settings are new.

## What it does

Revansa keeps what it does for each prospect in its own database. For a client whose source is
HubSpot and who runs lanes, it can copy some of it into **contact properties that already exist** in
the client's HubSpot account:

| Field | Written only if the property is | Never |
|---|---|---|
| Status (`status_property`, usually `hs_lead_status`) | less strict than Revansa's restriction (paused, closed, opt-out if asked) | loosened; `new`/`active` are never written |
| Last contact, last reply | empty or a strictly earlier moment | replaced by an equal or later one |
| Follow-up count | empty or a strictly smaller number | replaced by an equal or larger one |
| Next action (type + due date) | empty, or still exactly what Revansa last wrote | overwritten when a person put it there |

Names, phone numbers, e-mail addresses, the do-not-contact property, the language and every other
property are never written (the client refuses them in code: `NEVER_WRITTEN`). No property is ever
created, and none is written by default.

The status is written in the CRM's own word: the **inverse of the validated `status_mapping`**. A
contact marked do-not-contact in HubSpot (`do_not_contact_property`) reads as opted out and is never
written to.

## What it takes (three steps, all deliberate)

1. The deployment: nothing to switch on (write-back is permanent). Needs the SQL database.
2. The source, in the operator's settings (the client cannot set it; the client configures nothing
   technical). Only for a source of a client's lanes (`sources:`), not for the legacy single `source`:

   ```yaml
   sources:
     - id: hubspot_a
       provider: hubspot
       settings:
         status_property: hs_lead_status
         status_mapping:            # the CRM's words; exactly one word per status written back
           NEW: new
           BAD_TIMING: paused
           UNQUALIFIED: closed
           DO_NOT_CONTACT: opted_out
         writeback:
           status: true             # written to status_property, in the inverse word
           properties:              # every value written names an existing contact property
             last_contacted_at: revansa_last_contact      # datetime
             last_inbound_at: revansa_last_reply          # datetime
             follow_up_count: revansa_followups           # number
             next_action_type: revansa_next_action        # single-line text
             next_action_due_at: revansa_next_due         # datetime
           propagate_opt_out: false
   ```

   Rules checked when the settings are saved (a refusal states why; the admin `PUT .../source`
   returns 400): the block writes at least one thing; the next action is named whole or not at all;
   opt-out propagation needs `status: true`; no two values share a property; no property is an
   identity property, the status, do-not-contact or language property; and, with `status: true`,
   `status_mapping` must be **invertible without ambiguity**: exactly one CRM value for `paused`
   and one for `closed` (and one for `opted_out` if `propagate_opt_out`). Two values for one
   status (`LOST` and `UNQUALIFIED` both `closed`, or the same word in two cases) or none are
   refused; the source is then neither declared ready nor written, and a client whose already
   published settings are in that state does not start (a `RuntimeBuildError`, like Sheets).
3. The client's agent must be running and not in the global `dry_run` mode (then no syncer exists).

A source without a `writeback` block is never written, whatever the deployment says.

## Readiness: never "ready" without a real answer

Before every pass the source is verified (`/crm/v3/objects/contacts?limit=1`, then one
`GET /crm/v3/properties/contacts/{name}` per written property). The source is **not ready**, so
neither read for values nor written, if: the token is refused or cannot read contacts; the Private
App cannot read property definitions; a named property does not exist; it is of the wrong type
(`datetime`, `number`, `string`; the status must be an enumeration offering every word Revansa
would write, or a text); or HubSpot says it is calculated or read-only. `/health/clients` shows
counters only (`writeback`: sources, applied, conflicts, not_ready, stopped, failed...) and the
sentences `write-back <source>: the source is not ready` / `stopped (auth|rate_limited|...)`. The
per-source `can_write_back` flag of the descriptor only says the provider is able; what is
actually allowed is the source's own `capabilities.writable`, empty unless the block is there.

## How it runs

One syncer per source, on the `sync` job (every 10 minutes). Each write names the value the
source held when it was read. Just before a write, the contact is read again and every expected
value is compared (statuses through the mapping, moments to the second, counts as numbers); any
difference is a conflict and **nothing is sent for that call**. Writes are one `PATCH
/crm/v3/objects/contacts/{id}` per contact (a clear is an empty string). Failures: 401/403 stop the
pass (credentials / scopes), 429 honours `Retry-After`, 5xx and network errors are deferred with
backoff, 404 is an unknown prospect, 400 is a failure of that write. Nothing HubSpot says and no
token is ever put in an error, a log or a `repr`. After a restart nothing is written twice: the
ledger is durable, a write whose answer was lost is found already applied on the next pass.

## The limits that cannot be removed

- **HubSpot has no compare-and-set over its API.** The contact is re-read right before the write
  and the write is a separate request: a person who edits that property in between is overwritten.
  The window is one round trip, not zero.
- A call with several contacts is checked for all of them first, but sent one `PATCH` at a time:
  if the second fails after the first succeeded, the first stays written (the next pass sees it as
  already applied).
- HubSpot's own automations (workflows, lead-status rules) may change `hs_lead_status` after a
  write; Revansa will only ever tighten again, never fight back.
- `hs_lead_status` is one status per contact: if the client uses it for other pipelines, mapping
  it is the operator's call.

## What was tested, and only in simulation

`tests/app/test_hubspot_writeback.py` and `tests/app/test_hubspot_client.py` (a stateful fake
account that applies PATCHes and enforces enumeration options): bounded write, forbidden
properties, 401/403/404/429(+Retry-After)/5xx/lost connection, no token or HubSpot text in any
message, status tightened only, human values kept, ISO and epoch-ms dates, unreadable values
skipped, restart without duplicate (SQLite ledger), lost answer recovered, readiness checks, flag
off / dry-run / no block = no request, ambiguous mapping refused, isolation between two clients
and between a HubSpot and another source of one client. **Never exercised against real HubSpot.**
In particular the exact formats HubSpot accepts and returns for `datetime`/`number` properties,
the shape of the property-definition answer (`type`, `options`, `calculated`,
`modificationMetadata.readOnlyValue`) and the 4xx codes of a refused value are taken from the
documentation, not observed.

## Scopes the HubSpot Private App needs

- `crm.objects.contacts.read` (already required to read the prospects);
- `crm.objects.contacts.write` (to update the properties);
- `crm.schemas.contacts.read` (to check that the properties exist and have the right type; without
  it the source is reported not ready, with that scope named).

Nothing needs `crm.schemas.contacts.write`: Revansa never creates a property.

## Before turning it on: manual validation with a real account

Not done. At least, with a **test** HubSpot account (or a sandbox), a Private App with the three
scopes, and the five properties created by hand with the right types:

1. save the settings; confirm `PUT` is refused with a clear message for an ambiguous mapping;
2. one pass with a test contact: each value lands in its property, nothing else changes (compare
   the contact's whole property history), a second pass writes nothing;
3. edit each property by hand (a later date, a larger count, your own next action, a stricter
   status): it must survive; set `hs_email_optout`: the contact must stay untouched;
4. remove or rename a property, change its type, remove an enumeration option: the source must
   report not ready and send no `PATCH`;
5. remove the write scope: the pass must stop with `auth`, nothing else;
6. watch the quota (a pass reads the contact list twice, verifies each property once, and re-reads
   each contact before writing it) and the duration of a pass on a realistic account;
7. restart the application between two passes and confirm that nothing is written twice;
8. check what HubSpot's own workflows do with the written status.

## Update: client OAuth and the client's own write-back settings

- **Connection.** A client connects its HubSpot account with OAuth (`/client/v1/hubspot-access`,
  `src/sales_app/hubspot_wiring.py`): refresh token encrypted in the client's scope, access token
  (30 min) renewed per request, `reauth_required` on revocation. The Admin enters the HubSpot app's
  client id / secret / state key in Settings (global, write-only) and registers the redirect URL and
  scopes shown there (`crm.objects.contacts.read`, `.write`, `crm.schemas.contacts.read`). OAuth is the
  only path offered to a client (spec 7.1). The legacy Private App token (`hubspot.access_token`) is
  no longer proposed in any Client screen or route; the backend still reads it, and an Admin can still
  set it, only so that a client already running on one keeps working (compatibility, not a normal path). Only one HubSpot account per client.
- **Settings.** The client chooses the properties in
  `PUT /client/v1/lanes/{lane}/sync`.
  Same route for Google Sheets columns. There is no master switch any more (spec 6.3, 6.4, 17.1):
  write-back is permanent, and a source with no mapping has nothing to write. The client cannot switch
  it off either; it can only change the mapping.
- **Not done:** conversation text is not sent to HubSpot (no notes/engagements), only the properties
  above. Nothing here was tried against a real HubSpot account.
