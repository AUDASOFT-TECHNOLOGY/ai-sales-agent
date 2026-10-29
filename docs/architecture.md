# Architecture

## Dependency rules

```
sales_app ──► sales_core ◄── sales_integrations
                 ▲  ▲
                 │  └── sales_brain_kit ◄── brains/*
                 └────── brains/*
```

- `sales_core` imports nothing from `sales_integrations`, `sales_app` or any brain.
  It defines contracts (data models) and ports (interfaces).
- `sales_integrations` implements Core ports. It knows nothing about clients or brains.
- Brains import only `sales_core` (contracts and port types), and may import `sales_brain_kit`. They contain no vendor code;
  the Core injects the `LLMProvider`. The generic `consultative` engine (`sales_brain_kit.generic`) is such a Brain.
- `sales_app` is the composition root, the only place where everything is wired together.
- Brains never import each other.
- `sales_brain_kit` is the shared, client-agnostic building block for Brains: the settings schema
  and `BrainSettingsSpec` of the "consultative sales" family and the generic Brain that runs on
  them (`sales_brain_kit/consultative`, see [brain-kit.md](brain-kit.md)). It imports only
  `sales_core` (contracts and ports only, for its runtime) and pydantic, knows no client, and is never imported by `sales_core`, `sales_integrations` or
  `sales_app` (which finds a Brain's `SETTINGS_SPEC` with `importlib`). Brains may import it.
  See [brain-settings.md](brain-settings.md). Markets, prices per place, payment and conversion
  workflows are generic capabilities of the kit driven by client configuration
  ([brain-commerce.md](brain-commerce.md)); they do not change the Core. The two generic Core
  capabilities they rely on (facts a Brain proposes, and the payment model in which only a person
  authorised by the client confirms a payment) are in
  [core-facts-and-payments.md](core-facts-and-payments.md). The Core names no payment provider.

How the agent learns from the results of its conversations (neutral traces and results in the Core,
what counts as good practice in the kit) is in [learning-from-results.md](learning-from-results.md).

`tests/architecture/` enforces these rules.

## Layers and the future Web Admin

| Layer | Role |
|---|---|
| `sales_core` | generic contracts, ports, planner, engine, safety (and, later, the KPI computation) |
| `brains/*` | client-specific strategy, wording, cadence |
| `sales_brain_kit` | shared Brain family: settings schema and validation, and the generic consultative sales Brain (catalog, discovery, strategy, follow-up cadence, prompt, validation) |
| `sales_integrations` | adapters: Google Sheets, OpenAI, WhatsApp (later possibly PostgreSQL) |
| `sales_app` | composition root, scheduler, webhook (and, later, the Admin API) |
| Web Admin (future) | browser UI; talks **only** to the Admin API in `sales_app` |

MVP: Google Sheets is the operational CRM; there is no UI, no Admin API and no database. Future: the
Web Admin (Configuration, CRM, Dashboard) becomes the official interface, the CEO WhatsApp report
and the Dashboard share one KPI computation, and PostgreSQL may replace the Sheet behind the same
ports without touching `sales_core`. See [web-admin-product-design.md](web-admin-product-design.md).

Accounts, sessions and versioned client settings can live in a SQL database (PostgreSQL in
production) through `sales_app/persistence/`, the **only** package of `sales_app` allowed to import a
SQL library. It implements the existing ports (`AdminUserStore`, `AdminSessionStore`,
`ClientProfileStore`, `ConfigStore`); nothing else knows whether data is in files or in SQL. Credentials
stay in the `SecretStore`, never in the database. See [persistence.md](persistence.md).

Client settings are stored as versioned JSON through the `ConfigStore` port in `sales_app`
(`sales_app/config_store/`); `sales_core` never sees it. Operator wiring and secrets stay in
`client.yaml` and the environment. See [client-configuration-design.md](client-configuration-design.md)
section F.

## MVP data store

Google Sheets is the operational store. Consequences the design must respect:

1. No transactions: write `queued/sending` before sending, `sent` after (idempotency).
2. Single writer per client at a time.
3. Batch all reads and writes (API quotas).
4. A Log tab records every decision and send.
5. Validate and map columns at read time (humans edit the Sheet).

## Modes

- `approval`: proposed actions are written to the Actions tab; only approved rows are sent.
- `autonomous`: approved automatically (later phase).
- `dry_run`: nothing is sent; only the log is written.
