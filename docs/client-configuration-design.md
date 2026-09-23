# Client configuration design

Status: **proposal, nothing implemented.** No code changes until the decisions in section E are
approved. The current YAML keeps working unchanged in the meantime.

Goal: a future Web Admin lets each client configure its own Sales Agent (sheet, offers, strategy,
schedule, CEO report...) **without code changes and without a database in the MVP**, while the Core
keeps its safety guarantees no matter what a client types.

## 0. Principles

1. **Every setting has exactly one home and one owner** (four categories, section 1).
2. **Clients can tighten, never loosen.** Deployment guardrails bound client settings. Core
   invariants are not settings at all.
3. **Configuration is data, not code.** Anything a client edits is a versioned, schema-validated
   document. Brain code and Core code do not change when a client edits it.
4. **`sales_core` never learns where configuration lives.** It receives immutable objects
   (`ClientContext`, `EngineConfig`, later a contact policy) built by `sales_app`.
5. **Secrets are referenced, never stored** in configuration, versions, audit logs or the Sheet.
6. **Storage is one replaceable seam** (`ConfigStore`), so a database can arrive later without
   touching the Core.

## 1. What "client configuration" is

**Client configuration** is the set of versioned, schema-validated values that make one client's
Sales Agent behave as that client wants, *within bounds*. It has two parts:

- **Operational settings**: defined by the platform, identical for every client (sheet, schedule,
  limits, report, opt-out words, requested mode).
- **Brain settings**: defined by the client's Brain through its own schema (products, strategy,
  tone, cadence rules...). The platform stores and edits them but never interprets them.

It is *not* secrets, deployment settings, Core rules, or Brain code. At runtime `sales_app` resolves
everything into one immutable **effective configuration** (with a version number) and builds the
Engine from it.

| Category | Contains | Owner | Changes through | Never editable by the client |
|---|---|---|---|---|
| **1. Infrastructure / deployment** | secrets, credentials, server settings, security, mode ceiling, guardrail bounds; plus the operator-owned *client wiring* (which Brain package, providers, secret references, WhatsApp account ids, enabled flag) | operator | environment / `client.yaml` / operator console | yes |
| **2. Client configuration** | Sheet, schedule, daily limit, report, CEO number, opt-out words, requested mode, pause | the client | Web Admin (CLI in the MVP) | no, within bounds |
| **3. Brain configuration** | offers, objectives, strategy, audience, languages, tone, qualification, objections, follow-up and handoff rules | the client, schema owned by the Brain | Web Admin, schema-driven forms | no, within Core guardrails |
| **4. Core invariants** | safety, idempotency, state transitions, execution guarantees, security | nobody | code and tests only | yes, always |

## 2. Where every setting lives

"Web Admin" means editable there eventually. "MVP home" is where it is stored with no database.

| Setting | Cat. | Web Admin? | MVP home |
|---|---|---|---|
| OpenAI key, WhatsApp token, Meta app secret, webhook verify token, Google service-account credentials | 1 | no (operator sets; UI shows only "set / not set") | environment / `.env` (mode 600) |
| Mode ceiling, deployment guardrails (daily cap, minimum gap, max touches, sending window, allowed LLM models) | 1 | operator console only | environment / `Settings` |
| Host, port, log level, lock file, config store path, admin auth secrets | 1 | no | environment |
| Poll intervals (dispatch, recover), max attempts, backoff | 1 | no | deployment defaults |
| Which Brain package, providers, enabled flag, secret references | 1 (wiring) | operator console only | `clients/<id>/client.yaml` |
| WhatsApp agent number: `phone_number_id`, WABA id, display number | 1 (wiring) | read-only for the client | `client.yaml` |
| Display name, timezone | 2 | yes | settings document |
| **Google Sheet URL** (plus tabs, column mapping) | 2 | yes, with a connection test | settings document |
| Commercial days, cycle start (and optional end) | 2 | yes | settings document |
| Max daily commercial actions | 2 | yes, up to the deployment cap | settings document |
| Requested mode | 2 | `dry_run` and `approval`; `autonomous` needs operator approval | settings document (clamped by the ceiling) |
| Opt-out keywords | 2 | yes | settings document |
| Pause / resume the agent | 2 | yes | settings document |
| **CEO report**: on/off, time, days, sections, language, **CEO number** | 2 | yes | settings document |
| Products/offers, objectives, strategy, target audience, languages, tone and style | 3 | yes | settings document, `brain.settings` |
| Qualification rules, objection handling, message and prompt fragments | 3 | yes | settings document, `brain.settings` |
| Follow-up cadence, no-response rule, stop conditions, human-handoff rules | 3 | yes | settings document, `brain.settings` |
| LLM model choice | 3 | yes, from an operator-allowed list | settings document |
| Opt-out and status enforcement, `SENDING` guard, idempotency keys, one outreach per prospect per day, approval gating, `dry_run` cannot send, replies bypass the quota, planner tier order, client isolation, single writer, signature checks | 4 | no | code |

