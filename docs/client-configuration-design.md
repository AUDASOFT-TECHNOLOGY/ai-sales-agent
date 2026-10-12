# Client configuration design

Status: **the decisions in section E are approved; the `ConfigStore` and the `config` CLI are
implemented (section F), and so are the Brain settings schema and the document schema 2 (section
F.1).** `ContactPolicy` and the CEO report are still proposals. The runtime refresh and the Admin
API now exist for what they cover (credentials, the models, and a client's name, quota and LLM):
sections 6 and F were written before them and are corrected where they say otherwise. **Section G
documents the LLM models, their Model IDs and how an Admin assigns one.** The current YAML keeps
working unchanged.

Scope note: this document covers the **Configuration** module of the future Web Admin. The overall
product direction (Configuration + CRM + Dashboard, MVP versus future, shared KPI source, Admin API
boundary) is in [web-admin-product-design.md](web-admin-product-design.md). Where the two differ,
that document wins; in particular the module list in section 7 below is a first sketch, superseded by
its three-module structure. The order of work in decision 10 was confirmed there (section 9): items
(a) and (c), the Google Sheets adapter and the `ConfigStore` with its CLI, are done. The Brain settings
schema (decision 5) is done too: see [brain-settings.md](brain-settings.md).

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
| **1. Infrastructure / deployment** | secrets, credentials, server settings, security, mode ceiling, guardrail bounds; plus the operator-owned *client wiring* (which Brain package, sheets/WhatsApp providers, secret references, WhatsApp account ids, enabled flag) | operator | environment / `client.yaml` / operator console | yes |
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
| Which Brain package, sheets/WhatsApp providers, enabled flag, secret references (including `llm.api_key_env`) | 1 (wiring) | operator console only | `clients/<id>/client.yaml` |
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
| LLM provider and model | 1 (assigned by the Admin) | **never by the client.** The Admin assigns one of the models configured in Settings, under a provider that is wired (section G) | settings document (`llm`) |
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
| LLM model | allowed list (optional) | nothing: the Admin picks | must be configured and on in Settings, and in the allowed list if one is set (section G.3) |

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
| Schedule, daily limit, report, opt-out words, Brain settings | next tick / next action (cheap runtime rebuild) |
| LLM model | at once when the Admin assigns it (section G.4); a version published with the CLI waits for the next refresh or start |
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
7. **CEO daily report as a new capability** (its metrics must come from the same KPI computation as
   the Web Dashboard, see [web-admin-product-design.md](web-admin-product-design.md) section 5):
   metrics computed by the Core from Sheet data,
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

## F. Implemented: ConfigStore (MVP)

Code: `src/sales_app/config_store/` (port and stores), `src/sales_app/config/resolution.py` (settings
document rules), `src/sales_app/config_cli.py` (`sales-agent config`). `sales_core` is untouched.

**Port.** `ConfigStore` (in `sales_app`, not the Core) stores opaque JSON documents, append-only and
numbered 1, 2, 3 with no gaps: `current`, `get`, `history`, `publish(..., expected_parent)`. A stale
`expected_parent` raises `ConfigConflictError`. The latest version is the current one; a rollback is
the old document published again as a new version. Implementations: `FileConfigStore` (MVP) and
`InMemoryConfigStore` (tests). A PostgreSQL store later is another implementation of the same
protocol, in an adapter package because `sales_app` may not import database libraries.

**Files.** `<config dir>/<client_id>/versions/000001.json ...`, default `config/`
(`SALES_AGENT_CONFIG_DIR`), git-ignored, to be backed up. Each file is an envelope (client, version,
parent, UTC time, author, note, SHA-256 checksum) around the document. A version appears atomically
(temporary file hard-linked into place, which cannot replace an existing version), so a `config
publish` from a second process is safe while the agent runs. On read, a bad checksum, an unreadable
file, a renamed file or a gap in the numbering is `ConfigCorruptError`; nothing is guessed, and a
corrupt history never accepts a new version. Reading never creates the directory.

**What a document holds.** Only client-editable keys, with a `schema_version` (currently 1) and the
same key names as `client.yaml`:

```
schema_version, display_name, timezone, mode, daily_action_limit, opt_out_keywords,
sheets{spreadsheet_id, prospects_tab, actions_tab, messages_tab, log_tab, column_mapping},
llm{provider, model}, schedule{daily_at, days}, brain{settings}
```

Everything else stays in `client.yaml` (operator wiring): `client_id`, `enabled`, `brain.factory`,
providers, secret variable names, `whatsapp.*`, `max_attempts`, the poll intervals. A document that
contains any of them, an unknown key, or a key that looks like a secret (`password`, `token`,
`api_key`... as whole words; `max_tokens` is fine) is refused. Secret values stay in the
environment. `paused` and `reports` are not in schema 1; they arrive with their features.

**Validation.** `ClientFile` remains the single schema. A document is checked for structure (above),
then merged with the operator wiring and validated by `ClientFile`, so every existing rule applies
(placeholders, timezone, ranges, `HH:MM`). Every error is collected. `publish` stores the *normalized*
document, so defaults such as `mode: dry_run` are explicit in the history. Not yet checked at
publish time: the Brain's own settings schema (next block), guardrails beyond today's ranges
(`ContactPolicy`), and a connection test.

**Resolution and precedence.** `resolve_client_config(directory, env, mode_ceiling, store)` builds
one client's immutable `ClientConfig`. If the store has a version for the client, its document
supplies the settings-owned keys and any settings left in `client.yaml` are ignored (never blended:
keys the document omits take `ClientFile`'s defaults). If it has none, `client.yaml` is used exactly
as before, so existing files keep working. `ClientConfig.config_version` and `config_source` say
which, and appear in `/health/clients` and `sales-agent validate`. The mode ceiling, the secrets
check and the cross-client checks apply as before.

**Invalid configuration.** `publish` and `rollback` refuse an invalid document and write nothing.
The resolver never raises and never mutates a running config; a bad version simply yields errors, so
a caller that holds a running config can keep it. At startup, an invalid or unreadable current
version fails fast with every problem listed, like any other configuration error; the operator
rolls back.

**CLI.** `sales-agent config validate | publish | show | history | diff | rollback | import`
(exit 0 ok, 1 store problem, 2 invalid input). `import` seeds version 1 from the settings already in
`client.yaml`. The typical migration is `config import <client>`, after which the settings in
`client.yaml` are no longer read.

**Prepared, not built.** Runtime refresh: `Application.config_store` is kept, the resolver is a pure
function, and configs are immutable and versioned, so a later refresh loop can call the resolver,
build a new runtime beside the old one, and swap only on success (section 6). Nothing polls the
store yet, so a new version takes effect at the next start. *Update:* the refresh now exists
(`RuntimeManager`, section G.4), triggered by what the Admin API writes and by credential changes,
not by polling: a version published with the CLI is still only picked up at the next refresh of
that client or at the next start. Not built: `ConfigService`, `config_version` in Engine audit
events.

### F.1 Document schema 2 and Brain settings

Implemented after the ConfigStore; details in [brain-settings.md](brain-settings.md).

- **Schema 2** adds `brain.settings_version` and an `inbound` section (spontaneous conversations,
  unknown-contact policy, anti-abuse limits, allowed decisions). New documents default to the safe
  side (spontaneous conversations off, unknown contacts ignored, and a prospect is created for an
  unknown sender only with policy `engage`). The Engine enforces the unknown-sender part (ignore or
  engage); the limits and allowed decisions are stored and validated but **not yet enforced**.
- **Schema 1 keeps working.** It is upgraded in memory, keeping today's behaviour (replies handled,
  unknown senders ignored), and it cannot contain schema 2 keys. `client.yaml` files without an
  `inbound` section behave the same way, and `config import` writes that behaviour explicitly.
  Stored versions are never rewritten.
- **Brain settings are validated by the Brain's own `SETTINGS_SPEC`** (a module attribute next to
  its factory, found with `importlib`) when it has one, at publish time and at load time, and the
  normalised latest version is what is stored and passed to the Brain. A Brain without a spec keeps
  opaque settings. Invalid Brain settings are never written, and never disturb a running
  configuration.

