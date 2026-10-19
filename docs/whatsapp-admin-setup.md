# WhatsApp: what the Admin does on Meta, after hosting

Nothing here needs a code change. Do it once for the platform, then once per client only if you
connect an account by hand. The public base URL below is `SALES_AGENT_PUBLIC_BASE_URL` (https).

## 0. Before anything

- The database schema is at revision `0018` (`sales-agent db upgrade`; no migration was run by this change): it adds `wa_contact_windows`,
  `wa_deliveries`, `wa_templates`. Without a database those registers live in memory (lost at restart).
- The server runs **one worker** (the Embedded Signup `state` is kept in memory, as Gmail's).
- `SALES_AGENT_PUBLIC_BASE_URL` is the public https address; the Admin screen shows the three URLs
  below built from it (`GET /admin/v1/whatsapp/platform`).

## 1. On Meta (developers.facebook.com and business.facebook.com)

1. A **Meta Business Portfolio** for Revansa, **Business Verification** completed.
2. A **Meta app** of type Business with the products **WhatsApp** and **Facebook Login for Business**.
3. **Tech Provider** onboarding for the app (required to onboard other businesses), the app in **Live**
   mode, and **App Review / Advanced Access** for `whatsapp_business_management` and
   `whatsapp_business_messaging`.
4. **Embedded Signup**: create a *Login for Business* configuration from the WhatsApp Embedded Signup
   template (permissions above). Note its **configuration id**.
5. **Facebook Login settings**: Client OAuth login and Web OAuth login on; enforce HTTPS; add your
   public domain to **Allowed Domains for the JavaScript SDK**.
6. **App settings → Basic**: App Domains = your domain; Privacy Policy URL; **Data deletion request URL** =
   `<public>/webhooks/whatsapp/data-deletion`; **Deauthorize callback URL** =
   `<public>/webhooks/whatsapp/deauthorize`.
7. **Webhooks → WhatsApp Business Account**: callback URL `<public>/webhooks/whatsapp`, verify token =
   the value you store in step 2 below. Subscribe the fields `messages`,
   `message_template_status_update`, `template_category_update`, `message_template_quality_update`,
   `phone_number_quality_update`, `account_update`, `account_review_update`. (Revansa subscribes each
   client's account to the app itself, during connection.)
8. **Billing — the client pays nothing to Meta through Revansa's flow only if you arrange it**: in
   Business Manager set up a payment method and share your **credit line** with each client's WhatsApp
   account (or have the client add its own payment method in WhatsApp Manager). Not automated by
   Revansa. Without billing, sends fail with 131042 and wait.

## 2. In Revansa (Admin → Settings → Integrations, owner)

| Key | Where it comes from |
| --- | --- |
| `whatsapp.app_id` | Meta app id (public) |
| `whatsapp.config_id` | Embedded Signup configuration id (public) |
| `whatsapp.app_secret` | Meta app secret (secret; also signs every webhook) |
| `whatsapp.verify_token` | any long random string, the same as in step 1.7 |
| `whatsapp.api_version` | optional, e.g. `v21.0` |

When all four are stored, `GET /admin/v1/whatsapp/platform` says `ready: true` and clients on WhatsApp
see **Connect WhatsApp**. Check that Meta's webhook handshake succeeds (Meta calls
`GET <public>/webhooks/whatsapp`).

## 3. Per client

The client connects itself (Client space → Connections → Channels). The Admin then, on the client's
page, **WhatsApp Business**:

1. **Ask Meta now** — checks the number (quality) and that the webhook subscription is in place.
2. **Message templates** → *Read from Meta*, or *Submit a template*: a template with exactly **one body
   variable** inside a sentence (e.g. `Bonjour, un message de <Business> : {{1}} Merci de répondre ici.`,
   one example value). Wait for **APPROVED** (shown by webhook or by *Read from Meta*), then
   **Use to open conversations**. Until then the agent only answers people who write first.
3. Keep an eye on *Recent delivery failures* and the number's quality.

## 4. Connecting an account by hand (without Embedded Signup)

Store for the client `whatsapp.access_token` (a permanent System User token), `whatsapp.phone_number_id`
and `whatsapp.waba_id`, subscribe the account to the app yourself, and register the number. This path
exists but gives no automatic reconnection or revocation.

## 5. Check list after hosting (a real account is required)

- [ ] Meta's webhook handshake passes; a message sent to a test number arrives in a conversation.
- [ ] A client completes **Connect WhatsApp** with a real number; `GET .../whatsapp` shows `connected`.
- [ ] A template is submitted, approved, chosen as opening; a first message to a new person is delivered.
- [ ] Delivery statuses appear on the messages; a blocked number shows `failed` with a code.
- [ ] **Disconnect** shows `done` for the steps; Meta no longer sends that account's events.
- [ ] Removing the app from the Facebook account triggers the deauthorisation call and forgets the client.
