# sales_app design note

Status: **implemented** in `src/sales_app/`. Section 10 records what the implementation decided and
what is still pending.

`sales_app` is the **composition and orchestration layer**. It loads configuration, builds one
Engine per client, decides *when* things run (scheduler) and *where* events go (webhook). It makes
**no commercial decision**: those belong to the Brain (strategy, wording, cadence) and the Core
(planner, engine, safety). If a rule would read differently for two clients, it does not belong
here.

```
                       clients/<id>/client.yaml            environment (secrets)
                                  │                               │
                                  ▼                               ▼
   ┌──────────────────────── sales_app ────────────────────────────────────────┐
   │  config loader ─► validator ─► ClientRegistry ──► ClientRuntime(client A)   │
   │  (fail fast)                    │                 ClientRuntime(client B)   │
   │                                 │                        │ each = own Engine, Sheet,
   │  Scheduler ─────────────────────┤                        │ channel, LLM, Brain instance
   │  (time triggers only)           │                                            │
   │  FastAPI: /webhooks/whatsapp ───┘  route by phone_number_id                  │
   │           /health                                                            │
   └───────────┬─────────────────────────────┬────────────────────────────────────┘
               ▼                             ▼
          sales_core                  sales_integrations              brains/*
   (planner, engine, safety,      (Sheets, OpenAI, WhatsApp,     (loaded by dotted path
    ports, contracts)              system clock, fakes)           from config, never imported)
```

## 1. Composition root

`sales_app/bootstrap.py::build_application(settings)` is the **only** place where Core ports meet
implementations. Dependency direction (enforced by `tests/architecture`):

- `sales_core` imports nothing from `sales_app`.
- `sales_integrations` imports nothing from `sales_app`.
- `sales_app` imports Core, integrations, and **never a brain package statically**. A Brain is
  resolved from the configured dotted path with `importlib`, so the app has no compile-time
  knowledge of any client or Brain.
- `sales_app` may not use commercial vocabulary from the Core (`DecisionType`, `BrainDecision`,
  `ProspectStatus`, `SalesActionType`, `ActionStatus`, `MessageDraft`, ...). A test scans for it, so
  a commercial decision cannot creep into the app layer unnoticed. Another test bans client names
  from `sales_app` and `sales_core`, and vendor SDK or storage imports from `sales_app`.

Layout:

```
sales_app/
  settings.py          process-level settings from env (clients dir, log level, mode ceiling...)
  config/
    schema.py          file schema (pydantic, unknown keys rejected)
    loader.py          discover, parse, validate; collects ALL errors
    secrets.py         resolve env vars by name; redaction
  wiring/
    brains.py          importlib entrypoint -> Brain, protocol-checked
    integrations.py    builder table: provider name -> Core port implementation
    runtime.py         ClientRuntime + build_runtime(config) -> Engine
  registry.py          ClientRegistry
  scheduler.py         in-process time triggers
  api/app.py           FastAPI factory + lifespan, webhook, health
  logs.py              client-aware JSON logging
  cli.py               validate | serve | run
  lockfile.py          single-process guard
  bootstrap.py         composition root
```

The old stubs `sales_core/config/` and `sales_core/registry.py` would have had to import
integrations and brains, which the Core must not do; they were removed.

## 2. Client configuration

One directory per client: `clients/<client_id>/client.yaml`. Directories starting with `_`
(`_template`) are ignored. Secrets are never in the file, only the **names** of environment
variables:

