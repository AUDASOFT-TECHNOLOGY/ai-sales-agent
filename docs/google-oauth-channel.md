# Gmail mailbox through Google OAuth (XOAUTH2)

Status: **wired end to end in the application, tested against doubles only**. Nothing has been tried
against the real Google or the real Gmail, and **the Google OAuth application is not verified or
approved by Google**. The Admin enters the Google application's credentials in Settings, the Client
clicks "Connect Revansa with my Gmail", and the mailbox is used for sending (SMTP XOAUTH2) and for
reading replies (IMAP XOAUTH2, `observe` mode). Do not claim Gmail works in production until the
Google steps below are done and a real consent has succeeded.

## Client journey (what the code does, against fakes)

1. The Client chooses Email, then Gmail, and clicks "Connect Revansa with my Gmail". It types nothing.
2. `POST /client/v1/channel/google/start` returns the Google consent URL (PKCE S256, signed single-use
   state bound to that client, valid 10 minutes). Refused: 403 channel not allowed, 409 channel is not
   e-mail, 409 a Revansa (dedicated) mailbox is assigned, 503 Google not configured by the Admin.
3. Google shows its own consent page; the Gmail password never reaches Revansa.
4. `GET .../callback` always answers 302 to `/client/channel?google=connected|denied|scope|invalid|
   unavailable|error` (no code, token or address in the URL).