## G. Implemented: LLM models and their assignment

Code: `src/sales_app/model_catalog.py`, `llm_models.py`, `model_usage.py`, `connection_tests.py`,
`config/offered_models.py`, `config/published.py`, `wiring/providers/` (descriptors),
`api/admin_settings.py`, `api/admin_integrations.py`, `api/admin_clients.py`. `sales_core`,
`sales_brain_kit` and the Brains are untouched and name no model. Tests: `test_admin_model_catalog`,
`test_model_assignment_rules`, `test_model_protection`, `test_admin_client_apply`,
`test_openai_model_test`, `test_client_cannot_choose_model`, `test_model_assignment_end_to_end`.

### G.1 The rule: only an Admin assigns a model

The Client API (`/client/v1`: login, logout, `auth/me`, `profile` read and update, password) has no
route and no field for a provider or a model, and its responses do not show one. `PATCH /profile`
accepts only `name`, `login` and `whatsapp_number`; any other field, `llm` or `model` included, is
refused with **422**. The login and password bodies ignore unknown fields without reading them. A
Client session opens nothing under `/admin/v1`. The tests pin the list of Client routes and scan the
generated OpenAPI contract, so a new route or field has to be reviewed.

On the Admin side, **only the OWNER role assigns a model**: `PUT /clients/{id}/llm`, `PATCH
/clients/{id}` and `POST /clients` require it, and so do the routes that write the model list and
the credentials. The role is checked first, so an operator or an editor gets 403 whatever the
client, and learns nothing about clients outside its scope. They can still read a client and its
model; reading is open to any admin. Later, a subscription plan is meant to assign the model
automatically; that is **not implemented**.

