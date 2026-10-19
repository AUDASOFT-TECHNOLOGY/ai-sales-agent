# Admin: configuration to finish after hosting

This is the one page the Admin follows once Revansa is hosted. Everything here is entered in the Admin
screens (or one environment variable); nothing needs a new implementation. **No integration below has
been exercised against a real external account**: the code is tested against doubles only, so each
section ends with the check that proves it for real.

Rules that hold everywhere: a secret is write-only (never returned by an API, shown in a screen or put
in a log; the screens say only "set" / "not set"); a Client never types a token, a key, a webhook or an
SMTP setting; "stored" never means "verified".

## 0. Deployment (environment, set by whoever hosts)

| Variable | Purpose |
|---|---|
| `SALES_AGENT_DATABASE_URL` | PostgreSQL URL (the password stays in this variable only). Apply migrations with `sales-agent db upgrade` on a **test database first**. |
| `SALES_AGENT_SECRETS_KEY` | Master key of the credential store (`sales-agent secrets generate-key`). Losing it loses every stored credential: back it up apart from the database. |
| `SALES_AGENT_PUBLIC_BASE_URL` | `https://<your domain>`, no trailing path. Needed for every OAuth redirect and for the Telegram/WhatsApp webhooks. |
| `SALES_AGENT_ADMIN_COOKIE_SECURE` | Leave `true` (default) behind HTTPS. |
| `SALES_AGENT_WRITEBACK_ENABLED` | Default `true`; `false` starts the deployment with write-back off. The Admin's switch (below) overrides it. |

Create the first Admin: `sales-agent admin create --username <you> --role owner`. No development
account exists in production.

## 1. AI model (Admin → Settings → OpenAI)

Enter the API key (and optional organisation/project/base URL), add the model names and their upstream
ids, press **Test** on a model, activate it, then assign it to each client (Admin → client). Test =
a real call to the provider. A client cannot choose or see the model.

## 2. Google (Gmail mailbox **and** Google Sheets), Admin → Settings → Gmail

One Google Cloud "Web application" OAuth client serves both features.

- Enter: client id, client secret, state signing key (16–256 characters, random).
- **Authorised redirect URIs** (the screen prints both once the public URL is set):
  - `{base}/client/v1/channel/google/callback` (Gmail)
  - `{base}/client/v1/sheets-access/callback` (Google Sheets)
- Scopes: `https://mail.google.com/` (restricted), `https://www.googleapis.com/auth/spreadsheets`,
  `openid`, `email`.
- Limits: Google's app verification (and, for the restricted Gmail scope, a security assessment) is
  needed before arbitrary accounts can connect. In "Testing" mode only listed test users (max 100) can
  consent and refresh tokens expire after 7 days (the client then sees "reconnect"). See
  `docs/google-oauth-channel.md`.
- Gmail also needs, per client, a daily send cap (Admin → client): sending is refused without one.
- Test: a test client clicks **Connect Google Sheets** / **Connect Gmail** in its space; the state must
  become *connected*. A revoked or expired grant shows *reconnect required* and the Client reconnects
  with one click.

## 3. HubSpot, Admin → Settings → HubSpot

- Create a HubSpot public app (developer account). Enter client id, client secret and state signing key.
- **Redirect URL**: `{base}/client/v1/hubspot-access/callback` (printed in the screen).
- Scopes, exactly: `crm.objects.contacts.read`, `crm.objects.contacts.write`,
  `crm.schemas.contacts.read`.
- Tokens are renewed automatically; a refused renewal shows *reconnect required* to the Client.
- Per client (Admin → client → Prospect sources): the **status mapping** (what each HubSpot lifecycle
  value means). The Client chooses which results are copied back (Sources → Results). For write-back
  the properties must exist in the HubSpot account first (types in `docs/writeback-hubspot.md`).
- Test: connect a test HubSpot account, run the source test in the Client's Sources page.

## 4. Write-back (Admin → Settings → Write-back, owner only)

