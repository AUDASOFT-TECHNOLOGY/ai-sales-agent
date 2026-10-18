# The Client space (web)

The Client space is the business owner's side of Revansa. It is organised around what an owner does,
not around the platform's internals. The words "lane", "token", "webhook" or "SMTP" never appear in it.

## Menu and routes (`web/src/App.tsx`, `web/src/shell/AppShell.tsx`)

| Menu | Routes | What it is for |
|---|---|---|
| Home | `/home` | Agent state, start/pause, readiness checklist (what the client fixes, what Revansa must activate), key numbers |
| Prospects | `/prospects`, `/conversations/:id` | Prospect list and conversations (read-only, real data) |
| To review | `/review`, `/review/payments` | Messages to approve; payments to check and confirm |
| My offer | `/offer/products[/new\|/:id]`, `/offer/payments`, `/offer/business` | Catalogue, payment methods, business and messages |
| Connections | `/connections/sources`, `/connections/channels` | Where prospects are, and Email/Telegram/WhatsApp |
| Reports | `/reports` | Daily report settings and history |

Former addresses (`/agent`, `/lanes`, `/settings`, `/channel`, `/approvals`, ...) redirect.
The light theme is scoped to the Client space (`ClientTheme` sets `data-app="client"`;
`styles/client-theme.css`); the Admin console keeps its own look. Copy lives in `i18n/` (English is the
reference; French is complete for the Client space; Arabic is partial and falls back to English).

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
| Sources | `source` (GET/PUT), `source/test`, `lanes` (several-source accounts) |
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
