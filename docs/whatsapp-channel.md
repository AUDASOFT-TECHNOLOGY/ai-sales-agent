# The WhatsApp channel

WhatsApp Business Cloud API, one account per client, connected by the client itself with Meta's
**Embedded Signup** (no token, no identifier, no webhook is typed by the client or by the Admin).
Each client has its **own** Meta Business Portfolio, WhatsApp Business account (WABA), number and
payment method; Revansa provides the Meta app, the Embedded Signup and the webhook, nothing else.
What the Admin must do on Meta after hosting: see [whatsapp-admin-setup.md](whatsapp-admin-setup.md).

> **What is proven and what is not.** Everything below is implemented and covered by tests that
> replace Meta with a fake. That proves what Revansa asks of Meta and what it keeps; it does **not**
> prove that Meta accepts the app, the review, the templates, or that a real number can send.
> The list "Needs a real Meta account" at the end says exactly what to check after hosting.

## How a client connects (Client space → Connections → Channels → WhatsApp)

1. The client has its WhatsApp **number** in its profile (the only thing it types). A changed number
   sets `whatsapp_reconnect_required` and the agent is not ready until the account is connected again.
2. **Connect WhatsApp** → `POST /client/v1/channel/whatsapp/start` returns the platform app's public ids
   (`app_id`, `config_id`) and a signed, single-use, 10-minute `state` bound to that client.
3. The browser loads Meta's SDK and opens Meta's own window (`FB.login` with the configuration, code
   flow). The client signs in at Meta and chooses its business, its WhatsApp Business account and its
   number. Meta gives the page a one-time code (30 s) and reports the ids it chose.
4. `POST /client/v1/channel/whatsapp/complete {state, code, waba_id?, phone_number_id?}`. The server:
   checks the `state`; exchanges the code with the app secret (the token never reaches the browser);
   asks Meta which accounts the token **really** grants (`debug_token`) — the ids sent by the browser
   are hints Meta confirms, never trusted; refuses (`denied`) a token that lacks
   `whatsapp_business_management` or `whatsapp_business_messaging`; reads the account's numbers;
   refuses (`number_mismatch`) a number that is not the one in the client's profile; **asks Meta which
   Business Portfolio owns the account** (see "Ownership"); claims the Portfolio, the account and the
   number for the client in `wa_accounts` (atomic: see "Ownership, associations and routing");
   subscribes the app to the account's webhooks;
   registers the number on the Cloud API (6-digit PIN generated and kept) unless Meta shows it
   connected; stores the token, the account id and the phone number id under the client's own scope in
   the encrypted secret store (`whatsapp.phone_number_id` is unique across clients); refreshes the
   client's runtime. Nothing is stored unless every step worked; on failure the token is revoked and
   the subscription undone.
