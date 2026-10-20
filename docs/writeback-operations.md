# Write-back: permanent, what stays guarded, what is still unproven

## Decision (spec 6.3, 6.4, 17.1)

Write-back is a permanent part of Revansa. The former switch is removed: no
`SALES_AGENT_WRITEBACK_ENABLED` setting (an old value in the environment is ignored), no
`writeback.enabled` platform setting, no `GET/PUT /admin/v1/settings/writeback`, no Admin card, and
no Client option to stop it (`enabled` was removed from `PUT /client/v1/lanes/{lane}/sync`, which now
only sets the mapping; the sync states are `off` = no mapping yet, `configured`). Idempotence is kept
(`op_writeback_ledger`), and the technical guards below stay, unexposed: a source that is not
authorised, not verified or failing is a visible error on the pass, not a silent skip.

## What stays guarded (unchanged)

Nothing is written unless: the client named the columns/properties one by one (Client > Connections >
Prospects > Results); the client is on lanes (see below); the client is not in the global dry-run mode;
a database keeps the attempts (ledger); names, phones, e-mail addresses, the do-not-contact property
are never written; an existing value is only replaced when empty or when Revansa wrote it; every
client's sources, tokens and ledger are its own.

## Moving a client onto lanes (new, needed)

Per-source channel/products and write-back exist only for clients that run lanes, and nothing moved a
client there. `POST /admin/v1/clients/{id}/lanes/adopt` (Admin > client > "Prospect sources" > "Move
onto lanes", owner) turns the client's current source and channel into its first lane (`main`, whole
catalog). Refused if already on lanes; a client whose agent has been started needs `confirm_new_ids`
(prospect ids get a `<source>:` prefix). The agent is paused.

## HubSpot additions

- Status meaning (`status_mapping`) is set by the Admin in the same card, no API call needed. The
  client's write-back choices are kept when it is saved.
- A **commercial summary** property (`summary`): one line built from what Revansa holds (status,
  stage, follow-ups, dates of last message and last reply, next action), at most 240 characters, no
  name/number/address and no message text. Written over an empty property or over Revansa's previous
  text, never over a person's own text. The full conversations stay in Revansa (client space >
  Conversations).

## Not proven (needs a real account)

No real Google Sheet and no real HubSpot account has been read or written with this code: every test
uses doubles. Before relying on it: (1) a test sheet shared by OAuth with the six columns, one pass of
the `sync` job, check the cells; (2) a HubSpot developer test account with the app installed, the
properties created (types: datetime, number, text, text/datetime), one pass, check the contact.