### G.2 Provider, catalog, Model ID, assignment: four separate things

- **Provider and credentials.** A provider is a descriptor (`wiring/providers/`). The OpenAI API key,
  organization, project and base URL are stored by the Admin in Settings (credential store, global
  scope), with the environment variable named by `llm.api_key_env` as a fallback for the key.
- **Model list.** A descriptor declares the models it serves by *key* and *label* only (OpenAI:
  `gpt-lune`, `gpt-sol`; Meta: `muse-spark`) and, with `model_list`, where the Admin keeps its own
  models. For OpenAI that is the credential `openai.models`, a JSON list of `{name, id, active, key}`
  (plus `last_test`, G.6), managed with `/admin/v1/settings/openai/models`. Until it is first saved,
  the older `openai.model_sol` and `openai.model_lune` ids stand in for it. No Model ID is written in
  the shipped code (a test enforces it).
- **Model ID.** The `id` of an entry is **the exact identifier OpenAI is called with**. It is what a
  client's `llm.model` holds and what `OpenAIProvider` receives; nothing translates it on the way.
  A new compatible model is added by an Admin in Settings (name and Model ID): no change to the Core,
  a Brain or the code.
- **Catalog and key.** Each entry has a stable **key**, derived from its name when the entry is
  first saved ("Big Model 2" becomes `big-model-2`), kept when the name or the id changes, unique,
  and never one of another provider's keys. An entry whose key is a declared model's gives it its
  Model ID; any other entry is a model of its own. The key is Revansa's name for the model and is
  **never sent to a provider**. The catalog is built at each request from the descriptors and
  Settings (`settings_catalog`) and shown in `GET /admin/v1/settings/llm`, section `catalog`: per
  provider (wired or not), each model with `key`, `label`, `configured`, `active`, `runnable` and
  `last_test`. The `providers` section of the same response is unchanged, and Meta is not in it.
  Clients are still assigned by **Model ID**, not by key; assignment by key is not implemented.
- **Assignment.** An Admin gives a client a provider and a Model ID (G.3). It is written to the
  client's settings document (`llm`), or to its `client.yaml` for a client created and not yet
  configured.

`SALES_AGENT_MODEL_UPSTREAM_IDS` (introduced for an earlier catalog) is **deprecated and ignored**: a
warning is logged at startup. The Model IDs have a single source, Settings.

### G.3 What an Admin may assign

The same rule, `check_model`, for `PUT /clients/{id}/llm`, `PATCH /clients/{id}` and the creation of
a client. For `PUT`, the order is: provider, then the client exists (404), then the model.

| Case | Answer |
|---|---|
| provider not wired here | 400 `llm.provider 'x' is not wired in this deployment`; a provider Revansa knows but cannot run (Meta) adds `(it is known, but no adapter can run it yet)` |
| outside `SALES_AGENT_ALLOWED_LLM_MODELS` (when set) | 400 `llm.model 'x' is not allowed for provider 'p'` (or `llm.provider 'p' is not on the deployment's allowed list`), checked before Settings |
| no model configured for the provider in Settings (no list, or an empty one) | 400 `no model is configured for provider 'p' in Settings: add one there before assigning a model to a client` |
| Model ID not in the list | 400 `llm.model 'x' is not configured in Settings for provider 'p'` |
| in the list but switched off | 400 `llm.model 'x' is switched off in Settings for provider 'p': switch it on, or choose another model` |
| in the list and on | accepted |
| OpenAI API key not set | **accepted**; the client is not ready (see G.4) |