## 3. Core invariants and guardrails

**Invariants** (row 4 above) are code, covered by tests, and no configuration path can reach them.
Opt-out *keywords* are data; *enforcing* an opt-out is not.

**Guardrails** are Core-enforced *bounds* whose limits are set by the deployment. The client sets
values inside them and can only tighten:

| Guardrail | Deployment sets | Client may set | Effective |
|---|---|---|---|
| Daily actions | cap | any value up to the cap | `min` |
| Minimum gap between two contacts of one prospect | floor | anything at or above it | `max` |
| Maximum touches per prospect | ceiling | anything at or below it | `min` |
| Sending window (earliest/latest local time) | allowed window | a window inside it | intersection |
| Mode | ceiling | requested mode | safer of the two (already built) |
| LLM model | allowed list | one of the list | must be in the list |

Today only the mode ceiling and a fixed range for the daily limit exist. The minimum gap, maximum
touches and sending window are **new Core work** (a small `ContactPolicy` used by the planner and
Engine), needed before autonomous mode is offered to real clients.

## 4. How each kind of value is represented

### 4.1 Google Sheet URL / ID

```yaml
sheet:
  spreadsheet_id: "1AbC..."       # canonical, parsed from the URL; what the adapter uses
  source_url: "https://docs.google.com/spreadsheets/d/1AbC.../edit"   # display only
  tabs: { prospects: Prospects, actions: Actions, log: Log }
  column_mapping: {}              # advanced; defaults come from docs/sheet-schema.md
  verification: { status: unverified | ok | error, checked_at: ..., detail: ... }
```

- The UI accepts a URL or a bare id; the id is extracted and stored, the URL is kept for display.
- The platform's service account must be given access to the Sheet. The UI shows its e-mail.
- A new Sheet takes effect only after a **connection test** (reachable, tabs present, headers match
  the mapping). One Sheet belongs to one client (already validated).

### 4.2 WhatsApp agent number and CEO report number

```yaml
whatsapp:                         # operator-owned wiring, read-only for the client
  phone_number_id: "1234567890"   # Meta id, the webhook routing key, unique per client
  waba_id: "..."
  display_number: "+216XXXXXXXX"  # shown in Web Admin
  token_ref: secret://naga/whatsapp/token
  app_secret_ref: secret://naga/whatsapp/app_secret
reports:                          # client-owned
  recipients: [ { name: "CEO", phone: "+216YYYYYYYY", language: fr } ]
```

- The **agent number** is a connection, not a preference: changing it means a new Meta
  registration, webhook and token, so it stays operator-onboarded (Embedded Signup could automate
  this later). The client sees it and its health.
- The **CEO number** is client data: E.164 validated and normalised, different from the agent number.
  The CEO is **not a prospect**: not in the Sheet, outside the outreach quota and the prospect
  state machine. If the CEO answers "stop", that recipient is disabled.
- WhatsApp only allows a business-initiated message outside a 24-hour window as an approved
  **template**, so the report is sent as a template with summary variables (or as free text if the
  window happens to be open). The template name is operator wiring.

### 4.3 Schedules

```yaml
schedule:
  timezone: Africa/Tunis          # IANA; times are local and never stored as UTC
  commercial_days: [mon, tue, wed, thu, fri, sat]   # preset "all" = 7/7
  cycle_start: "09:00"            # daily commercial cycle
  cycle_end: "18:00"              # optional: no outbound after this (sending window)
reports:
  enabled: true
  at: "20:00"
  days: same_as_commercial        # or an explicit list
  sections: [sent, replies, qualified, handoffs, errors, top_prospects]   # from a Core catalogue
```

