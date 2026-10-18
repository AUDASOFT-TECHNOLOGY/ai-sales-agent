# Integration contracts: Gmail OAuth and Telegram Admin (wave 2, not applied)

Status: sections 1 to 4 (Gmail) are now APPLIED, with these differences: the keys are `gmail.*` (not `google_*`), the
mode is `gmail`, the grant lives in the secret store outside the credential catalog, and the proof of section 3 is
replaced by the grant itself (see `docs/google-oauth-channel.md`, which is the reference). Section 5 (Telegram) and
the text below are the original proposal; Nothing below is applied to a shared file unless said above. The autonomous parts exist as new files:
`sales_integrations/google_oauth/`, `sales_app/google_mailbox.py`, `web/src/admin/pages/AdminClientTelegramCard.tsx`.
Apply only after the Email work has frozen the shared files and after validation.

## 1. Gmail: mutually exclusive with the platform mailbox

A client's e-mail is exactly one of: custom, platform mailbox, Google. Rules to enforce at the single place that
resolves the mailbox (`config/loader.py` + `mailbox_access.py`), not in the UI only:
- connecting Google while a platform mailbox is assigned is refused (409, fixed sentence);
- assigning a platform mailbox while Google is connected is refused the same way;
- `platform_mailbox_clients()` (mailbox_proof.py) must skip Google clients.

## 2. Gmail: send cap per client

A configurable per-client daily cap, stored with the client's e-mail settings (key to name at integration, e.g.
`google.daily_send_cap`). No default value is invented here: the cap is "unset" until the Admin sets it, and the Admin
UI must say that Gmail's own limits apply meanwhile. The default is a business decision (see open decisions).
Enforcement point (implemented): `OAuthMailChannel.send()` takes a `send_guard` (`DailySendCap` over a `SendCounter`
port) and refuses with `send_cap_not_configured` / `send_cap_reached`; with no guard it refuses too (fail closed).
Remaining: a shared counter for multi-process, and the settings key + Admin UI.

## 3. Gmail: proof contract with `mailbox_proof.py` (read, not modified)

Current platform proof: `ClientMailbox.print_` = `fingerprint(host, port, security, username, PASSWORD, ...)`, recorded in
`application.mail_auth` (`AuthLedger.record/state(subject, print_)`) under `client:{id}:smtp|imap`.

Incompatibility to avoid: for Google the "password" is an access token that changes about every hour. Reusing
`print_` would void the proof hourly. The Google proof must use
`google_mailbox.mailbox_login_proof(key, address, refresh_token)` as `print_` (stable per address + grant, changes on
reconnect or another address). `GoogleMailboxConnection.proof(client_id)` returns it, or None when not connected or
`reauth_required`.

Needed in `mailbox_proof.py` (new branch, existing branches untouched): a `google_mailbox_state(application, client_id)`
that returns `READY` only if the ledger holds AUTHENTICATED for both `client:{id}:google:smtp|imap` with that proof;
the verification step runs the existing `run_login_check` with a probe built from the XOAUTH2 settings (fresh token).
Note the ledger is in memory: after a restart nothing is proven, as for the platform mailbox (`verify_all` equivalent).

## 4. Gmail: shared-file changes

| File | Change |
|---|---|
| config/secrets.py, secret_store/catalog.py | Global (Admin only, write-only): google_oauth_client_id, google_oauth_client_secret, google_oauth_state_key (>= 16 bytes). Per client (written by the callback only): google_refresh_token (sensitive), google_account_address (unique across clients), reauth flag. Keep the test that aligns secrets.py and the catalogue passing. |
| bootstrap.py | Build GoogleOAuthClient (redirect_uri = {public_base_url}/client/v1/channel/google/callback), GoogleMailboxConnection, one GoogleTokenSource per client; a GoogleGrantStore adapter over the secret store; an atomic PendingAuthorizationStore (memory only if single process). |
| api/client_channel.py, api/app.py | POST /client/v1/channel/google/start -> {authorize_url} (409 if channel is not e-mail, 403 if not allowed, 503 if Google is not configured by the Admin); GET .../google/callback -> 302 /client/channel?google=connected|error, no data in the URL, errors never raw; POST .../google/disconnect -> ChannelResponse. Body of the Client routes stays technical-field-free (422 without echo). |
| channel_status.py | not_connected / reauth_required -> action_required; Google not configured by the Admin -> waiting_platform; proof valid -> ready. |
| channel_policy.py, mailbox_access.py, config/email_settings.py, wiring/providers/email.py | New "google" mode: from_name and subject only; XOAUTH2 access to smtp.gmail.com:587 starttls and imap.gmail.com:993 ssl (constants in google_mailbox.py); build OAuthMailChannel / OAuthMailInbox instead of static-token channel. |
| api/admin_integrations.py, admin/api.ts, client/api.ts, AdminSettingsPage.tsx, ClientChannelPage.tsx, i18n | Admin credentials form + config-only test (no network); Client "Connect Gmail" / "Reconnect" / "Disconnect" component; no token ever in a response. |

Open facts: no migration is expected (secret store). The session cookie must survive the top-level redirect back from
Google (SameSite=Lax to verify in client_auth). Production for external clients needs Google's verification of the
restricted scope (see docs/google-oauth-channel.md); until then Testing mode applies.

## 5. Telegram Admin: what remains

Verified by reading: PUT .../integrations/telegram.bot_token, POST .../telegram/connect (owner only), GET
.../integrations, `check_telegram` (read-only) exist. Missing, in this order:
1. sales_integrations/telegram probe: `delete_webhook` (not shared with Email; do first).
2. admin_integrations.py, appended at the end of the Telegram section only:
   - POST /clients/{id}/integrations/telegram/test: read-only, `check_telegram(probe, expected_url)`; no message sent;
   - POST /clients/{id}/integrations/telegram/disconnect: owner only, deleteWebhook then remove telegram.webhook_secret.
3. admin/api.ts: adminConnectClientTelegram, adminTestClientTelegram, adminDisconnectClientTelegram
   (token save: existing adminSaveClientCredential(clientId, "telegram.bot_token", value)).
4. AdminClientDetailPage.tsx: one import and one block mounting AdminClientTelegramCard with the injected props
   (tokenConfigured from the credentials list; canEdit = owner).
5. docs/telegram-channel.md: the two routes, the three 409 cases of connect, and "a prospect must start the bot via an
   invitation link" (a bot cannot write first).

Token rotation (decision needed): "Replace token" = save + connect (already the card's flow). Keeping webhook_secret on
reconnect keeps already-sent invitation links valid (current behaviour); disconnect removes the secret and so
invalidates them. The card asks for confirmation before disconnecting.

## 6. Open business decisions

1. Default Gmail daily cap (none invented here). 2. Google activation without Admin validation (recommended yes).
3. Gmail address shown masked or not. 4. Who creates the Telegram bot and who sends invitation links.
5. Whether disconnecting Telegram should invalidate old invitation links.
