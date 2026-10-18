# Billing: confirming invoice payments

Revansa never collects money. An invoice becomes `PAID` in one of two ways, and the invoice says which
(`payment_origin`):

| Origin | Who/what | Recorded |
|---|---|---|
| `manual` | an owner Admin declares it (`PUT /admin/v1/billing/invoices/{id}/status`) | who, when, audit row |
| `electronic` | a signed payment notification from a provider, verified | transaction id, event id, when, audit row |

## Invoice lifecycle

- `PENDING` -> `PAID` (manual or electronic) or `FOR_FREE` (manual only).
- `PENDING` -> `FAILED` when a verified notification reports the payment failed. A `FAILED` invoice can
  still be paid later, electronically or by hand.
- `PAID` and `FOR_FREE` are final. An invoice paid electronically cannot be marked manually (409
  "this invoice was paid electronically and cannot be changed"). A genuine payment arriving for an
  invoice the Admin already settled is refused and traced (outcome `already_settled`); it changes
  nothing.
- **Overdue is computed, not stored**: an invoice still `PENDING` or `FAILED` 15 days (`DUE_DAYS`) after
  the end of its month. The API returns `due_at` and `overdue`; it never goes stale and needs no job.

## When billing starts

A client is billed from the day its plan was assigned (`plans.assign`, `started_at`): earlier days cost
nothing and the first invoice is prorated from that day. Generating a month
(`POST /admin/v1/billing/invoices/generate`) is idempotent: a second call creates nothing and reports
`already_invoiced`; the database also refuses a second invoice per client and period.

## The webhook

`POST /api/payments/webhook` is public (no Admin session); authenticity rests only on the signature.

Signed-webhook format (the generic adapter `SignedWebhookProvider`):

- Headers `X-Payment-Timestamp` (unix seconds) and `X-Payment-Signature` = hex
  `HMAC-SHA256(secret, "<timestamp>." + raw body)`. Compared in constant time on the raw bytes. A
  timestamp more than 5 minutes from the server clock is refused.
- Body (JSON, max 64 KB): `{"event_id", "invoice", "amount", "currency", "transaction_id", "status"}`
  - `event_id`: unique per notification (idempotency key); `invoice`: the invoice number, e.g.
    `INV-2026-04-alpha`; `amount`: decimal string (max 2 decimals); `currency`: ISO code, upper case;
    `status`: `succeeded` or `failed`.

Answers: `409 no payment provider configured` (secret not set), `401` (missing/wrong/stale signature,
nothing else said), `400` (authentic but malformed, or too large), `503` (database down: the provider
should retry), `200 {"status": "applied" | "duplicate" | "failed_recorded" | "rejected"}`.

Rules applied atomically: a `succeeded` event marks the invoice `PAID` only if currency and amount equal
the invoice's and the transaction id has not been used. The same `event_id` or `transaction_id` again is
a no-op. Anything else (amount/currency mismatch, unknown invoice, transaction already used by another
invoice, invoice already settled) is rejected, answered 200 (a retry cannot fix it) and traced.
`GET /admin/v1/billing/payment-events` lists every decision, rejected ones included, scoped to the
clients the Admin can reach (events with no known invoice: owners only).

## What to configure

- **Secret** (Admin, global scope, via the credentials API/Settings): `payments.webhook_secret`, 16-256
  printable characters. Storing it turns the webhook on; it is encrypted at rest, never returned, and
  read on each call (no restart). Removing it turns the webhook off (409).
- Needs the database (`SALES_AGENT_DATABASE_URL`), migration `0012_payments` (columns
  `payment_origin`, `payment_transaction_id` (unique), `payment_event_id` on `invoices`; table
  `payment_events`, primary key `event_id`).
- The deployment must be reachable over HTTPS at `/api/payments/webhook`.

## What a real provider still needs

No named payment company is integrated. To go live, someone has to:

1. Pick a provider (or a small relay) able to send this signed format, or write another
   `PaymentProvider` adapter for the provider's own signature scheme (`parse_webhook(body, headers,
   now)`); wire it in `bootstrap._payment_provider_resolver`.
2. Create the payment flow that gives the client something to pay: a payment link/checkout that carries
   the invoice number as the reference. Revansa has no checkout page and does not send invoices.
3. Handle refunds/chargebacks (not modelled: `PAID` is final) and partial payments (refused as an
   amount mismatch).
4. Alerting on rejected events (today they are only listed in `payment-events` and logged).