- Default **on** (deployment default). The Admin can switch it off/on or return to "follow the
  deployment"; the decision is stored in the database credential store, audited (who, when), and applies
  to every running client at once without a restart.
- Switching it on writes nothing by itself. A source is written only if its Client named the
  columns/properties one by one, the client is on lanes, the client is not in dry-run, and the database
  keeps the attempts (ledger). Names, phones, e-mail addresses and the do-not-contact property are
  never written; a cell/property is overwritten only when empty or when Revansa wrote it.
- Not proven: no real Sheet or HubSpot has been written. Run the validation of
  `docs/writeback-operations.md` ("Not proven") on test accounts before relying on it. Google Sheets has
  no atomic compare-and-set: a person's simultaneous edit can still be overwritten in a very short window.

## 5. E-mail

- **Dedicated mailbox** (Admin → Settings → Email platform): domain, SMTP and IMAP hosts/ports/security
  and the technical account. Per client request: create the mailbox at the provider using the Sender
  Name the client confirmed, store its credentials (Admin → Email activation), verify, then activate.
- **Gmail**: section 2.
- Test: the connection test in the client's Admin page does a real login (SMTP and IMAP). A reply is
  attached to a prospect by its sender address; mail from an unknown sender is not read in full
  (`docs/email-channel.md`).

## 6. Telegram

- Create one bot per client with BotFather (a platform decision, not the Client's). Admin → client →
  Telegram card: enter the token, **Test**, **Connect** (Revansa registers the webhook
  `{base}/webhooks/telegram` itself and keeps its secret). Needs `SALES_AGENT_PUBLIC_BASE_URL` in HTTPS.
- **Hard limit, by Telegram's rules:** a bot cannot message a person who never pressed Start. Revansa
  therefore never cold-contacts on Telegram: each prospect receives a personal invitation link that the
  Client sends by its own means (`GET /client/v1/channel/invitations`), and only after they press Start
  does the conversation happen. A prospect list with no link sent yields no Telegram contact.
- Contacting prospects proactively from the Client's own Telegram account (MTProto) is **not
  implemented**: Telegram treats unsolicited outreach as spam; see `docs/telegram-user-account.md`
  (decision pending).
- Status: not validated with a real bot.

## 7. WhatsApp (scope: stored credentials and the webhook only)

Admin enters the verify token, app secret and API version (global) and, per client, the access token and
phone-number id; the Client supplies only its WhatsApp number. Admin → client → WhatsApp **Test** reads
the number record from Meta. Meta onboarding (business account, number registration, webhook
`{base}/webhooks/whatsapp`, approved templates for contact outside the 24 h window) is done on Meta by
the Admin; no screen checks it, and the status never claims "verified". Template management and
disconnect are not built. Treat WhatsApp as **future** until a real number has exchanged messages.

## 8. Moving Naga and Tounesimmo onto lanes (do not run before the checks)

`POST /admin/v1/clients/{id}/lanes/adopt` (Admin → client → Prospect sources → "Move onto lanes", owner).
It turns the client's current source and channel into lane `main` (whole catalog), keeps the catalogue,
settings, consent registry (keyed by contact digests, so opt-outs survive) and history, pauses the agent,
and is refused if the client is already on lanes or the version is stale.

**Known consequence:** in lanes every prospect id becomes `main:<id>`. For a client whose agent already
ran, the confirmation `confirm_new_ids` is required, and previous conversations stay attached to the old
ids while the same people appear as new prospects (a risk of a second first-contact message). Do it
before the first real run when possible, check the first plan in `approval` mode, and restart the agent
only after reviewing it. After the move the Client configures sources, channels, products and result
columns in its own space.

## 9. Final checks on the live site

1. `/health` answers; Admin sign-in works with your own account; sessions use secure cookies.
2. Each integration's own test above succeeds once with a real test account.
3. A client in `approval` mode: connect source + channel, start, read the planned messages, approve one.
4. Stop and restart the process: no message is sent twice (the actions are persisted).
5. `clients/naga/client.yaml` is a fallback file only; the database is the source of truth.