5. Scopes actually granted are checked: if `https://mail.google.com/` is missing from the granted
   scopes (Google's granular consent lets a person untick it) nothing is kept, the grant is revoked
   and the result is `scope`. A refresh that returns a narrower scope puts the client in
   `reauth_required`. Reading (IMAP) and sending (SMTP) both need that single scope, so they are
   granted or refused together.
6. `GET .../google` gives `state` (`not_connected|connected|reauth_required|unavailable`),
   `send_cap_configured` and `send_ready`. `send_ready` is true only when connected AND the Admin set
   the daily send cap.
7. `POST .../disconnect` deletes the grant locally first (always), then asks Google to revoke it; if
   Google cannot be reached the grant is still gone locally and no further send or poll is possible.
8. Reconnecting replaces the grant and revokes the previous refresh token. A failure after the code
   exchange (scope, address lookup) revokes what Google just issued: no orphan grant.
9. Isolation: the grant, the token cache and the pending sessions are keyed by client id; the client id
   comes only from the session cookie, and a state made for another client is refused.

Sending is closed by default: `OAuthMailChannel` refuses (`FAILED_TRANSIENT`, `send_cap_not_configured`)
without a configured per-client daily cap (`DailySendCap`, UTC day, no default value, no implicit
unlimited), and `send_cap_reached` once reached; a refused or failed send gives its slot back; the
token is not even requested when the cap refuses. The in-memory counter forgets on restart (a shared
counter is needed for several processes).

## What exists

- `src/sales_integrations/google_oauth/`: consent URL (PKCE S256, `access_type=offline`,
  `prompt=consent`), code exchange, refresh, revoke, account address, typed errors
  (`GoogleReauthRequiredError` for `invalid_grant`, `GoogleTransientError`,
  `GoogleAccessDeniedError`, `GoogleProtocolError`). The HTTP client is injectable.
- `src/sales_app/google_mailbox.py`: signed single-use state, `GoogleMailboxConnection`
  (start / callback / disconnect / status), `GoogleTokenSource`, `OAuthMailChannel`,
  `OAuthMailInbox`, `mailbox_login_proof`. Everything is written against ports
  (`GoogleGrantStore`, `PendingAuthorizationStore`, `GoogleOAuthPort`); in-memory versions are
  provided for tests.

Mail goes through the existing adapter with XOAUTH2: SMTP `smtp.gmail.com:587` (STARTTLS), IMAP
`imap.gmail.com:993` (SSL). The scope `https://mail.google.com/` is the only constant tying the
design to IMAP/SMTP (`MAIL_SCOPE`); a later Gmail REST implementation replaces it, together with
the two wrappers.

## Google Cloud prerequisites (done once by the platform operator, never by a Client)

1. Create a Google Cloud project and configure the OAuth consent screen (external).
2. Enable the scopes `https://mail.google.com/`, `openid`, `email`.
3. Create an OAuth client of type "Web application".
4. Authorised redirect URI, exactly: `{public_base_url}/client/v1/channel/google/callback`.
5. Keep the client id and client secret in the platform secret store (Admin only).

Limits to know before promising anything to a Client (from Google's published policies as known
when this was written; check the current Google documentation before relying on any number):

- `https://mail.google.com/` is a **restricted scope**. Using it with arbitrary Google accounts
  requires Google's OAuth app verification and, for restricted scopes, an annual third-party
  security assessment (CASA). This has not been started or obtained.
- While the app is in **Testing** mode: at most 100 listed test users, only those users can
  consent, and refresh tokens expire after **7 days** (the mailbox then becomes `reauth_required`).
  This is not usable for production mailboxes.
- While the app is **unverified** (even in production status) Google shows an "unverified app"
  warning and caps new users at 100 over the project's lifetime; verification is needed to lift it.
- Google OAuth verification also needs a published privacy policy, a verified domain, a justified
  scope and a demo video. Lead time is outside our control.
- A narrower alternative is the Gmail REST API with `gmail.send` (sensitive, not restricted) plus a
  read scope; reading mail with `gmail.readonly`/`gmail.modify` is restricted again. Replying needs
  reading, so IMAP/`mail.google.com` is not the only restricted path. No decision taken.
- Google Workspace administrators can block third-party apps or IMAP for their domain; consumer Gmail
  and Workspace also have their own sending limits (on the order of hundreds to a couple of thousand
  messages a day; verify) which apply on top of the Revansa cap, and exceeding them can suspend the
  mailbox. Prospecting cold mail from a personal Gmail risks the Client's account; this is a product
  risk to put to the Client.
- A Google account holds a limited number of refresh tokens per OAuth client; the oldest are
  invalidated when it is exceeded (reconnecting repeatedly can break another device's grant).
- Refresh tokens can also stop working when the user revokes access, changes the password in some
  cases, or after long inactivity; the product treats all of these as `reauth_required`.

## Behaviour contracts

- State: `base64url(json{client, session, exp}).base64url(HMAC-SHA256)`, valid 10 minutes, single
  use (the pending session, which holds the PKCE verifier, is consumed by the first callback, valid
  or not), bound to the authenticated client of the callback request. Every refusal raises the same
  `InvalidGoogleStateError`.
- Token source: in-memory access-token cache, renewed 60 s before expiry, one refresh at a time per
  client, cache dropped if the stored grant changes. `invalid_grant` marks the client
  `reauth_required` and stops further refresh attempts until a new consent.
- Wrappers fetch a token at each `send()` / poll. Failures: `send()` returns `FAILED_TRANSIENT`
  with code `reauth_required` or `token_unavailable`; polling raises `MailAuthError` /
  `MailUnavailableError`.
- Login proof: HMAC of the Gmail address and a fingerprint of the refresh token. Stable across
  access tokens; changes on reconnection or for another address.
- No token, code, secret or Google answer appears in a `repr`, log line or error message.

## How it is wired (what the code does now)

- **Platform credentials** (global, Admin only, write-only, never returned; Admin -> Settings ->
  "Gmail"): `gmail.oauth_client_id`, `gmail.oauth_client_secret`, `gmail.oauth_state_key` (16 to 256
  printable characters). The public base URL is the existing `SALES_AGENT_PUBLIC_BASE_URL` (must be
  https, or http on localhost): the redirect URI is derived from it. They are read at each use, like
  the payment secret, so what the Admin saves applies without a restart. `GET
  /admin/v1/settings/integrations/gmail/status` is config-only (no network): `configured` means
  "stored and usable", `verified` is always false, and the redirect URI to copy into Google Cloud is
  shown. Stored is not verified.
- **Mode `gmail`** in the e-mail channel settings (`{"mode": "gmail", "from_name", "subject"}`), exactly
  one of `gmail` / `platform` / `custom`. The client chooses "Use my Gmail" (`email.use_gmail: true`)
  or goes back to a Revansa mailbox (`use_gmail: false`); it cannot name a server, a login, a
  password or a token (422). The server refuses (409) every transition that would leave two
  mailboxes: into Gmail while a Revansa mailbox is assigned, its activation is under way or a mailbox
  password is stored; out of Gmail while a grant is held (disconnect first); storing a dedicated
  mailbox credential for a client in Gmail mode or holding a grant. This applies to the Client route,
  the Admin settings publication and the Admin credentials route (`sales_app/mailbox_exclusivity.py`).
- **Grant storage**: the encrypted secret store, client scope (`gmail.refresh_token`,
  `gmail.account_address` UNIQUE across clients, `gmail.reauth_required`). These keys are not in the
  credential catalog: the Admin credentials API can neither write nor list them (404). Survives
  restarts; deleted at disconnection (after which Google is asked to revoke it); a failure after the
  code exchange revokes what Google just issued. The same Gmail address cannot be connected to two
  clients (`?google=in_use`).
- **Pending consents** (the PKCE verifier of a consent under way) are in memory
  (`InMemoryPendingAuthorizationStore`). Revansa runs one worker by design (uvicorn without
  `--workers`): a consent started on one process and finished on another would be refused as
  `invalid`, never accepted wrongly. A consent in progress is lost by a restart (the client clicks
  again). A grant is not.
- **Runtime**: `bootstrap.py` builds one `GmailMailService`; `default_builders(..., gmail=...)` makes the
  e-mail provider build `OAuthMailChannel` (SMTP `smtp.gmail.com:587` STARTTLS, XOAUTH2) and
  `OAuthMailInbox` (IMAP `imap.gmail.com:993`, XOAUTH2) for a client in Gmail mode; the other modes are
  built exactly as before. The sending channel goes through `wiring.runtime._metered` like any e-mail
  channel (shared SQL daily cap, `min(Admin cap, plan quota)`); the OAuth channel uses
  `CapEnforcedByWiring` so a message is counted once. Without a cap, or without a database, nothing is
  sent. A token is fetched at each send / poll (cached, renewed 60 s before expiry); `invalid_grant`
  or a narrowed scope marks the grant `reauth_required` (persistent) and stops sending and reading.
- **Reading replies**: `observe` mode, because it is a person's own mailbox: nothing is ever marked
  read or written, the folder is opened read-only, the position is kept in the database
  (`op_mail_cursor`, migration 0010) and starts at the end of the mailbox at the first read (no history
  is touched, so a reply that arrives before the first read is not seen), and the body of a message is
  read only when its sender is a known prospect. Reconnecting resumes from the saved position.
  Without a database the reader refuses to read.
- **Readiness** (`channel_status`): `ready` only with the Google application configured, a valid grant
  (mail scope verified at consent and at every renewal) and a daily cap set; `waiting_platform` when
  Google is not configured or the cap is not set; `action_required` when not connected or when
  reconnection is needed. The client's agent does not start until then. A client in Gmail mode is
  "not ready" (not run) while it has no valid grant. Gmail asks for no password anywhere.
- **Client UI**: Channel page -> "Your Gmail" card: choose Gmail or a Revansa mailbox (exclusive), "Connect
  Revansa with my Gmail" (redirects to the URL returned by `POST /client/v1/channel/google/start`),
  return messages for `?google=connected|denied|scope|invalid|in_use|unavailable|error`, connected address,
  reading state, Reconnect, Disconnect (with confirmation), warning when no daily cap is set.

## Google Cloud: what you must do (once, by the platform operator)

1. Create (or pick) a Google Cloud project.
2. **APIs and services -> Library**: enable the **Gmail API**. Check that IMAP is allowed for the accounts
   you test with (consumer Gmail: Settings -> Forwarding and POP/IMAP; Workspace: the administrator may
   block IMAP or third-party apps).
3. **OAuth consent screen**: user type External; app name, support e-mail, authorised domain, **privacy
   policy URL**, developer contact. Add the scopes `https://mail.google.com/`, `openid`, `email`.
   `https://mail.google.com/` is a **restricted scope**.
4. While the app is in **Testing**: add every Gmail address that will connect as a **test user** (at
   most **100**); only they can consent, and refresh tokens expire after **7 days** (the client then sees
   "Reconnection needed"). Good enough for a business test with a few people, not for production.
5. To leave Testing for arbitrary accounts: publish the app and complete **Google's OAuth app
   verification** and, for a restricted scope, the **security assessment (CASA)** required by Google.
   Until then Google shows an "unverified app" warning and caps the number of new users at 100. These
   requirements come from Google's policies as known when this was written; check the current Google
   documentation. Nothing has been submitted or obtained.
6. **Credentials -> Create credentials -> OAuth client ID**, type **Web application**. **Authorised
   redirect URI, exactly**: `{SALES_AGENT_PUBLIC_BASE_URL}/client/v1/channel/google/callback` (shown in
   Admin -> Settings -> Gmail; same scheme, host, port and path, no trailing slash).
7. In Revansa: set `SALES_AGENT_PUBLIC_BASE_URL` (https) and restart once; then Admin -> Settings -> Gmail:
   enter the client id, the client secret, and a random state signing key (16+ characters, for example
   `openssl rand -base64 24`). Then set the client's daily send cap (Admin -> client -> send limit).
8. Test with a **test user** account you own: Client -> Channel -> "Use my Gmail" -> "Connect Revansa
   with my Gmail" -> consent -> the page shows the address.

## What has NOT been verified (no real Google call was made)

- That Google accepts the application, the consent screen, the redirect URI or the scope.
- That a real `access_type=offline` consent returns a refresh token, that granular consent behaves as the
  scope check assumes, or that a real refresh / revoke answers as the client expects.
- That `smtp.gmail.com` / `imap.gmail.com` accept the XOAUTH2 string built here, that the observe-mode IMAP
  commands work against Gmail's IMAP implementation, or the sending limits of the account.
- Deliverability and the risk of cold mail from a personal Gmail account (a product risk).
- Multi-worker deployments (pending consents are per process).
- The access log of the web server records the callback URL (`state` and the one-use `code`): both are
  single-use and the code is bound to the PKCE verifier, but do not forward those logs to third parties.