```yaml
client_id: alpha                # must equal the directory name
display_name: Alpha
enabled: true                   # false = fully skipped (placeholders, paused clients)
mode: approval                  # dry_run | approval | autonomous   (default: dry_run)
timezone: Africa/Tunis          # IANA; used by the planner's day boundary and the scheduler
daily_action_limit: 20
max_attempts: 3
opt_out_keywords: ["stop"]      # the client adds its own words; the Core hard-codes none

brain:
  factory: package.module:create_brain   # module:callable, called as (llm, settings)
  settings: {}                           # opaque, passed to the Brain as ClientContext.settings

sheets:   { provider: google_sheets, spreadsheet_id: ..., prospects_tab: ..., actions_tab: ...,
            messages_tab: ..., log_tab: ..., column_mapping: {...},
            credentials_env: GOOGLE_APPLICATION_CREDENTIALS }
llm:      { provider: openai, api_key_env: OPENAI_API_KEY, model: ... }
whatsapp: { provider: whatsapp, token_env: ..., phone_number_id_env: ..., app_secret_env: ... }
schedule: { daily_at: "09:00", days: [mon, tue, wed, thu, fri, sat],
            prepare_every_minutes: 60, dispatch_every_minutes: 5, recover_every_minutes: 15 }
```

- **Isolation.** Each client's config, credentials, spreadsheet, WhatsApp number and Brain settings
  live under its own key; nothing is inherited from another client.
- **Brain identification.** `brain.factory` names a callable `(llm: LLMProvider, settings) -> Brain`
  (`BrainFactory` in `sales_core.ports`). The app calls it once per client with that client's own
  `LLMProvider`. The Brain owns prompts, language and cadence; the app never reads them.
- **Validation at startup**, collecting every error before reporting:
  1. YAML parses; schema is valid; **unknown keys are rejected**; placeholders (`TODO`,
     `CHANGE_ME`) are refused for enabled clients; `daily_at` must be a quoted `"HH:MM"`.
  2. `client_id` equals the directory name.
  3. Values are sane: IANA timezone, limits `>= 1`, known `mode`.
  4. Cross-client uniqueness: WhatsApp `phone_number_id`, spreadsheet id, and the WhatsApp token and
     phone-number variable names are not shared. The LLM key and the Meta app secret may be shared.
  5. Required secrets exist in the environment (WhatsApp secrets are not needed for a `dry_run`
     client, judged on its *effective* mode).
  6. The Brain imports, its factory runs, and the result satisfies the `Brain` protocol.

Since the ConfigStore block, the client-editable part of this file (everything except the operator
wiring) can instead come from a versioned store; see
[client-configuration-design.md](client-configuration-design.md) section F. `client.yaml` as
described here remains valid, and remains the source for a client with no published version.

## 3. Client registry

`ClientRegistry` owns `client_id -> ClientRuntime`. A `ClientRuntime` holds the `Engine`, the config,
health bookkeeping and in-memory schedule state. `build_runtime(config)` performs the wiring for one
client:

1. Build the repository, the `LLMProvider` and the `MessagingChannel` from the builder table
   (`DisabledChannel` in `dry_run`).
2. Load the Brain with the LLM and the client's settings.
3. Construct `Engine(client=ClientContext, repository, brain, channel, clock, EngineConfig)`.

The registry adds lookups by `client_id` and by `(channel provider, routing key)` for the webhook.
**It contains no client-specific logic.** Integrations are chosen by the `provider` field through
`wiring/integrations.py`, so replacing Google Sheets later is a new builder and a config edit.

Isolation between clients: separate adapter instances, credentials, Engine (each with its own
lock) and Brain instance; the only shared things are the process, the event loop, the scheduler and
the logger.

## 4. Scheduler

An in-process loop (`asyncio`, no new dependency) that wakes every ~30 s and asks, per client, "is a
trigger due?". It holds **time-of-day logic only**; every business decision stays in the Engine, the
Planner and the Brain. Engine calls are synchronous, so they run on worker threads, one per client
at a time.

| Job | Trigger (client timezone) | Calls |
|---|---|---|
| Daily cycle | once per local day at `daily_at` on the scheduled weekdays; also as **catch-up** if the app starts later that day | `recover_pending_replies()` → `prepare()` → `dispatch()` |
| Planning pass | every `prepare_every_minutes` after the daily cycle, on scheduled weekdays | `prepare()` (picks up new prospects and newly due follow-ups) |
| Dispatch | every `dispatch_every_minutes`, on scheduled weekdays from `daily_at` | `dispatch()` (what humans approved in the Sheet) |
| Recovery | every `recover_every_minutes`, every day | `recover_pending_replies()` |