A provider with no list in Settings is bound by the allow-list alone. A refusal publishes and writes
nothing. A deployment without a credential store cannot hold a list, so an Admin cannot assign a
model there (clients are then configured with files or the CLI).

**Loading is not strict.** The check applies when an Admin assigns, never when a configuration is
loaded: a client whose `client.yaml` or published version names a model nobody configured keeps
running. One loading rule does exist: as soon as Settings holds an OpenAI list (or the older ids
that stand in for it), a client whose model is not on in it is **not ready** (`LLM model 'x' is not active in Settings`) and takes no work. Add
the models of the existing clients before the first new one.

### G.4 A change is in force at once

After `PUT /clients/{id}/llm` and `PATCH /clients/{id}` (model, name, quota) publish a version, the
running client is refreshed with the same `RuntimeManager` refresh a changed credential triggers: it is resolved again, a new Engine is built beside the old
one and replaces it when no job uses it (the process of section 6). The request waits up to
5 seconds (`REFRESH_WAIT_SECONDS`); it succeeds either way. Whether the change is in force is read at
`GET /admin/v1/clients/{id}/runtime-status` (`applying`, `applied`, `pending`, `not_ready`,
`failed`, with reasons). If the refresh fails, the previous runtime keeps running and the Admin
shows what really runs.

- What the Admin shows for a client with a runtime is its running configuration (its name
  excepted, see below); for a client
  without one (disabled, not started), its `client.yaml` with the newest published version laid over
  it.
- **A client renaming itself does not refresh its agent.** The new name is published and the Client
  API and every Admin screen that names the client (client list and details, agent list and
  details) show it at once, but no Engine is rebuilt: a client could otherwise repeat the rename
  and rebuild its agent, on the one worker every client's refresh shares, as often as it likes.
  The running agent keeps the previous name until the next refresh of that client (any change the
  Admin publishes for it, or a credential change) or the next start. Only the name is read from
  what is published: everything else the Admin shows for a running client is what runs.
- **Every new version is built on the newest published document**, not on the running configuration,
  which may lag. Otherwise the next change, an Admin's quota edit or the client renaming itself,
  would silently undo an assignment that had not caught up.
- Editing or deleting a model, or the OpenAI credentials, refreshes all clients. Recording a test
  result (G.6) refreshes none.
- Nothing polls the config store: a version written with the CLI is applied at the next refresh of
  that client or at the next start. `PATCH status` (enabling a client) still needs a restart.

### G.5 A model that clients use is protected

A client uses a model when its published version, its `client.yaml` or its running configuration
names it, disabled clients included.

- **Delete a model, or change its Model ID:** refused with **409**; `detail` is an object with
  `message`, `client_ids` and `models`. Nothing is saved. The Web Admin shows the message followed
  by the clients (`web/src/admin/api.ts`, `detailText`).
- **Switch it off:** allowed. The `PUT` answer carries `impacted_clients` and `warning`; those clients
  become not ready (G.3) until it is switched on again or they get another model. Changing only a
  model's name is free.
- **The generic credential routes** (`PUT` and `DELETE /settings/integrations/{key}` on
  `openai.models` and the older `openai.model_*`) apply the same protection but are stricter: they
  refuse (409) any write that would remove **or switch off** a model in use, since they cannot carry
  a warning. Other credentials are not concerned.
- Not atomic: the check and the write are two steps, so two Admin requests at the same moment could
  both pass. These rules guard the Admin API only: `sales-agent config publish` and a hand-edited
  `client.yaml` are not subject to the Settings rules of G.3 or to this protection.

### G.6 Testing a model for real

The existing connection test, `POST /admin/v1/settings/integrations/openai/test`, only lists what
the account can see; it is free and unchanged. **`POST
/admin/v1/settings/openai/models/{model_id}/test?confirm=true`** (OWNER) is different: it asks the
model, by its exact Model ID, for a few tokens and says whether it is really usable.

- Without `confirm=true` nothing is sent: 400, with a message saying the request is real and may be
  billed. An unknown Model ID is a 404. A switched-off model can be tested and stays off.
- One request, `max_completion_tokens` = **16** (the parameter the OpenAI adapter sends at run
  time), 30 seconds at most, with the stored key, organization, project and base URL.
