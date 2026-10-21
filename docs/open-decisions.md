# Open decisions

Questions that are **not decided**. The code does not invent an answer: it keeps the current
behaviour behind a replaceable seam and says so here. Each entry names the seam, so that deciding it
is a change in one place.

## Billing (lot 3)

### 1. Billing cycle and plan change (DECIDED, no longer open)

**Decided by the Product Owner.** There is **no prorata**. The start date is the client's plan
assignment. An invoice is issued **at once** when the plan is assigned, dated that day, at the plan's
price; the next one is issued automatically on the **same day of the following month** (assigned on
2026-10-20: invoices dated 2026-10-20, 2026-11-20, 2026-12-20, ...). When the day does not exist in a
month (31 in April, 29 to 31 in February) the invoice falls on the month's last day, and the original
anchor day is kept for the later months (31 Jan, 28 Feb, 31 Mar, 30 Apr). The amount is always the
plan's price, whatever the day.

**Plan change.** A new plan assignment is a **new anchor**: an invoice is issued immediately at the
new plan's price, dated the assignment day, and the cycles of the previous plan stop (nothing is
credited or refunded for the unused part of the previous cycle). Taking a client off its plan stops
all invoicing. Only the current assignment is billed.

**Known edge.** An invoice is unique per client and date: a plan changed on a day that already has an
invoice for that client (for example a second assignment the very same day) does not get a second one
for that date.

**Where.** `sales_app.billing` (`cycle_dates`, `add_months`, `build_invoice`), `sales_app.billing_service`
(`AutoBilling.run`, `issue_for_client`). `DailyProrata` and `ProrationPolicy` no longer exist. Invoices
of the former calendar-month rule keep their `YYYY-MM` period and stay readable. Annual plans are
still not invoiced (see 4).

### 2. Currency

**Question.** One currency for the whole platform, or one per plan? What happens when a client changes
to a plan in another currency in the middle of a month (a plan change now starts a new cycle in the new plan's currency)? Is there a conversion for the dashboards?

**Today.** Each plan has its own currency; the finance figures are given per currency and never added
across currencies (`docs/finance-contracts.md`).

### 3. VAT and taxes

**Question.** Are prices tax-inclusive? Which rate, per country? Is the tax a line of the invoice?

**Today.** None: an invoice's `amount` is the plan price, with no tax line, and the finance figures
have no tax field. Nothing in the code assumes a rate.

**Seam.** Not built yet. It will be a separate step after `build_invoice` (lines -> taxed lines);
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

## Channels and integrations

### 7. WhatsApp provider: Meta Cloud API direct, Twilio or Bird

**Decision to take.** The specification (§8.4) plans Twilio or Bird; the code talks to Meta's Cloud API
directly (Embedded Signup, `sales_integrations/whatsapp/graph.py`).

**Options.** Keep Meta direct (no intermediary cost, Revansa owns the Meta app review and the webhooks);
Twilio or Bird (their onboarding, their coexistence support, their per-message fee). Criteria from the
specification: account compatibility, coexistence with the phone app, several numbers per client, costs,
partnership terms.

**Can continue without it.** Everything except the provider adapter: templates, 24-hour window,
delivery statuses, consent, costs (`meta_whatsapp / message_sent` tariff is an Admin parameter).

**Stays configurable.** The channel is behind `MessagingChannel`; a Twilio or Bird adapter would be a
new module beside `whatsapp/channel.py`. Not built, because the choice is open.

### 8. Telegram method

**Decision to take.** Validate the user-account method (MTProto, `telegram/telethon_client.py`, with the
consent gate) with the Product Owner and test it on a real account before calling it operational (§8.3).

### 9. WhatsApp and Telegram in the plan quota

**Question.** Only e-mail is metered against the plan's daily quota today. Counting WhatsApp or Telegram
sends needs a cap for every client that uses them.

### 10. Conversions and credits

**Question.** No conversion event is recorded, so the dashboards show conversions as "not tracked". What
counts as a conversion (a confirmed payment, a booked appointment, a source status)? Likewise there is
no rule that converts a message into plan credits, so credits consumed are "unavailable".

## Platform

### 11. Data retention, export and deletion (§17.2)
No policy. Only the Meta data-deletion webhook exists.

### 12. Several users per client (§3.4)
Not built. A client has one login. Needs a users table linked to the client with roles; isolation by
client id must stay the only boundary.

### 13. Consent and compliance per market (§17.2)
The consent registry exists; the rules that apply per country are not specified.

### 14. Self-service SaaS (lot 8, §13.5)
Landing page, sign-up, plan choice, online subscription payment and guided onboarding are **not**
implemented in this mission: the specification lists them as priority 4 and does not specify them
enough (payment provider, plans offer, legal pages).