The scheduler already evaluates local time, weekdays and catch-up; the report is one more
time-of-day trigger (`REPORT`). The scheduler still only *triggers*. The polling intervals
(dispatch every 5 minutes, recovery every 15) are technical deployment defaults, not client settings.

### 4.4 Follow-up rules: Brain owns strategy, Core enforces bounds

Three pieces, each with one job:

1. **Brain settings (data, client-editable)**: the rules themselves, in the Brain's schema. Shape
   for illustration only (each Brain defines its own):
   ```yaml
   follow_up:
     steps: [ {after_days: 2, intent: reminder}, {after_days: 5, intent: value_add} ]
     no_response: { after_steps: 3, then: park }        # park | close | handoff
     stop_conditions: [ "asked_to_stop", "not_interested", "bought_elsewhere" ]
     handoff: { triggers: [ "asks_for_price", "wants_a_call" ] }
   ```
2. **Brain code**: turns those rules plus the conversation into `BrainDecision`s (next action type
   and date, `CLOSE`, `HANDOFF_TO_HUMAN`). It is the only place that knows what "cadence" means.
3. **Core guardrails**: check every decision without knowing why it was made:
   date in the future (exists), aligned to commercial days and window (**clamped** to the next
   allowed moment, since that is only time), gap at or above the minimum, touches at or below the
   maximum (a violation is **refused** and the prospect is parked for a human, with an audit event),
   closed/paused/opted-out prospects never contacted.

A client can therefore rewrite its whole strategy in the UI, but cannot configure the agent into
spamming, ignoring an opt-out, or working on a day it may not.

### 4.5 What belongs to a Brain versus the generic Core

