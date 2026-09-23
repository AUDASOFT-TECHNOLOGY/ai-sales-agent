# AI Sales Agent Core

Generic, multi-client AI sales agent. Google Sheets is the prospect source, CRM and
operational store (MVP). OpenAI is the AI brain provider. WhatsApp Business Cloud API is
the channel (later phase).

The Core knows nothing about any specific client. Client-specific behaviour lives in
Business Brains (`brains/`) and client configuration (`clients/`).

## Layout

| Path | Purpose |
|---|---|
| `src/sales_core/` | Generic Core: contracts, ports, planner, engine, approval, safety |
| `src/sales_integrations/` | Adapters implementing Core ports (Google Sheets, OpenAI, WhatsApp, system clock, fakes) |
| `src/sales_app/` | Composition root: config loading, client registry, scheduler, webhook, CLI |
| `clients/` | Per-client configuration (data only, no secrets) |
| `brains/` | Business Brains, one independent package per client |
| `tests/` | Tests for the generic Core, including architecture rules |
| `docs/` | Architecture, Brain contract, Sheet schema |

See [docs/architecture.md](docs/architecture.md) for the dependency rules.

## Setup (uv)

```
uv sync
uv run pytest
```

## Running

```
uv run sales-agent validate          # check every client's configuration, start nothing
uv run sales-agent serve             # scheduler + webhook (one process, one worker)
uv run sales-agent run --client <id> --job dispatch --dry-run
```

Client configuration lives in `clients/<id>/client.yaml` (see `clients/_template/`); secrets are
environment variables named there (see `.env.example`). Clients ship `enabled: false`.
`SALES_AGENT_MODE_CEILING` (default `approval`) caps every client's mode.

See [docs/sales-app-design.md](docs/sales-app-design.md) and
[docs/engine-design.md](docs/engine-design.md).

Status: planner, engine, application layer and the Google Sheets adapter are implemented and
tested (Sheets against an in-memory stand-in and a mocked HTTP transport). The Business Brains are
not built yet. See [docs/sheet-schema.md](docs/sheet-schema.md) for the spreadsheet layout.