- **Default schedule:** 09:00 client time, Monday to Saturday (Sunday off), configurable per client.
- **Restart-safe without a database.** Jobs are idempotent by construction, so scheduler state is
  only in memory; after a restart the daily cycle may run once more, which the Core makes harmless.
- **No overlap** per client; clients are offset by a few seconds.
- **Mode-agnostic:** in `dry_run` the Engine turns `dispatch` and `recover` into no-ops.
- **Testable clock:** the loop calls `tick(now)`; tests drive it with fake time and never sleep.

## 5. WhatsApp webhook

Thin by design. `GET /webhooks/{provider}` answers the provider's subscription handshake. `POST`:

1. Read the **raw body** and hand it, with the headers, to the channel adapter's `WebhookParser`,
   which verifies the signature and returns `(routing_key, InboundMessage)` events. The app never
   sees the provider's payload shape.
2. **Identify the client:** `registry.by_routing_key(provider, routing_key)`.
3. `engine.receive_inbound(message)` (fast path: find prospect, de-duplicate, persist).
4. Queue `engine.process_inbound(receipt)` as a background task, then return **200**.

| Situation | Response |
|---|---|
| bad or missing signature | 401 |
| malformed payload | 400 |
| unknown routing key (no client owns that number) | 200 + ERROR log |
| status-only / non-message events | 200, ignored |
| `receive_inbound` raises (e.g. Sheets down) | 500, so the provider redelivers; the Engine de-duplicates |
| background processing crashes | logged with the client id, client marked `degraded`; `recover_pending_replies()` picks it up |

No Brain call, no prospect logic and no approval logic runs in the request path. Deployment
invariant: **one process, one uvicorn worker** (the Engine's single-writer guarantee is an
in-process lock).

## 6. Approval modes

`mode` in `client.yaml` maps directly onto `ApprovalMode` and into `EngineConfig`; the Core
interprets it.

| `mode` | Behaviour (implemented in the Core) |
|---|---|
| `dry_run` | Brain is called, only audit events are written, nothing is persisted or sent. **Default when unset.** |
| `approval` | proposals wait as `PROPOSED` until a human approves in the Sheet |
| `autonomous` | proposals are approved automatically |

Two guards live in the app layer because they are deployment concerns:

- **Mode ceiling.** `SALES_AGENT_MODE_CEILING` (default `approval`) clamps every client: effective
  mode = the safer of the client's mode and the ceiling (`dry_run < approval < autonomous`).
  Autonomy needs a deployment change, not just a YAML edit. `run --dry-run` can only lower it.
- **Loud reporting.** The effective mode of every client is logged at startup and shown on
  `/health/clients`, with any clamping marked.

## 7. Errors and startup

**Fail fast.** `serve` validates everything (section 2) before opening a port, prints every problem
at once and exits non-zero. `sales-agent validate` runs the same checks without starting anything.
An exclusive OS-level lock file refuses a second process (for example a manual `run` while the
server is up), protecting the single-writer rule without a new dependency and without stale locks.

**Isolation at runtime.** Every scheduler job and webhook task runs inside its client's own
`try/except`. A failure marks only that client `degraded` and is retried after a backoff; the
process and all other clients continue.

**Surfacing errors.** Two separate records, on purpose:
- **Business audit** (Sheet `Log` tab): written by the Engine.
- **Operational logs** (stdlib `logging`, JSON lines on stdout): every record carries `client_id`;
  `RunReport.errors` are logged at ERROR; secrets are never logged.

`GET /health` (liveness) and `GET /health/clients` (per client: effective and configured mode,
whether it was clamped, status, last run times, last error, consecutive failures; no secrets).
Shutdown is graceful: stop scheduling, let running jobs finish (bounded), rely on the `SENDING`
guard for anything that cannot.