- The answer says `outcome`: `usable`, `no_api_key` (nothing sent), `invalid_api_key` (401, 403),
  `model_not_found`, `invalid_parameters` (400, 422), `quota_exceeded` (429: no quota left or rate
  limit, told apart in the message), `timeout`, `unreachable`, `service_error`,
  `unexpected_response`; with `request_sent`, `tested_at`, `recorded` and, from the service, only its
  error code, the tokens used and the model that answered. Messages are fixed sentences: no key and
  no text from the service is repeated. A reasoning model that stops at the 16-token limit still
  counts as `usable`.
- **Never tested:** how the last test ended (`outcome` and time, nothing else) is kept with the model
  and shown as `last_test` in the catalog; `null` means never tested. It follows the model through
  renames and on/off changes and is dropped when the Model ID changes. It is not invalidated when the
  key, organization or base URL change.
- Tests of the code never call OpenAI: the HTTP transport and the SDK are replaced.

### G.7 Known limits

- **`SALES_AGENT_ALLOWED_LLM_MODELS`** stays a hard guardrail on top of Settings. When it is set, a
  model added in Settings must also be listed there or its assignment is refused. A published
  version or `client.yaml` naming a model outside it fails that client's configuration: at startup
  the application does not start; at refresh the change is reported `failed`.
- **Existing clients** are not migrated and keep running as before (G.3); only an Admin assignment is
  strict. The loading rule on a model missing from the OpenAI list applies once a list exists.
- **Meta and Muse Spark are not runnable.** `muse-spark` is declared by the `meta` descriptor and
  appears in the catalog with `runnable: false`. The descriptor has no `build`, so Meta is not in the
  wired providers and cannot be assigned. Meta has no list in Settings and no credential in the
  credential store catalog, and no call to Meta is made anywhere. Its descriptor names the
  `openai_compatible` adapter family, but its structured-output behaviour is declared unverified
  (`capabilities`), so it is not claimed to work. OpenAI and Meta stay two providers.
- **Still OpenAI-only:** the routes under `/settings/openai/models`, the `openai.*` credential keys,
  the loading rule of G.3 and the `providers` projection of `GET /settings/llm`. They move to
  descriptors when a second provider gets a model list.
- **Not implemented:** assignment by plan and assignment by catalog key. The Web Admin does not show
  the `warning` and `impacted_clients` returned when a model is switched off.

## H. Implemented: the prospect source a Client chooses

A client's prospects come from **one source at a time**: `google_sheets` or `hubspot`. The Client
chooses it, gives what that source needs from the client, and checks the connection, all from the
Client API. Everything here is *configuration*: HubSpot can be chosen, given its token and tested,
but **no agent runs on it yet** (its connector is a later increment).

### H.1 What lives where

| Value | Owner | Where | Seen by the Client |
|---|---|---|---|
| Which source (`source.provider`) | Client | the versioned settings document, written only when it differs from the store's provider | yes |
| Spreadsheet (id, from a pasted link or id) and prospects tab | Client | the settings document (`sheets.spreadsheet_id`, `sheets.prospects_tab`) | yes |
| The address to share the spreadsheet with | Platform | the `client_email` of the service-account file | yes (an address, never the file) |
| HubSpot Private App token | Client | credential store, scope `client:<id>`, key `hubspot.access_token` | **no: write-only**, only "stored or not" |
| Store tabs, column mapping, quota, mode, model, WhatsApp number and Meta credentials | Admin / platform | settings document and credential store | never changeable here |
| Google service-account file | Platform | environment (`sheets.credentials_file`), unchanged | no |

### H.2 The settings document

`source` is a section of the current schema (version 2) holding only `provider`. A client that never
chose has no `source` and its source is the store's provider, as it has always been: **every existing
document, dump and configuration is unchanged**. `extract_document` writes `source` only when the
source differs from the store's provider, so choosing Google Sheets again leaves no trace. `sheets`
keeps describing the spreadsheet that holds Revansa's own state (the store), whatever the source is:
the schema can later tell a source and a store apart (`ClientFile.source_provider`,
`source_apart_from_store`), without that being implemented now.

A source apart from the store is **refused wherever it would be run**: `build_runtime` raises
`RuntimeBuildError` ("cannot be run yet"), so a client never keeps running on the store's provider
instead; a refresh that hits it is reported `failed` and the settings in force stay as they were. The
canonical normaliser refuses it too (`NormalizationError`) until it can be represented.

### H.3 The Client routes

No route takes a client id: it comes from the session cookie. All answers are `Cache-Control:
no-store`.

