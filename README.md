# AI Sales Agent Core

Generic, multi-client AI sales agent. Google Sheets is the prospect source, CRM and
operational store (MVP). OpenAI is the AI brain provider. WhatsApp Business Cloud API is
the channel (later phase).

The Core knows nothing about any specific client. Client-specific behaviour lives in
Business Brains (`brains/`) and client configuration (`clients/`).

## Layout

| Path | Purpose |
|---|---|
| `src/sales_core/` | Generic Core: contracts, ports, planner, engine, approval, safety, config |
| `src/sales_integrations/` | Adapters implementing Core ports (Sheets, OpenAI, WhatsApp, fakes) |
| `src/sales_app/` | Entry points: FastAPI app, CLI, composition root |
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

Status: scaffold only. No business logic implemented yet.
