# Dashboards (lot 4)

Two read-only endpoints assemble figures that earlier lots already compute. Nothing is simulated,
estimated by the dashboard, or written. Code: `src/sales_app/dashboards/` (pure rules),
`api/admin_dashboard.py`, `api/client_dashboard.py`, `api/dashboard_support.py`. No migration.

## Availability of every indicator

An indicator is `{value, availability, reason?}`:

| availability  | meaning |
|---------------|---------|
| `measured`    | counted or read from data Revansa holds. An empty tracked count is a real `0`. |
| `estimated`   | approximate or partial (a flat tariff, a lower bound, notifications truncated). |
| `not_tracked` | not recorded, or not readable right now: `value` is `null`, `reason` says why. Never shown as 0. |

Conversions are two measured figures: `conversions_declared` (the prospect says they paid) and `conversions_confirmed` (the payment is confirmed); a declaration is never counted as confirmed. Their rates are over the prospects contacted. Per product / circuit / channel / source / day breakdowns of conversions are not computed (payment facts carry no lane/channel in the metrics reader). A sum where one term is
`not_tracked` is `not_tracked`. Costs map the lot 2C basis `unavailable` to `not_tracked`. A store
that is missing (no database) or unreadable degrades its block to `not_tracked`; the response stays 200.

## `GET /admin/v1/dashboard?from&to&client_id`

`from` / `to`: `YYYY-MM` or `YYYY-MM-DD`, inclusive, at most 24 months (422 otherwise). Default: the
current month and the five before it. Daily series up to 92 days, monthly beyond.
Scope: an owner sees all clients; another role only the clients it can reach; a `client_id` outside
them (or unknown) is a 404. Response is `Cache-Control: no-store`.

Blocks: `clients` (total/active/inactive), `llm_models`, `agents`, `activity` (lot 2B metrics summed
over the clients in scope: prospects, contacted, conversations, messages, replies, reply rate,
needs_human, failed actions, payments declared/confirmed, groupings), `finance` (lot 3
`/finance/summary`: billed, after discount, collected = PAID only, free, discounted, pending, overdue,
invoice counts, timeline, subscriptions; currencies never added), `costs` (lot 2C per provider with
basis, completeness, monthly series), `profitability`, `usage` (plan quota per client), `reliability`
(open notifications by kind/severity, sync failures, degraded runtimes), `integrations` (runtime
readiness per client), and `series`: `activity`, `performance`, `finance`, `subscriptions`, `costs`,
`reliability`.

Rules:

- **Performance**: reply rate = prospects who replied / prospects contacted, computed month by month
  (distinct prospects, so months are never summed); can exceed 100%.
- **Costs** are counted by whole calendar month (UTC), whereas activity uses the exact days.
- **Profitability** compares collected revenue with the technical costs of the *closed* months of the
  range. `available` (margin per currency, with `basis` `measured` or `estimated`) only if no cost line
  lacks an amount and no cost is in a currency without revenue (no conversion); `partial` (known costs
  as a lower bound, no margin); `unavailable` (no cost with an amount). Absence of a cost record is
  never read as zero cost.
- **Incident series**: warning/critical notifications by creation date (at most the 2000 most recent
  are read; beyond that the figure is `estimated`).

## `GET /client/v1/dashboard?from&to`

No client id is accepted: the session decides it (a `client_id` parameter is ignored), every store is
asked for that client only. Blocks: `agent` (state and the client-safe readiness sentences),
`activity` (prospects, contacted, conversations, replies, rate, conversions declared + confirmed and their rates, needs_human,
errors, by product / circuit / source / channel / status, payments declared vs confirmed vs awaiting),
`human_requests`, `incidents` (open problems, connection problems), `usage` (quota, no Admin cap),
`billing` (subscription, invoice counts, recent invoices) and `series` (`activity`, `performance`,
`incidents`). No cost, token, provider or margin is ever part of it.

## Front end

- Admin: `web/src/admin/pages/AdminDashboardPage.tsx` keeps every existing indicator (Clients, LLM
  models, Active agents, Total revenue, Prospects contacted, Conversations, Conversions declared, Conversions confirmed, Revenue, LLM
  cost, WhatsApp cost; now fed by the endpoint, "—" with the reason when not tracked) and adds, in
  `AdminDashboardInsights.tsx`, the six chart families with period and client filters. Billed,
  collected, free, discounted and costs have their own colours and columns.
- Client: `web/src/client/dashboard/ClientDashboardPage.tsx` on `/dashboard` (texts in
  `dashboardText.ts`, French and English).
- Charts: `web/src/dashboard/TimeChart.tsx`, hand-made SVG (no chart library). Colour-blind safe
  palette, legend always present, markers/dashes besides colour, native tooltips, text alternative,
  data table fallback, gaps for unknown values, loading/error/empty states.

## Known limits

- Platform-level integration statuses (email service, Gmail, HubSpot platform apps) are not in the
  dashboard; it reports per-client runtime readiness and degraded runtimes.
- Activity is read client by client (one query set per client and, for performance, per month); fine
  for tens of clients, to be revisited for hundreds.
- The Client menu entry for `/dashboard` has to be added in `shell/AppShell.tsx`.

## Failure notifications and audit (spec 14, 12.4)

- `sales_app/operational_signals.py` wraps each client's channels (`NotifyingChannel`) and model (`NotifyingLLMProvider`) and the scheduler's inbox job (`job_result_hook`): a failed send (Email / Telegram / WhatsApp, transient or permanent), an unreadable inbox, a provider quota / credential / model error opens one notification per client, channel and type (`send:<channel>`, `receive:<channel>`, `llm:quota|auth|model`) for the Client and the Admin; the next success closes it. A send deferred by the daily cap is not a failure; timeouts and unusable model answers are transient and not notified. Texts are fixed sentences with an action, never a secret or the provider's text. Human intervention, billing errors, quotas, sync and report failures were already wired.
- Audit: the middleware keeps the `before` state of client update, LLM assignment, source, plan, send cap, channels and plan edits (read through the app's own GET route with the caller's session, so it is secret-free and redacted); refused Admin and Client sign-ins are audited (`admin.login_refused`, `client.login_refused`: typed name, reason, never the password). Invoice actions were already audited (`invoice.generate`, `invoice.status_change`).
