# Write-back: how it is switched on, who decides, what is still unproven

## Why it was off, and what changed

`SALES_AGENT_WRITEBACK_ENABLED` was introduced with write-back (commit 86ad7c3, "off by default") as a
safety "until validated against a real Google Sheet". That validation never happened, so nobody ever had
a reason to set it, the default stayed `false`, and no deployment file or Admin screen mentioned it:
the feature was announced but silently inert.

Now:

- The deployment default is **on** (`Settings.writeback_enabled = True`). `.env.example` documents it;
  set `SALES_AGENT_WRITEBACK_ENABLED=false` to start a deployment with it off. `.env` was not touched.
- The switch is the **Admin's** (Admin > Settings > Write-back, `GET/PUT /admin/v1/settings/writeback`,
  owner only). Its decision overrides the deployment default, is stored in the credential store,
  audited, applied to every running client at once (no restart) and shown with who decided.
  "Follow the deployment" removes the decision.
- The client's screen says when copying is not switched on (state "saved, not active yet").

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
