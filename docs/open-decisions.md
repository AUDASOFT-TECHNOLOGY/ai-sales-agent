# Open decisions

Questions that are **not decided**. The code does not invent an answer: it keeps the current
behaviour behind a replaceable seam and says so here. Each entry names the seam, so that deciding it
is a change in one place.

## Billing (lot 3)

### 1. Prorata of a partial period

**Question.** A client joins, leaves or changes plan in the middle of a month: is it charged by the
day, by the half month, a full month from the day it joins, or free until the first full month?

**Today.** Daily prorata: `price x days / days in the month`, rounded to the cent per line, from the
day the plan was assigned. This is the behaviour that existed before the lot; it is kept, not
endorsed.

**Seam.** `sales_app.billing.ProrationPolicy` (`lines(assignments, start, end) -> list[InvoiceLine]`);
the implementation in force is `DailyProrata` (`DEFAULT_PRORATION`). `build_invoice(...,
proration=...)` and `AutoBilling(..., proration=...)` take another policy. Invoices already kept carry
their own lines and never change.

### 2. Currency

**Question.** One currency for the whole platform, or one per plan? What happens when a client changes
to a plan in another currency in the middle of a month (today: the client is reported, not invoiced,
and a `BILLING_ERROR` notification asks the Admin to act)? Is there a conversion for the dashboards?

**Today.** Each plan has its own currency; the finance figures are given per currency and never added
across currencies (`docs/finance-contracts.md`).

### 3. VAT and taxes

**Question.** Are prices tax-inclusive? Which rate, per country? Is the tax a line of the invoice?

**Today.** None: an invoice's `amount` is the plan price, with no tax line, and the finance figures
have no tax field. Nothing in the code assumes a rate.

**Seam.** Not built yet. It will be a separate step after `ProrationPolicy` (lines -> taxed lines);
the invoice lines are already a copy, so adding a tax column is additive.

### 4. Annual billing terms

**Question.** For an annual plan: is the invoice issued at the start or at the end of the year? Is
the price paid in one go or spread? How is a mid-year plan change settled? What is the invoice's
`period` (it is a month today, `YYYY-MM`, unique per client)?

**Today.** `plans.billing_period` ("monthly" by default, "annual" accepted) is stored. The automatic
billing job **does not invoice** a client for any month in which it was on an annual plan: it reports
it as skipped. The unique key `(client_id, period)` of the invoices already refuses a second invoice
for the same client and period whatever their meaning, so an annual invoice keyed by its starting
month fits it; whether that is the right key is part of this decision.

### 5. Billing history window

**Question.** How far back does the automatic job invoice a client that was never invoiced (a plan
assigned long ago, a database restored)?

**Today.** At most the last 12 closed months (`billing_service.DEFAULT_LOOKBACK_MONTHS`); older months
can still be invoiced by hand (`POST /admin/v1/billing/invoices/generate`).

### 6. Profitability

**Question.** Which costs count against revenue (model calls, channels, a share of the
infrastructure), and in which currency?

**Today.** The finance summary exposes `profitability.status = "not_connected"`. The extension point
is `sales_app.finance.service.CostSource`; it will be wired to the provider costs of lot 2C. No cost
is estimated.