| Naga Brain (`naga_brain`, and Naga's *data*) | Generic Core / `sales_app` |
|---|---|
| The **code** that turns Naga's rules and history into decisions; its settings schema and defaults; prompts and message skeletons | Contracts, planner, engine, safety, guardrails, approval, idempotency |
| Naga's **values** (offers, tone, cadence numbers, languages): *data*, versioned in Naga's settings document, not in code | The daily quota, priority tiers, schedule triggers, report *metrics* and delivery |
| How a day is *narrated* in the CEO report (wording, language, highlights) | Which figures exist in the report and how they are computed from the Sheet |
| Which of Naga's prospect stages exist (opaque labels) | The Core lifecycle (`new`, `active`, `paused`, `opted_out`, `closed`) |

Distinguish three things: **Brain package** (code, versioned with the repository), **Brain
settings** (data, versioned as configuration), and **client data** (prospects, in the Sheet). For
commercial scale, one configurable Brain could serve many clients; see decision 4.

## 5. Secrets

- Configuration stores only a **reference**: `secret://<client>/<service>/<kind>`. Today's
  `*_env` fields are exactly this, resolved from environment variables.
- A `SecretResolver` seam in `sales_app` turns a reference into a value **once, when a client's
  runtime is built**. MVP backend: environment / `.env`. Later: a secret manager or encrypted
  store, with no change to configuration documents.
- Values never appear in configuration versions, audit events, logs or `/health` (the `Secrets`
  class already redacts). Audit records reference names only.
- Web Admin is **write-only** for secrets and shows "set / not set / last rotated". In the MVP only
  the operator sets them. Rotating a secret rebuilds that client's runtime.
- Per-client references are isolated; only the OpenAI key and the Meta app secret may be shared.

## 6. Versioning, audit, and changing a running agent

**Versions.** The client settings document is **append-only**: v1, v2, v3... each with author,
time, note and parent version. Rolling back means publishing an old version as a new one. A change
is validated in full before it can become a version.

**Audit.** Two complementary records:
- The **version history** (full documents, so any two versions can be diffed).
- **Business audit events** in the existing Sheet log: `config.published`, `config.applied`,
  `config.rejected`, with the version, actor and changed keys (never secret values). Engine audit
  events also carry the `config_version` in force (a small future addition to `EngineConfig`).

**Applying a change to a running agent.** `sales_app` polls each client's current version on every
scheduler tick, then: validate the new version (platform schema, the Brain's settings schema,
guardrails; connection test if the Sheet or number changed), build a **new runtime beside the old
one**, and swap it in when that client is idle. In-flight jobs finish under the old version. If the
new version is invalid, the current one keeps running and the failure is reported.

| Change | Effect |
|---|---|
| Schedule, daily limit, report, opt-out words, Brain settings, LLM model | next tick / next action (cheap runtime rebuild) |
| Sheet, mode, Brain package | full rebuild of that client's runtime, connection test first |
| Pause | immediate |
| Lowering the mode | immediate; raising it needs operator approval and the ceiling |
| Secrets, guardrails, wiring | operator only, rebuild on restart or reload |

Nothing already decided is rewritten: stored drafts keep their text, next-action dates already in
the Sheet keep their dates, and `SENDING` actions stay under human control. The Engine's state lives
in the Sheet, so swapping runtimes loses nothing.

## 7. Web Admin

**Interaction.** The Web Admin is a static single-page app that talks only to an **Admin API inside
`sales_app`** (`/admin/v1`, authenticated, scoped to one client per call). The API uses a
`ConfigService` (validate, version, audit, connection tests, dry-run preview) on top of
`ConfigStore`. The frontend never touches the store, the Sheet or secrets, and the Core never sees
the frontend. Approval from the UI later becomes another service writing to the same
`SalesRepository`. Roles (operator, owner, editor, viewer) arrive with authentication.

**Modules (high level):**

| Module | Purpose |
|---|---|
| Overview | agent state, today's quota used (for example 12/20), pending approvals, replies, errors |
| Connections | Sheet URL with connection test and the e-mail to share it with; WhatsApp number and health (read-only); CEO number |
| Business and offers | Brain-schema form: products/offers, objectives, audience, languages, tone |
| Sales rules | qualification, objections, follow-up cadence editor, no-response rule, stop and handoff rules, with the guardrails shown as limits |
| Schedule and limits | working days (presets Mon-Sat and 7/7), cycle start, daily limit, timezone |
| Reports | time, days, recipients, sections, language, "send me a test report" |
| Approvals | review, edit, approve or reject proposed messages (replaces editing the Sheet) |
| Conversations | read-only prospects and history from the Sheet |
| Activity and history | audit log, configuration versions, diff and rollback |
| Agent controls | pause/resume, request a mode, dry-run against a sample prospect |
| Operator console | clients, guardrails, secret status, Brain packages, onboarding checklist |

Forms for the Brain modules are **generated from the Brain's settings schema**, so a new client or
a new Brain needs no frontend work.

## 8. Introducing PostgreSQL later without touching `sales_core`

`sales_core` sees three seams and none of them changes: **`SalesRepository`** (prospects and
history; already a port), **`ConfigStore`** (client configuration, in `sales_app`), and
**`SecretResolver`**. Steps, each independent:

1. `PostgresConfigStore` behind the same `ConfigStore` interface: versions as JSONB rows
   (`client_id`, `version`, `document`, `author`, `created_at`, `note`), plus users and roles for the
   Web Admin.
2. A one-time import of the version files, preserving version numbers.
3. Bootstrap chooses the store by an environment setting (`file` or `postgres`).
4. Optionally, later and separately, `PostgresSalesRepository` to leave the Sheet as CRM; again
   only a new adapter behind the existing port.

---

## A. Proposed configuration model (conceptual, not implemented)

Three stored artifacts and one resolved object:

```
DEPLOYMENT (env)         Settings + Guardrails + secrets values
CLIENT WIRING (operator) client.yaml: client_id, enabled, brain package, providers,
                         secret references, WhatsApp phone_number_id / waba / display number
CLIENT SETTINGS          versioned document (client-editable):
 (versioned)              meta:      version, author, created_at, note, parent
                          profile:   display_name, timezone
                          sheet:     spreadsheet_id, source_url, tabs, column_mapping, verification
                          schedule:  commercial_days, cycle_start, cycle_end?
                          limits:    daily_actions, requested_mode, opt_out_keywords, paused
                          reports:   enabled, at, days, sections, language, recipients[]
                          llm:       model
                          brain:     schema_version, settings{ ...Brain-owned... }

EffectiveClientConfig (immutable, version N) = wiring + settings, clamped by Guardrails,
    with brain.settings validated by the Brain's schema.  sales_app builds from it:
    ClientContext + EngineConfig + (future) ContactPolicy + schedule  ->  sales_core
```

Mapping of today's `client.yaml`: `client_id`, `enabled`, `brain.factory`, `*.provider`, `*_env`,
`whatsapp.*` stay in wiring; `display_name`, `timezone`, `mode`, `daily_action_limit`,
`opt_out_keywords`, `sheets.spreadsheet_id/tabs/column_mapping`, `schedule.daily_at/days` and
`brain.settings` move to the settings document; `max_attempts` and the poll intervals become
deployment defaults; `llm.model` moves to the settings document (validated against an allowed
list). Mostly reorganising, not redesigning.

## B. MVP storage without a database

| What | Where | Edited by |
|---|---|---|
| Secrets, guardrails, server settings | environment / `.env` on the server | operator |
| Client wiring | `clients/<id>/client.yaml` in git (as today) | operator |
| Client settings and Brain settings | versioned JSON files: `<config dir>/<id>/versions/000001.json ...` plus a `current` pointer, written atomically, append-only | operator via CLI now, Web Admin later |
| Prospects, actions, history, business audit | the client's Google Sheet (unchanged) | agent, humans |

- `ConfigStore` is a small interface (`current`, `get(version)`, `history`, `publish`), with a
  file implementation. The config directory is outside git (it holds client business data and will
  change through the UI) and is included in server backups.
- Until a UI exists the operator uses a CLI: `sales-agent config validate | publish | history |
  diff | rollback`. Validation is the same code the Web Admin will use.
- Single process, single writer: atomic file writes are enough; no locking service needed.

## C. Future Web Admin architecture

```
Browser (SPA) ──HTTPS──► Admin API in sales_app (/admin/v1, auth, one client per call)
                              │
              ┌───────────────┼─────────────────────────────┐
              ▼               ▼                             ▼
        ConfigService    ApprovalService               ReportService
        (validate, version, (writes to SalesRepository) (preview, test send)
         audit, tests)
              │
        ConfigStore ──(file now / PostgreSQL later)
              ▲
   scheduler tick polls the current version ──► rebuild that client's runtime ──► sales_core
```

The frontend is a separate deliverable; nothing in the MVP depends on it.

## D. Future PostgreSQL migration path

Add `PostgresConfigStore`, import the version files, switch the bootstrap setting, add users and
roles for the Web Admin. Optionally add `PostgresSalesRepository` later. `sales_core`, the Brains
and the Engine are untouched because they only ever see ports and immutable objects.

## E. Decisions that need your approval before implementation

1. **The four-category model and "clients tighten, never loosen".** Including that the agent
   WhatsApp number is operator-onboarded and read-only for clients.
2. **Split today's `client.yaml`** into operator wiring plus a versioned client settings document,
   as in section A.
3. **MVP store:** versioned JSON files behind a `ConfigStore` interface (recommended), versus keeping
   YAML in git only until the Web Admin exists, versus a config tab in the client's Sheet. The file
   store is the one that makes the Web Admin and the later database cheap.
4. **Shared configurable Brain versus one Brain package per client.** For a sellable product I
   recommend one configurable Brain family whose behaviour comes entirely from settings, with
   client-specific Brain packages only when a client needs different logic. This changes how Naga
   and Tounesimmo Brains are planned.
5. **Brain settings contract:** every Brain publishes a settings schema (JSON Schema plus defaults)
   next to its factory, so the Web Admin can render and validate forms.
6. **New Core `ContactPolicy` guardrails** (minimum gap, maximum touches, sending window) and the
   clamp-versus-refuse rule in 4.4, to be built before autonomous mode is offered.
7. **CEO daily report as a new capability:** metrics computed by the Core from Sheet data,
   optional narrative from the Brain, delivered as a WhatsApp template to a non-prospect recipient
   by a new scheduler trigger. Confirm scope and whether it precedes or follows the Sheets adapter.
8. **Apply-changes behaviour:** poll the current version each tick, rebuild the client's runtime when
   idle, keep the old one if the new version is invalid; raising the mode always needs operator
   approval.
9. **Secrets:** environment variables for the MVP, with the `secret://` reference form reserved
   now so a later backend needs no document migration.
10. **Order of work (recommended):** (a) Google Sheets adapter and `docs/sheet-schema.md`, which do
    not depend on this design; (b) commit the current work; (c) `ConfigStore` and the `config` CLI;
    (d) Brain settings schema; (e) `ContactPolicy`; (f) report; (g) Admin API and Web Admin;
    (h) PostgreSQL only when needed.
