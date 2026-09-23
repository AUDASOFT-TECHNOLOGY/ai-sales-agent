# Product direction: MVP and future Web Admin

Status: **product and architecture direction, nothing of the future part is implemented.** The
decisions in sections 9 and 10 are approved. It fixes the boundaries so today's code does not block
tomorrow's product. Details of configuration
(categories, versioning, secrets, `ConfigStore`) stay in
[client-configuration-design.md](client-configuration-design.md).

## 1. MVP versus future

| | MVP (now) | Future |
|---|---|---|
| Operational CRM | The client's **Google Sheet** (live, edited by people and the agent) | **Web Admin CRM** module; the Sheet becomes one storage option, not the interface |
| Configuration | `clients/<id>/client.yaml` and environment, edited by the operator | **Web Admin Configuration** module, through the Admin API |
| Reporting | none yet | **Web Admin Dashboard** and the **CEO WhatsApp report**, both fed by one KPI computation (section 5) |
| Approvals | edit `status` in the `Actions` tab | Approvals in the Web Admin (same `SalesRepository`) |
| Storage | Google Sheets (prospects, actions, messages, log); files/YAML for configuration | Optionally PostgreSQL behind the same ports (section 7) |
| Backend for a UI | none | **Admin API** in `sales_app` |
| Frontend | none | Web Admin (separate deliverable) |

Not in the MVP and not planned for it: PostgreSQL, Redis, Celery, n8n, any admin UI or Admin API.
The MVP is complete without any of them.

## 2. Who owns what

```
Web Admin (browser)            future, separate deliverable; talks ONLY to the Admin API
      │ HTTPS
      ▼
sales_app                      composition, scheduling, webhook, and the future Admin API
      │            │
      │            └──► integrations   Sheets, OpenAI, WhatsApp, (later) PostgreSQL
      ▼
sales_core  ◄── brains/*      contracts, ports, planner, engine, safety  |  client-specific logic
```

| Layer | Owns | Never does |
|---|---|---|
| **`sales_core`** | contracts, ports, planner, engine, safety, idempotency, state transitions, and (future) the **KPI definitions and computation** | knows a client, a vendor, a storage engine, or a UI |
| **Brains** (`brains/*`) | one client's strategy, wording, cadence, settings schema | import vendors, import each other, compute KPIs |
| **`sales_integrations`** | adapters for Core ports (Sheets, OpenAI, WhatsApp, later PostgreSQL) | know clients, Brains or the app |
| **`sales_app`** | wiring, scheduler, webhook, config loading, and the future **Admin API** (authentication, configuration service, approval service, dashboard and CRM read endpoints, report delivery) | make a commercial decision; hold a rule that differs per client |
| **Web Admin** | screens and forms; forms for Brain settings are generated from the Brain's settings schema | import or call `sales_core`, integrations, the Sheet, the database or secrets |

Dependency rules for code are in [architecture.md](architecture.md) and enforced by
`tests/architecture/`. The Web Admin is outside Python, so it is bounded by the API contract: the
only thing it can reach is `/admin/v1`.

## 3. Web Admin: three modules

### 3.1 Configuration

