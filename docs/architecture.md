# Architecture

## Dependency rules

```
sales_app ──► sales_core ◄── sales_integrations
                 ▲
                 └────── brains/*
```

- `sales_core` imports nothing from `sales_integrations`, `sales_app` or any brain.
  It defines contracts (data models) and ports (interfaces).
- `sales_integrations` implements Core ports. It knows nothing about clients or brains.
- Brains import only `sales_core` (contracts and port types). They contain no vendor code;
  the Core injects the `LLMProvider`.
- `sales_app` is the composition root, the only place where everything is wired together.
- Brains never import each other.

`tests/architecture/` enforces these rules.

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