5. Causes returned to the browser (fixed words, never Meta's text): `invalid`, `denied`,
   `number_mismatch`, `number_unclear`, `in_use` (number), `waba_in_use`, `business_in_use`,
   `platform_owned`, `ownership_unverifiable`, `unavailable`, `error`.

## Ownership, associations and routing

- **Reaching a WABA with a token is not ownership.** Revansa reads the WABA's `owner_business_info`
  (WhatsApp Business Account field of the Graph API) with the client's token. No owner named by Meta:
  refused (`ownership_unverifiable`). The owner is Revansa's own Portfolio
  (`whatsapp.platform_business_id`, set by the Admin): refused (`platform_owned`). Without
  `platform_business_id` nobody can connect (the Admin sees it in the platform's missing list).
- **Associations** live in `wa_accounts` (migration 0032): `client_id`, `business_id`, `waba_id`,
  `phone_id`, status, ownership (`verified` or `legacy_unverified`), the names Meta gave, the billing
  state, dates. Unique constraints on Portfolio, WABA and number: one of each per client, enforced by
  the database, so simultaneous connections cannot share them. Claimed before anything changes at
  Meta, undone if a later step fails. The token and the PIN stay in the encrypted secret store.
- **Routing.** Messages: by `phone_number_id` (registry). Statuses and account events: from
  `wa_accounts`, no secret is read. An event bound to a number follows the number and is refused if
  its WABA is not that client's; an event about a WABA alone follows the WABA. Unknown or
  contradictory events belong to nobody and change nothing. A Meta deauthorisation that matches
  several clients (one Meta user connected several) changes nothing.

## Billing (Meta bills the client; Revansa cannot see it)

- Meta's WhatsApp fees are the client's own, at Meta. Revansa's subscription does not include them,
  and Revansa's credit line is **not** to be shared with a client's WABA.
- Revansa knows no Meta call that proves a payment method. The states shown to the client and to the
  Admin: `unverifiable` (default: "Revansa cannot check it", with the instruction to check WhatsApp
  Manager > Payment methods), `to_configure` (Meta refused a send with error 131042), `confirmed`
  (reserved: nothing sets it today, and the interface never shows it without that evidence). A
  delivered message lifts `to_configure` back to `unverifiable`, never to `confirmed`.

The client sees: its own number, the business name Meta verified, a state (`not_connected`,
`connected`, `reauth_required`, `unavailable`), a status (`ready` / `waiting_platform` /
`action_required`) with a sentence, whether Revansa can write first (an approved opening template
exists), and 7-day delivered / failed counts. It never sees a token, an id of Meta's, a template or a
credit.

## Sending (rules of WhatsApp Business)

- **24-hour window.** Free text only while the person wrote to the account in the last 24 hours. The
  window is recorded when their message arrives (before anything else), per client and per number.
  An unknown window counts as closed (safe after a restart).
- **Outside the window** the message goes as the client's **opening template**: an approved template
  with exactly **one** body variable, which carries the agent's message (one line, 1024 characters).
  Chosen by the Admin; it stops being used as soon as it is no longer `APPROVED`.
- **No usable template:** nothing is sent; the message stays approved and waits (deferred: it does not
  use up an attempt), so it goes as soon as the person writes or a template is approved.
- An explicit template in a draft is never replaced.
- Error codes: account / moment problems wait without using an attempt (130429, 131042 billing,
  131047 window over — the window is forgotten —, 131048, 131049, 131056, 131031, 133010, a refused
  token); template problems (132000–132016) wait and mark the template (paused / disabled / missing);
  problems of the recipient (131026, 131030...) are permanent. A refused token marks the client
  `reauth_required`. A timeout after the request left stays "outcome unknown" for a human (WhatsApp has
  no idempotency key).
- Opt-in and opt-out remain the Engine's (consent registry, opt-out keywords); this module does not
  decide who may be contacted.

## Receiving, and what Meta tells us

`POST /webhooks/whatsapp` (signature `X-Hub-Signature-256` with the app secret; handshake with the
verify token on GET).

- **Messages** are routed by the receiving `phone_number_id` to one client, stored by the Engine and
  matched to the prospect by phone number (unknown senders follow the client's inbound policy).
  Reactions and system messages are not messages; an image / video / document caption is the text;
  the media id and the sender's profile name are kept as metadata (nothing is downloaded).
- **Delivery statuses** (`sent`, `delivered`, `read`, `failed` + Meta's error) are authenticated with
  the app secret, kept by Meta's message id, only move forward, and are shown on each outbound message of
  the conversation (`delivery`). A `failed` with a refused-token code marks the client to reconnect.
- **Template events** (`APPROVED`, `REJECTED`, `PAUSED`, `DISABLED`...) update the template in the Admin's
  list; a template no longer approved stops being the opening one.
- **Account events**: the account being withdrawn from Revansa (`PARTNER_APP_UNINSTALLED`, `ACCOUNT_DELETED`) forgets the
  authorisation; number quality / limit events refresh the snapshot the Admin sees.
- **Deauthorisation** (`POST /webhooks/whatsapp/deauthorize`) and **data deletion**
  (`POST /webhooks/whatsapp/data-deletion`, answers a status URL and a confirmation code): Meta's
  `signed_request` is verified with the app secret; the client tied to that Meta user (kept at
  connection) is forgotten. A call the secret does not sign is refused (401) and changes nothing.

## Reconnection and disconnection

- A refused token (send error 190 / 10 / 3, failed status, Admin "Ask Meta now") marks the client
  `reauth_required`: the Client space shows **Reconnect WhatsApp**; the same flow replaces the token (and
  unsubscribes the previous account if the number changed).
- **Disconnect** (client or Admin): unsubscribes the webhooks, withdraws the token's permissions
  (`DELETE /me/permissions`) and — only when the Admin asks — deregisters the number from the Cloud API;
  then forgets locally the token, ids, PIN, snapshot, templates and windows **whatever Meta answered**.
  The Admin sees each step (`done` / `skipped` / `failed`); a token Meta already refuses needs none.
  Delivery history stays with the conversations.

## Isolation and secrets

Every secret of a client is in `client:<id>` scope of the secret store; no response carries a token, a
PIN or the app secret; the Client's routes take no client id (the session decides); the
`state` is bound to one client and single-use; registers (windows, deliveries, templates) are filtered
by client. The keys written by the connection and not by the Admin API (`whatsapp.registration_pin`,
`whatsapp.fb_user_id`, `whatsapp.connection`, `whatsapp.reauth_required`) are deliberately not in the
credential catalog.

## Admin

`GET /admin/v1/whatsapp/platform` (what is missing, the three URLs to give Meta);
`GET /admin/v1/clients/{id}/whatsapp` (state, ids, quality, templates by status, 7-day deliveries,
recent failures); owner only: `POST .../refresh`, `POST .../templates/sync`, `POST|DELETE
.../templates`, `PUT .../templates/opening`, `POST .../disconnect {release_number}`. The Admin sees
the Portfolio, account, number, ownership, connection and billing state of each client. The credentials
routes **refuse** `whatsapp.access_token`, `whatsapp.phone_number_id` and `whatsapp.waba_id`
(409): only the client's Embedded Signup writes them.

## Existing accounts (migration)

At start, the accounts already connected (token and number resolved for a client) are written to
`wa_accounts` as `legacy_unverified`, with no Portfolio. No secret is changed and nothing is asked of
Meta, so they keep sending and receiving. The Admin and the client see "not verified": the client
reconnects with Embedded Signup, which verifies the owner and replaces the token. Old secrets are
overwritten only after the new connection fully succeeded; a failed reconnection restores the previous
row. A client configured by environment variables in `client.yaml` (`token_env`...) keeps working the
same way until it reconnects; remove those variables only after its page shows "verified by Meta".

## Needs a real Meta account (not provable by tests)

- App Review / Advanced Access for `whatsapp_business_management` and `whatsapp_business_messaging`,
  Business Verification, Tech Provider status, app in Live mode.
- That the Embedded Signup configuration returns a code the server can exchange without a redirect URI,
  and that `debug_token` lists the account in `granular_scopes` (the server also accepts the account
  reported by Meta's window, which Meta confirms by letting the token read it).
- That a number registered by Embedded Signup is already `CONNECTED` (then the PIN step is skipped) and that
  registration with the generated PIN works for a number without a previous two-step PIN.
- Template review times and categories; that the opening template is accepted with one variable.
- **Ownership:** that `owner_business_info` is returned for a WABA created or chosen in the Embedded
  Signup, with the permissions the configuration grants. If Meta does not return it, every connection
  is refused (`ownership_unverifiable`), by design: ownership is never confirmed on a guess.
- **Billing:** how a Tech Provider's clients are billed (the payment step inside the Embedded Signup
  configuration, the solution and partner settings) and whether Meta exposes any readable payment
  status. Until Meta confirms that the client pays Meta directly for its own WABA, "confirmed" is
  never shown. Without a payment method, sends fail with 131042 and wait.
- Real delivery of webhooks (statuses, templates, deauthorisation) to the hosted URL.
- The quality / messaging limit tier of the number and Meta's per-recipient marketing limits (131049).