## 8. Testing strategy (`tests/app/`, `tests/integration/`)

Only **wiring, scheduling and routing** are tested at the app level; commercial behaviour stays in
the Core, Engine and Brain tests.

- **Config:** valid load, each validation failure, aggregation across clients, template and
  disabled clients, mode ceiling, secrets redaction, the shipped client files.
- **Wiring / registry / bootstrap:** Brain loader failure modes, per-client runtimes from fakes,
  isolation between two clients, aggregated startup errors, strict startup.
- **Scheduler:** driven by `tick(now)`: daily trigger in the client's timezone, once per day,
  catch-up, Sunday off, intervals, no overlap, backoff, failure isolation, "only triggers the Engine".
- **API:** handshake, signatures, routing between two clients, unknown number, malformed body,
  500 on storage failure with recovery, duplicate delivery, background crash, health without secrets.
- **CLI / lock / settings / logging:** exit codes, second instance refused, one uvicorn worker.
- **Adapters:** WhatsApp parser and channel (mock transport, including how outcomes drive the
  Engine's duplicate-send guard), OpenAI provider (stub SDK), system clock, disabled channel.
- **Architecture:** no brain imports, no vendor or storage imports, no commercial vocabulary, no
  client names in `sales_app` or `sales_core`, scheduler and webhook reach the Core only through
  the Engine and ports.

## 9. Pieces outside `sales_app` this design needed (all built)

`BrainFactory` (ports), `InboundEvent` (contracts), `WebhookParser` and its two errors (ports),
`SystemClock`, `DisabledChannel`, `OpenAIProvider`, `WhatsAppChannel`, `WhatsAppWebhookParser`;
removal of the two Core stubs; migration of `clients/*/client.yaml` to the shape in section 2.
No new third-party dependencies.

## 10. Implementation notes

**Command line:** `sales-agent validate | serve | run --client X --job daily|dispatch|recover
[--dry-run]`. Exit codes: 0 ok, 1 job failed, 2 invalid configuration, 3 another instance running.

**Scheduling rules the code fixes** (schedule values are configurable per client):
- Outbound work (the daily cycle and dispatching approved actions) only happens on the scheduled
  weekdays, from `daily_at` on. Recovering stored replies runs every day.
- A failing job backs the client off 30 s, doubling to a 15 min cap, and marks it `degraded`.
  Problems the Engine merely reports (`RunReport.errors`) are logged and shown in `/health/clients`
  but do not count as failures.

**WhatsApp channel and the duplicate-send guard.** WhatsApp has no idempotency key, so the adapter
classifies outcomes for the Engine: a request that certainly did not go out (refused connection, 429,
5xx, expired credentials) is a *transient* failure; a rejection of this message or recipient is
*permanent*; a timeout or reset after the request was sent is **raised**, leaving the action in
`SENDING` for a human. Credential problems are reported as transient so an expired token cannot
pause prospects.

**Google Sheets:** the `SalesRepository` adapter is built (`sales_integrations/google_sheets`,
layout in [sheet-schema.md](sheet-schema.md)) and is what the `google_sheets` provider builds. The
service-account key path comes from the variable named by `sheets.credentials_env`.

**Planning passes during the day.** The daily cycle is followed by further `prepare()` passes every
`schedule.prepare_every_minutes` (default 60, between `daily_at` and the end of the day, on working
days). The Planner is idempotent and quota-aware, so a pass only plans what is newly eligible: a
prospect added to the sheet after the morning cycle, or a follow-up or callback that fell due since.
Set it to 1440 to plan once a day.

**Still pending:**
- `validate --online` (checking the real sheet with `GoogleSheetsRepository.verify()`).
- The Tounesimmo scaffold Brain does not yet expose the `create_brain` factory named in its disabled
  client file (Naga's does).
