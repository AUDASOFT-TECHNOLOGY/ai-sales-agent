# The Client space (web)

The Client space is the business owner's side of Revansa. It is organised around what an owner does,
not around the platform's internals. The words "lane", "token", "webhook" or "SMTP" never appear in it.

## Menu and routes (`web/src/App.tsx`, `web/src/shell/AppShell.tsx`)

The menu follows the order an owner works in: prepare, then follow, then look after the account.

| Group | Menu | Routes | What it is for |
|---|---|---|---|
| (top) | Home | `/home` | Agent state and its switch, what needs the owner, key figures, circuits, usage, connections, subscription |
| (top, until done) | Get started | `/setup` | The 7-step guide: business, products, payments, prospects, channel, circuit, start. Each step opens the real screen |
| Prepare | Connections | `/connections` | The only place a connection is made, one row each, one of four states (connected, to connect, Revansa is finishing, not available): Email (Gmail), WhatsApp, Telegram to write to prospects; HubSpot, Google Sheets to read them. A summary says "N of M ready" and the next step. Which channel a circuit uses is chosen on the circuit; the name, subject and Telegram limits are in Offers > Rules |
| Prepare | Offers | `/offer/products[/new\|/:id]`, `/offer/payments`, `/offer/rules` | Catalogue (completeness per product), one page per product (photos, documents, testimonials, payment link, arguments), payment methods, business and rules (tone, schedule, follow-ups) |
| Prepare | Circuits | `/circuits` | A circuit = source of prospects + channel + products. Channel and products are changed on the card; when its source or channel is not connected the circuit says "a configuration is needed" and links to Connections |
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

## Connections do not depend on the chosen channel

Gmail, WhatsApp and the Telegram account can be connected whatever channel the account uses today (the
Admin must have opened that channel to the client). The grant is kept; choosing the channel on the circuit
then uses it (a Gmail already connected selects Gmail mode, a Telegram account already signed in selects
the account mode). The mailbox Revansa provides excludes the client's Gmail: the Email row offers "Use my
Gmail instead" first. A Google Sheets account can be authorised before any spreadsheet is chosen.

## A connection has two sides, and the client's does not wait for Revansa's

Each connection needs Revansa's side (the platform's Google, Meta, Telegram or HubSpot application, and the
channel opened by the Admin) and the client's (the account it wants, then its permission on the provider's
page). The Connections screen shows the client's side only: a connection is **Connected** (green) or
**Disconnected** (red, "To reconnect" after a revocation). It never says "Revansa is finishing" or "not
available".

An address or a number alone connects nothing: the owner of the account must authorise on the provider's own
page (Gmail, HubSpot, Google Drive), in Meta's window (WhatsApp) or with the code Telegram sends. So each row
has one button with the service's mark: "Connect my Gmail", "Connect my HubSpot", "Connect my Google Drive",
"Connect my WhatsApp Business", "Connect my Telegram", above which the client names the account (the Gmail,
the Google account, the HubSpot email, the Telegram or WhatsApp number). What it types is kept first
(`/client/v1/connections`, `sales_app/connection_hints.py`; the WhatsApp number is its profile). If the
provider's page can open, the client goes there; if it cannot yet, the client is told its request is saved and
that it will be asked to authorise, and nothing is lost. Only the circuits show the difference: a channel or a
source that is not usable yet is greyed ("Coming soon"); the one in use is never greyed.

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
| Channels | `channel` (GET/PUT), `channel/email-activation`, `channel/google/*`, `channel/whatsapp/*`, `channel/telegram/account/*`, `channel/invitations` |
| Source accounts | `sheets-access/*`, `hubspot-access/*` |
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
