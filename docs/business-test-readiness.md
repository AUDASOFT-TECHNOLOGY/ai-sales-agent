# Preparing the business test (end to end)

What can be run today, what must be configured by hand, and what is **not** yet real. Nothing here
contains a secret; every credential is entered through the Admin UI or the credential store.

## 1. Start the project (local)

```
uv sync
export SALES_AGENT_DATABASE_URL=sqlite:///./revansa-dev.db     # or a PostgreSQL URL
uv run sales-agent secrets generate-key                          # prints a master key
export SALES_AGENT_SECRETS_KEY=<the printed key>                 # keep it; losing it loses the stored credentials
uv run sales-agent db upgrade                                    # applies migrations 0001 → 0013
uv run sales-agent admin create --username <you> --role owner    # prompts for a password
uv run sales-agent serve                                         # API on http://127.0.0.1:8000
cd web && npm install && npm run dev                             # UI on http://localhost:5173
```

- The Vite dev server proxies `/admin/v1` and `/client/v1` to `127.0.0.1:8000` (see
  `web/vite.config.ts`), so the browser stays on one origin.
- The write-back to Google Sheets / HubSpot is **permanent** (spec 6.3, 6.4): no flag, no Admin or Client switch. A source is written only when its client mapped columns/properties and a database keeps the ledger.
- Public webhooks (Telegram, payments) need a public https address: `docs/telegram-channel.md`.

## 2. External services to configure by hand

| Service | What to do | Where |
|---|---|---|
| LLM provider (OpenAI is the implemented adapter) | Admin enters the API key, tests it, activates models, assigns one per client | Admin → Settings |
| Telegram | Create a bot with BotFather; Admin enters the token, connects the webhook (public https URL) | Admin → client → Telegram card |
| Dedicated mailbox | Create the mailbox at the provider (OVH) by hand using the Sender Name the client confirmed; Admin enters SMTP/IMAP settings, verifies, then activates | Admin → Email activation |
| Gmail (client-owned) | Create a Google OAuth app (Web application; Gmail API enabled; consent screen with the restricted scope `https://mail.google.com/`; test users listed while unverified, 100 max; redirect `{SALES_AGENT_PUBLIC_BASE_URL}/client/v1/channel/google/callback`). Enter client id / secret / state key in Admin → Settings → Gmail; set the client's send cap. Google's verification + security assessment are required before arbitrary accounts can connect (`docs/google-oauth-channel.md`) | Google Cloud, then Admin → Settings |
| Google Sheets | Service account shared on the client's sheet | `docs/sheet-schema.md` |
| HubSpot | The client connects its own account by OAuth (spec 7.1); the Admin stores the HubSpot app id / secret / state key (scopes `crm.objects.contacts.read`; `.write` and `crm.schemas.contacts.read` for write-back). The Private App token is legacy, Admin-only, never offered to a client | Client → Source; Admin → Settings |
| Payment provider | None integrated. A signed webhook (`POST /api/payments/webhook`, HMAC) is implemented; set `payments.webhook_secret`. A real PSP needs an adapter or a relay (`docs/billing-payments.md`) | Admin → Settings |
| WhatsApp | Meta credentials alone prove nothing: the channel stays "waiting / not verified" until Meta validates the number (`docs/whatsapp-channel.md`) | Admin |

## 3. Data to prepare (invent nothing, use your own)

- One Admin (owner) account and one client created from Admin → Clients.
- A plan (price, currency, daily quota) assigned to the client, and an LLM model assigned.
- **A daily email send cap set by the Admin** (Admin → client → send limit). Without it, no email
  goes out: this is deliberate.
- A Google Sheet with a few prospects: one contactable, one without coordinates, one who will reply.
- A Telegram account you own, to press Start on the bot.
- For billing: a plan assigned in a past month, then generate that month's invoice.

## 4. Exact path to test together

1. Admin: sign in → create client → assign plan + model → set the send cap.
2. Client: sign in → profile and products → add a Google Sheet source → test it → create two
   circuits (source + channel + product each).
3. Channel: Email (dedicated mailbox request, Admin activates; or Gmail) and/or Telegram (client
   shares the invitation link; the prospect presses Start).
4. Start the agent → approve the first messages → check the sent mail / Telegram message.
5. Reply from the prospect → check the agent's answer, the follow-up scheduled, the Sheet status.
6. Send until the cap is reached → check the refusal and the explanation shown to the client.
7. Client → Reporting: set the channel, address, time and an explicit time zone → generate → check
   delivery status and content.
8. Admin → Billing: generate the invoice for a closed month → send a signed webhook (or mark
   manually) → check `PAID` origin (electronic vs manual), replay is a no-op.

## 5. Automated check that mirrors this path

```
uv run pytest tests/app/test_commercial_journey_e2e.py -q       # simulated Sheets/SMTP/IMAP/Telegram
```
See `docs/e2e-commercial-journey.md` for what is real and what is simulated.

## 6. Known limits (do not test these as if they worked)

- No payment provider integrated; no client checkout, refunds or partial payments.
- No self-service signup landing page; the backend accounts/plans/billing are the same system it
  will reuse.
- HubSpot write-back and Gmail OAuth were only exercised against simulated services.
- Gmail: wired end to end (Admin credentials, client mode `gmail` exclusive with the dedicated mailbox,
  persistent encrypted grant, XOAUTH2 send behind the shared cap, observe-mode reading with a persistent
  position, reconnect/disconnect UI) and tested with doubles in `tests/app/test_gmail_wiring.py`. **Not
  verified**: any real Google consent, token refresh, revoke, or Gmail SMTP/IMAP login. Until Google
  verifies the app, only listed test users (max 100) can connect and their refresh tokens last 7 days.
  A reply that arrives before the first read of a freshly connected mailbox is not seen (observe mode
  starts at the end of the mailbox). Pending consents live in one process's memory (one worker by design).
- WhatsApp is not verified; it is not part of the priority path.
- Purchase intent / appointments / conversions are reported as not tracked: no sale is ever
  claimed from them.
- The send cap is per client and channel (no per-circuit cap). Tests ran on SQLite, not PostgreSQL.
- `tests/app/test_writeback_durable.py::test_two_writers_over_one_database_never_repeat_or_skip_a_number`
  fails on a clean `HEAD` too (SQLite file concurrency on Windows); it is not caused by this work.