- `GET /client/v1/source`: `version`, `source` (its key; the Client API never has a field named
  `provider`, a word reserved to the model: see the contract test), `runnable`, the `options` (key, label, `runnable`),
  `google_sheets` (`spreadsheet_id`, `prospects_tab`, `share_with`) when that is the source, and
  `credential` (`label`, `configured`, `updated_at`) when the source needs a secret from the client,
  and `last_test` (`ok`, `checked_at`, `current`; `null` if never tested). `current` is false once the
  result no longer applies (see H.4).
- `PUT /client/v1/source` with `{expected_version, source, google_sheets?: {spreadsheet?,
  prospects_tab?}}`. Any other key answers 422 and is not echoed. A stale version answers 409, an
  invalid result 422 with every problem, and **nothing is written** on a refusal. Choosing what is
  already chosen publishes nothing. Changing the provider **pauses a running agent** (the Client's
  own switch): it is started again deliberately. If that switch cannot be written, nothing is
  published (503).
- `PUT /client/v1/source/credentials` with exactly `{"access_token": "..."}`, `DELETE` to remove it.
  Only for a source that declares a client credential (HubSpot, once chosen; otherwise 409). The key
  written is the one the chosen provider's descriptor declares, never one typed into a route. The
  value is checked against its pattern (`pat-<region>-<uuid>`); a refusal says the rule, never the
  value. A token already held by another client answers a generic 409 that names nobody. No response,
  error, log or audit event carries the token.
- `POST /client/v1/source/test` makes **one real, read-only call** to the chosen source and answers
  `{source, ok, message, details, checked_at}` and **keeps the outcome** (H.4); calls are spaced (429 when repeated within 3 seconds).
  - Google Sheets: reads the spreadsheet's tab list with the platform's service account and checks
    that the prospects tab exists (one try, 15 s). Nothing is written and no cell is read.
  - HubSpot: `GET /crm/v3/objects/contacts?limit=1&properties=hs_object_id` with the client's token.
    It succeeds only when the token is valid **and** may read contacts; the contact is discarded.
  - A success needs an answer of the documented shape; a failure is a fixed sentence plus the HTTP
    status (and HubSpot's plain error category). Timeouts, unreachable services and unexpected
    answers are failures, never successes. A success proves the access at that moment, for the
    configuration tested, and nothing more.

### H.4 Readiness

`GET /client/v1/readiness` gains `source_connected`. It holds only when **all** of this is true: the
source exists in the provider registry and **is runnable**, any secret it needs from the client is
stored, and the client's **last real test succeeded for the configuration it has now**. HubSpot never
passes (`HubSpot cannot run your agent yet`), so `POST /client/v1/agent/start` answers 409 with that
reason and `active` stays false, whatever its tests say.

The outcome of each test is kept per client (`client_source_check`, migration `0003`; the file
`source_checks.json` when there is no database; one record, replaced by the next test) with a
**fingerprint** of what it was made for: the source, and for Google Sheets the spreadsheet and the
prospects tab; for a source with a client secret, the moment that secret was last written (never the
secret). The fingerprint is a SHA-256: opaque, no secret, no identifier in the clear. Any change
makes the stored result stop counting (`last_test.current` false) until a new test succeeds;
changing back to exactly what was tested makes it count again. A failed test is kept and counts as
failed. A store that cannot be read never counts as connected. The client can neither write nor
forge a result: only the test route records one.

**Migration note.** Existing clients have no test on record. Their agents that already run keep
running (the scheduler reads the Client's switch and the Admin's `enabled`, not readiness), but
readiness says `source_connected` is not met and `active` is false until they test once, and a
paused or never-started agent cannot be started before that test. No automatic migration is done.

### H.5 HubSpot descriptor

`wiring/providers/hubspot.py` declares a source with no `build` (not runnable), no settings yet
(`EmptySettings`), and one credential: `hubspot.access_token`, client scope, sensitive, required,
unique. No runtime code reads it (an architecture test holds that); it is not part of a client's
resolved secrets.

### H.6 Known limits

- A test result is not re-checked over time: it counts until the configuration it was made for
  changes. A spreadsheet unshared later, or a token revoked, is seen at the next test or at runtime,
  not by readiness.
- A client edits the spreadsheet and the prospects tab only. The column mapping of the prospects and
  the store's tabs stay the Admin's (they share one `column_mapping`).
- Sheets access is the platform's single service account; per-client OAuth is not part of this.
- The Admin can still write `hubspot.access_token` for a client through the integrations routes and
  sees only whether it is set.
- Not implemented here: the HubSpot connector, a source and a store of different providers running
  together, write-back to HubSpot, and the Web UI for these routes.
