# Finance contracts

What the Admin dashboards (lot 4) read. Code: `src/sales_app/finance/` (pure rules and contracts),
`src/sales_app/persistence/finance_rows.py` (the SQL sums), `src/sales_app/api/admin_finance.py`
(the route). Open questions (prorata, currency, VAT): `docs/open-decisions.md`.

## Rules

- **Billed** (`billed`): the sum of the invoices' `amount`, *before* any discount.
- **After discount** (`after_discount`): what is left to pay (`amount_after_discount`, or `amount`
  when there is no discount).
- **Collected** (`collected`): `PAID` invoices **only**. A `FOR_FREE` invoice and a `DISCOUNTED_100`
  invoice are decisions, never money received.
- **Free** (`free`): what was given free: a `FOR_FREE` invoice's whole amount plus the free amount of
  discounted invoices. **Discounted** (`discounted`): the sum of the discounts.
- **Pending** / **overdue**: `PENDING` and `FAILED` invoices, by their `after_discount` amount. They
  are overdue from `due_at` (the end of the invoice's month plus 15 days), computed against the
  clock at the time of the request; otherwise pending.
- **Currencies are never added.** Every total and timeline point is per currency.
- The **timeline** is by invoice period: for each month, what was billed for it and how much of that
  is collected (by the time of the request, not by payment date).
- A **subscription** is active at the end of a month if the client had a plan assignment in force at
  the end of that month's last day; inactive if it had been on a plan before but was not then.

## `GET /admin/v1/finance/summary`

Query: `from`, `to` (months `YYYY-MM`, inclusive, at most 60 months; default: the 12 months ending
with the last month that has ended; `to` alone means the 12 months ending with it), `client_id`
(optional). Admin session. An owner sees all clients; another role sees only its own clients, and a
`client_id` outside them is a 404. 422 for malformed or reversed months. 503 without a database.

```json
{
  "period_from": "2026-03", "period_to": "2026-04", "client_id": null,
  "generated_at": "2026-05-20T00:00:00Z",
  "totals": [
    { "currency": "USD",
      "billed": "105.00", "after_discount": "90.00", "collected": "30.00",
      "free": "45.00", "discounted": "15.00", "pending": "0.00", "overdue": "30.00",
      "invoices_issued": 4, "invoices_paid": 1, "invoices_free": 1,
      "invoices_discounted": 1, "invoices_pending": 0, "invoices_overdue": 1 }
  ],
  "timeline": [
    { "period": "2026-03", "currency": "USD", "billed": "60.00", "collected": "30.00" }
  ],
  "subscriptions": [
    { "period": "2026-03", "active": 2, "inactive": 0 },
    { "period": "2026-04", "active": 2, "inactive": 1 }
  ],
  "profitability": { "status": "not_connected", "costs": {}, "margin": {} }
}
```

Amounts are decimal strings. `totals` has one entry per currency that has invoices in the range.
`timeline` is ordered by period then currency; `subscriptions` has one point per month of the range.

### Profitability (extension point)

`profitability.status` is `"not_connected"` until a `CostSource` is given to `FinanceService`
(`sales_app.finance.service.CostSource.costs(period_from, period_to, client_id, client_ids) ->
{currency: Decimal}`). Once wired (to `provider_costs`, lot 2C), the status is `"available"`,
`costs` is the cost per currency and `margin` is `collected - costs`. No cost is estimated.

## Client contracts (read only, the session decides the client)

- `GET /client/v1/invoices` -> `{ "invoices": [ClientInvoice] }`, newest period first.
- `GET /client/v1/invoices/{invoice_id}` -> `ClientInvoice` + `lines` (`plan_name`, `monthly_price`,
  `currency`, `days`, `days_in_period`, `amount`). Another client's invoice and an unknown one are the
  same 404.
- `GET /client/v1/subscription` -> `status` (`active` | `no_plan`), `plan` (`plan_id`, `name`,
  `monthly_price`, `currency`, `billing_period`, `included_credits`, `daily_quota`, `since`),
  `next_billing_at`, `unpaid_invoices`, `overdue_invoices`, `last_payment` (the most recent invoice:
  `invoice_number`, `period`, `status`, `collected`, `settled_at`).

`ClientInvoice`: `invoice_id`, `number`, `period`, `currency`, `amount` (billed),
`amount_after_discount`, `discount_amount`, `free_amount`, `status`, `collected`, `due_at`,
`overdue`, `created_at`, `status_changed_at`. No internal trace (who settled it, provider ids) is
shown, and nothing here changes an invoice.

## Automatic invoicing

`sales_app.billing_service.AutoBilling` (run every hour by `BillingLoop` under the `platform` /
`billing` lease): invoices the closed months that are due and missing, from the month of each
client's first plan assignment, at most 12 months back. Idempotent and restart-safe (nothing is
remembered: what is missing is recomputed; the database refuses a second invoice per client and
period). Failures open a `billing_error` notification for the Admin (once per client and period while
open, closed when the invoice is made) and an audit entry (`billing.invoice.auto_generated`,
actor `system:billing`).
