# Costs by provider and quotas

## Basis of every figure

Every cost line and every aggregate says how it is known. Nothing is ever invented.

| basis         | meaning                                                                                              |
|---------------|------------------------------------------------------------------------------------------------------|
| `measured`    | counted and priced from data Revansa holds (LLM tokens recorded x the price list), or typed in by the Admin from an invoice |
| `estimated`   | approximate or partial: a flat per-message WhatsApp tariff, an amount the Admin estimated, an LLM amount where some calls carry no token count (a lower bound) |
| `unavailable` | no amount: a price the Admin has not provided, or an unknown amount. Never shown as 0               |

Amounts are decimal strings. Currencies are never added together (`totals` is per currency).
An aggregate is `measured` only if all its lines are; `unavailable` if none has an amount; otherwise `estimated`.
`complete: false` means at least one line is unavailable, so the totals are a lower bound.

## Prices the Admin must provide

None is built in. Until provided, the corresponding cost is `unavailable`.

| Provider        | Where to set it                                              | What to provide |
|-----------------|--------------------------------------------------------------|-----------------|
| OpenAI (LLM)    | `PUT /admin/v1/settings/llm/pricing` (existing)              | price of 1M input and 1M output tokens, per model, with currency |
| Meta / WhatsApp | `PUT /admin/v1/settings/costs/rates` with `provider=meta_whatsapp`, `key=message_sent` | price of one sent WhatsApp message, with currency. Meta bills by conversation/template category; a flat tariff only approximates it, so the line is `estimated`. Quantity = messages Meta accepted (status sent/delivered/read, not failed) in the period |
| Google, OVH, infra, other | `POST /admin/v1/costs/entries`                      | the amount (and optionally quantity, unit, unit price) per period, with currency and basis (`measured` if read off an invoice, `estimated`, or `unavailable`) |

`GET /admin/v1/settings/costs/rates` lists the tariffs and a `missing` list of the ones the engine reads.

## API

- `GET /admin/v1/costs?period=YYYY-MM|YYYY-MM-DD[&client_id=]` (owner for all clients; scoped admins with `client_id`): per provider basis, totals and lines.
- `CostService.costs_by_provider(period, client_id=None) -> CostReport` is the service the dashboard uses.
- `POST /admin/v1/costs/materialize` keeps the computed lines of a period in `provider_costs` (idempotent).
- Rates and entries changes are owner-only and recorded in the audit log.

## Quotas

The effective daily limit is `min(Admin send cap, plan daily quota)`; a send reserves a place atomically per client, channel and UTC day (`send_cap`). At the limit the send is refused as a deferred, un-counted try: the action stays approved with the reason visible and no attempt is burnt, so nothing loops.

- `GET /client/v1/usage` (the client's own) and `GET /admin/v1/clients/{id}/quota`: today's and this month's real consumption, limit, percent, `state` (`ok`, `warning` from 80%, `reached`, `not_configured`, `unavailable`), a `message` and the `action` to take.
- Alerts: when 80% and 100% are crossed, one `quota_reached` notification for the client and one for the Admin, with `dedupe_key = quota:<channel>:<UTC day>:<threshold>` (per client through the notification scope).
- `included_credits` is shown as the plan's own term. Credits consumed are `unavailable`: no credit-per-message rule is defined. Provide the rule to compute it.
- Only e-mail is metered today (the Admin cap and the plan quota bound it); WhatsApp and Telegram sends are not counted against the plan quota.