Editing the client's own settings, within bounds. Everything here is data in the versioned client
settings document (or the Brain's settings); the four categories and who may change what are in
[client-configuration-design.md](client-configuration-design.md) sections 1 to 3.

- Sheet / future CRM connection (with a connection test); WhatsApp connection (read-only, health)
- Commercial schedule: working days, cycle start, timezone; daily action limit
- CEO report: time, days, destination
- Brain configuration: offers/products, commercial strategy, target audience, languages and tone
- Follow-up rules, qualification and handoff rules, stop/opt-out rules

Boundaries that stay true in the UI: clients can tighten guardrails but never loosen them; secrets
are write-only or operator-only; infrastructure and deployment settings stay operator-controlled;
Core invariants (opt-out enforcement, the `SENDING` guard, idempotency, approval gating...) are code
and are not settings.

### 3.2 CRM / Prospects

A view and edit surface over the data behind `SalesRepository`:
prospects, statuses, stages, last interaction, next action and follow-up dates, conversation
history, actions (with approval), appointments, conversions, opt-outs and human handoffs.

- Reads and writes go through the Admin API, which uses `SalesRepository`. The API adds no CRM
  logic of its own.
- In the MVP this module does not exist; the Sheet is the CRM.

### 3.3 Dashboard / KPIs

Period selection (day, week, month, custom) over these figures: prospects treated, new prospects,
messages sent, replies, conversations, follow-ups, appointments, conversions, response rate,
conversion rate, objections, feedback, reasons for non-conversion, human interventions.

All of them come from the single KPI computation in section 5.

## 4. Admin API (future, in `sales_app`)

- Lives in `sales_app` (`/admin/v1`), next to the webhook and health routes. It is a **service layer**
  over Core ports, not a second application.
- Authenticated, scoped to **one client per call**, with roles (operator, owner, editor, viewer).
- Services behind it: `ConfigService` (validate, version, audit, connection test, dry-run preview),
  `ApprovalService` (writes to `SalesRepository`), `ReportService` (KPIs, CEO report preview and test
  send), and read endpoints for CRM data.
- **The Web Admin talks only to this API.** It never reaches `sales_core`, integrations, the Sheet,
  the store or secrets. `sales_core` never learns that a UI exists.
- Applying a saved configuration to the running agent follows
  [client-configuration-design.md](client-configuration-design.md) section 6.
- Deployment stays one process, one worker (single-writer guarantee); the first version of the Admin
  API is served by the same process as `sales_app` and the webhooks (approved). It is kept
  separable: its own router and services, reaching runtimes only through the registry and ports, so
  moving it to another process later needs a shared store and a new writer-safety rule, not a
  redesign.

## 5. One KPI source: Dashboard and CEO report

**Rule: there is exactly one KPI computation. The CEO WhatsApp report and the Web Dashboard both
call it. No second implementation, in `sales_app`, in a Brain, in the frontend or in a Sheet formula.**

```
SalesRepository  (prospects, actions, messages, audit)
      │
      ▼
sales_core.kpi     pure functions: (data, period, timezone) -> KpiReport   [future, in Core]
      │
      ├──► ReportService ──► CEO report: pick sections, render text/template, send via WhatsApp
      └──► Admin API ──────► Dashboard: JSON for the charts
```

- **Where it lives.** KPI definitions and computation belong in `sales_core` (generic, client
  agnostic, no I/O), because a "reply" or a "conversion" must mean the same thing in every
  presentation. It is a pure function of repository data, a period and a timezone.
- **What each consumer adds.** The CEO report only selects sections, and renders them (the Brain may
  contribute narrative wording). The Dashboard only serialises the same `KpiReport` and adds
  presentation. Neither recomputes, re-filters or re-defines a figure.
- **Periods.** Day, week, month and custom ranges are inputs of the same function. Day boundaries use
  the client's timezone, as the planner does.
- **Test that protects it (future).** A test feeds one dataset to both consumers and asserts the
  figures are identical; the report layer and the API layer may not import each other's calculations.
- **Dependency on the data model.** KPIs are computed from what the repository can return, so the
  data model decides which KPIs exist. Today's data supports messages sent/received, conversations,
  follow-ups, prospects by status, and handoffs. It has **no** representation of appointments,
  conversions, objections, feedback or non-conversion reasons; their fields and ownership are
  defined before KPI reporting is built (sections 8 and 10).

## 6. Storage and the CRM's future

The Core sees storage only through `SalesRepository` (prospects, actions, conversations, audit) and
configuration only through immutable objects that `sales_app` builds. The Google Sheets adapter is
one implementation. The Web Admin CRM module reads and writes through the same port, so it works
with the Sheet today and with any later store.

While Google Sheets is the CRM, the Sheet stays the source of truth for prospect data: the Web Admin
does not keep a copy that could go stale (see [sheet-schema.md](sheet-schema.md) section 0).

## 7. Migration path to PostgreSQL

`sales_core` and the Brains do not change, because they only see ports.

1. `PostgresConfigStore` behind `ConfigStore`; users and roles for the Web Admin.
2. Import existing configuration versions, keeping version numbers.
3. `PostgresSalesRepository` behind `SalesRepository`, selected per client through the `provider`
   field in wiring (a new builder in `sales_app/wiring/integrations.py` and a config edit).
4. One-time migration of a client's Sheet data into it; the KPI computation, the Dashboard and the
   CEO report keep working unchanged, since they read through the port.

PostgreSQL is optional and introduced only when a real need appears (scale, query needs, a client that
wants the Web Admin as its only CRM). Nothing in the MVP depends on it. A read-heavy Dashboard may
later justify a read model or cache inside the adapter, never in the Core.

## 8. Known gaps before the future modules can be built

Recorded so nobody assumes they exist. Resolutions are in section 10.

1. **`SalesRepository` cannot read the audit trail** (`append_audit_events` only). A read capability
   will be added eventually, when a task needs it (KPIs, activity history).
2. **No contracts for appointments, conversions, objections, feedback or non-conversion reasons.**
   They may be recorded by the Brain and/or a human. Exact fields, ownership and storage are defined
   in a design step before KPI reporting is implemented; nothing is modelled now. The Core lifecycle
   is `new, active, paused, opted_out, closed`; a conversion and a close reason are not modelled.
3. **No KPI or report code exists**, and the scheduler has no report trigger.
4. **Sheet reads are full-tab reads on every call** (by design). A Dashboard over long periods on a
   large Sheet is a known future scaling and quota concern. It is acknowledged and not addressed in
   the MVP.
5. **Brain settings schema, `ConfigStore` and `ContactPolicy`** are still proposals (see
   [client-configuration-design.md](client-configuration-design.md)).

## 9. Order of work (approved)

1. `ConfigStore` and the `config` CLI. **Done** (see
   [client-configuration-design.md](client-configuration-design.md) section F).
2. Brain settings schema.
3. `ContactPolicy` / guardrails.
4. CEO report, built on the shared KPI computation in `sales_core`. **Prerequisite, before any KPI
   code:** a short design step that defines the exact KPI fields, their ownership and their storage
   (section 10, decision 3).
5. Admin API: Configuration, then CRM and approvals, then Dashboard (the Dashboard reuses the KPI
   computation from step 4).
6. Web Admin frontend.
7. PostgreSQL only when a real need appears.

KPIs and the report do **not** move ahead of `ConfigStore`.

## 10. Decisions (approved)

1. **KPI computation lives in `sales_core`.** It is one pure, reusable computation used by both the
   CEO report and the Web Dashboard. There is no duplicated KPI logic anywhere else (section 5).
2. **Order of work** is the one in section 9.
3. **Business data for the MVP.** Appointments, conversions, objections, feedback and
   non-conversion reasons may be recorded by the Brain, by a human, or by both. No complex data
   model is invented or built now. Before any KPI reporting is implemented, a design step will
   define the exact fields and who owns each one (Brain, human or both) and where they are stored.
4. **Audit.** `SalesRepository` will eventually gain a read capability for audit and KPI needs. It
   is not built until a task requires it.
5. **Google Sheets quota and performance.** The scaling concern (full-tab reads, section 8 item 4)
   is acknowledged and deliberately **not solved now**. The MVP stays on Google Sheets.
6. **Admin API process.** In its first version the Admin API runs in the same single process as
   `sales_app` and the webhooks. The code is organised so it can be split into its own process
   later: the Admin API shares no in-memory state with the webhook or scheduler other than
   through the registry, the ports and the stores, and it is mounted as its own router.
