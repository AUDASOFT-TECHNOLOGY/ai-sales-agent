# The WhatsApp channel: what exists, what is missing

## Exists (code)
- Cloud API channel and webhook parser: `src/sales_integrations/whatsapp/`; provider descriptor
  `src/sales_app/wiring/providers/whatsapp.py` (routing by `phone_number_id`).
- Credentials (Admin only): global `whatsapp.verify_token`, `whatsapp.app_secret`, `whatsapp.api_version`;
  per client `whatsapp.access_token`, `whatsapp.phone_number_id` (unique across clients).
- Admin: write-only credential routes, `POST /admin/v1/clients/{id}/integrations/whatsapp/test` (real
  Graph API read of the phone number record) and, new, `GET .../whatsapp/status` (stored pieces only,
  never claims "connected"; `verified_with_meta` is always false).
- Client: supplies only `whatsapp_number` (profile); a changed number sets `whatsapp_reconnect_required`.
- Tests: `tests/integration/test_whatsapp_channel.py`, `test_whatsapp_webhook_parser.py`,
  `tests/app/test_whatsapp_admin_status.py`.

## Absent / depends on the provider
- Meta onboarding (Business account, app, WABA, phone number registration, permanent token, webhook
  subscription pointing to `<public>/webhooks/whatsapp`, approved message templates for contact
  outside the 24h window): done by the Admin on Meta; nothing in the platform automates or checks it.
- The verify token and app secret are proven only when Meta calls the webhook: no test exists.
- No disconnect for WhatsApp (removal = delete the client credentials); no template management.
- `channel_status.py` returns READY for WhatsApp unconditionally (shared file; see report).
