# The Client space (web)

The Client space is the business owner's side of Revansa. It is organised around what an owner does,
not around the platform's internals. The words "lane", "token", "webhook" or "SMTP" never appear in it.

## Menu and routes (`web/src/App.tsx`, `web/src/shell/AppShell.tsx`)

The menu follows the order an owner works in: prepare, then follow, then look after the account.

| Group | Menu | Routes | What it is for |
|---|---|---|---|
| (top) | Home | `/home` | Agent state and its switch, what needs the owner, key figures, circuits, usage, connections, subscription |
| (top, until done) | Get started | `/setup` | The 7-step guide: business, products, payments, prospects, channel, circuit, start. Each step opens the real screen |
| Prepare | Offers | `/offer/products[/new\|/:id]`, `/offer/payments`, `/offer/rules` | Catalogue (completeness per product), one page per product (photos, documents, testimonials, payment link, arguments), payment methods, business and rules (tone, schedule, follow-ups) |
| Prepare | Circuits | `/circuits` | A circuit = source of prospects + channel + products. Channel and products are changed on the card; channels and accounts below |
| Follow | Conversations | `/conversations[/:id]`, `/prospects` | Every prospect (contacted or not), thread, prospect card, take-over; the same people as a table |
| Follow | To review | `/review`, `/review/payments` | Messages to approve, payments to confirm, conversations a person must take over |
| Follow | Results | `/results`, `/results/daily` | Figures by product, source, channel and period; the daily summary |
| Account | Subscription | `/account`, `/account/profile` | Plan, invoices, profile and password |
| Account | Notifications | `/notifications` | What failed or needs an action |

Former addresses (`/connections/*`, `/reports`, `/dashboard`, `/billing`, `/profile`, `/lanes`, `/channel`,
`/approvals`, `/settings`...) redirect. `client/overview.tsx` reads the agent, readiness, settings, channel,
source, circuits and figures once for the shell, the Home and the guide.
The theme is scoped to the Client space (`ClientTheme` sets `data-app="client"`; `styles/client-theme.css`:
dark glass over an aurora, one colour per kind of thing: source cyan, channel green, products violet); the
Admin console keeps its own look. Copy lives in `i18n/` (English is the reference; French is complete for
the Client space, `client.v2.*` holds the 2026 redesign; Arabic is partial and falls back to English).

## Demo account

Login `Demo` / `123456789` opens an in-memory demonstration (`web/src/demo`): a fictitious online-training
business with two circuits (a spreadsheet sent by WhatsApp, HubSpot leads waiting for their e-mail box),
four products with photos, documents and testimonials, 22 prospects and 16 conversations. Nothing reaches
the network; a reload restores the initial data.

## Design rules

- Nothing is invented: a figure that cannot be read is said so; "connected" is shown only after a real
  test; a document is "used by the agent" only if text was really extracted from it.
- A save sends only what changed and names the version it was based on (409 on conflict, the screen
  reloads). The commercial document is the Brain's own and is validated by the Brain's schema.
- Technical wiring is the platform's: the client never types a token, server or webhook.
- Sources and "lanes": a single-source client uses `/source`; an account Revansa runs with several
  sources uses `/lanes` behind the "prospect lists" screen (id, channel and products are filled in
  automatically; products and sending channel are offered in business terms).

## Main APIs used (`/client/v1`, session cookie, never a client id)

| Area | Endpoints |
|---|---|
| Session/account | `auth/*`, `profile` |
| Agent | `agent`, `agent/start`, `agent/pause`, `readiness` |
| Offer | `settings` (GET/PUT, `commercial` = the Brain's document) |
| Sources and circuits | `source` (GET/PUT), `source/test`, `lanes` (several-source accounts: one card per circuit) |
| Channels | `channel` (GET/PUT), `channel/email-activation`, `channel/google/*`, `channel/invitations` |
| Prospects | `prospects`, `summary`, `conversations/{id}` |
| Review | `approvals` (+ approve/reject), `payments`, `payments/{prospect}/confirm` |
| Product documents | `products/{product}/resources` (list, `link`, `file`, `{id}/retry`, `{id}` DELETE, `{id}/download`) |
| Reports | `reporting`, `reports*` |

## Product documents (backend: `src/sales_app/resources/`)

Stored per client and product (migration 0014). Files are checked by extension, real content and size
(10 MB); links must be public `https` and are fetched through a fetcher that refuses private networks.
Readable: text, Markdown, CSV, PDF with a text layer, Word, Excel, web pages. Images, videos, scans and
pages without text are kept and reported as "not read". The agent searches passages locally (BM25, no
model call); the Brain (`sales_brain_kit/consultative/knowledge.py`) bounds the excerpts, strips links,
amounts and account numbers, and gives them as untrusted reference material.

## Payments

Revansa is not a payment gateway. An *online* method is a link to the client's own page, an *offline* one
is instructions; both are given to the buyer exactly as written (the model never sees them). One switch on
the Payments screen manages the purchase journey (`offer/purchaseJourney.json`, a workflow with a payment
step), and never replaces a sales process set by Revansa. A payment is paid only when the client confirms
it (`PaymentDesk`, the Core's single confirmation door).
