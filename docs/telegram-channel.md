# The Telegram channel

A client talks to its prospects through **its own Telegram bot**. Revansa sends with the Bot API and
receives what people write through a webhook. Nothing is shared between clients: the bot, its token
and its webhook secret are each client's own. Which clients may use the channel at all is the Admin's
decision, like every channel.

## What Telegram can and cannot do

**A bot cannot write to a person who has never started it.** There is no API, no template and no
paid tier that changes this: a message to someone who has not pressed **Start** on the bot is refused
by Telegram (`403 Forbidden`). It also cannot look a person up by phone number, e-mail or `@username`.
So a prospect who is only a row in Google Sheets or a record in HubSpot **cannot be messaged on
Telegram until they say hello to the bot**. That is Telegram's rule, not a business restriction of
Revansa, and everything below is how Revansa works with it instead of failing on it.

| Situation | What happens |
|---|---|
| A prospect in the sheet/CRM who has not started the bot | They **wait**. They are not planned (no quota is spent on them), not paused, not failed, and the Brain is never asked about them. The client sees them in the invitations list. |
| They press Start on their **personal invitation link** | They are attached to *their* row (the link carries their signed id). Their Telegram chat is saved on the row. From then on they are an ordinary prospect: the Engine replies to them, plans first contact and follow-ups, and a follow-up is sent in the same chat. |
| They write to the bot with no link, and the client does not take strangers | The bot asks them to share their phone number with one button. If Telegram confirms the number is theirs and **a prospect holds that number**, they are attached to that prospect (same result as the link). If nobody holds it, the bot says so and nothing is created. |
| They write to the bot, and the client *does* engage strangers (`inbound.unknown_contacts.policy: engage`) | The Engine creates a prospect from the chat (`inbound-<day>-tg<chat id>`, named after their Telegram name), keeps the message and replies, within the client's daily cap for new contacts. This is the **spontaneous-message journey**. |
| They tell the bot `STOP` (or `/stop`, or the client's own words) | Opted out, immediately and everywhere (see Consent). |
| They block the bot | Telegram refuses the next send (`403`): a permanent failure, so the Engine pauses that prospect, as for any person who cannot be reached. |

The operational consequence is one thing the client has to do: **send each waiting prospect their
invitation link** by whatever means it already has for that person (SMS, WhatsApp from a personal
phone, a mail merge, a call). `GET /client/v1/channel/invitations` lists them with phone, e-mail and
link. Revansa does not send those links itself: there is no channel configured for that, and the
client may choose any.

## What the client does (Naga)

1. **Choose Telegram** — `PUT /client/v1/channel`, body `{expected_version, channel: "telegram"}`;
   optionally `telegram: {contact_prompt, contact_button, not_found_text}` (the words the bot uses
   before it knows who it is talking to, in the client's customers' language). Offered only if the
   Admin allowed it.
2. **Create the bot** with `@BotFather` and give its token — `PUT /client/v1/channel/credentials`,
   body exactly `{"bot_token": "..."}`. Write-only: no response, error, audit event or log carries it.
3. **Connect it** — `POST /client/v1/channel/connect`. The platform generates the webhook secret,
   tells Telegram to deliver this bot's messages to `<public address>/webhooks/telegram` with that
   secret, and stores the secret (write-only) only once Telegram agreed. Safe to repeat: the secret,
   and the links already sent, stay. A new token drops the connection (another bot is not the one
   that was connected) and invalidates the links of the old one.
4. **Test** — `POST /client/v1/channel/test`: asks Telegram who the bot is and where it delivers.
   Sends nothing to anyone. `ok: true` means "the token works and Telegram delivers to this
   deployment", at that moment, and nothing more.
5. **Start the agent** — the readiness check `channel_ready` asks for the token, then the connection.

Then it sends the invitation links. Everything else is the usual journey (Sheets or HubSpot as the
source, the approval mode, the daily cap, the follow-up cadence the Brain sets).

## What the Admin does

- Allows the channel: `PUT /admin/v1/settings/channels` (owner) or `PUT /admin/v1/clients/{id}/channels`.
  It is judged **every time a client's configuration is resolved**: a channel taken away afterwards
  stops the client (no sending, no processing; Telegram is still answered `200` so it does not retry
  forever, and the message is dropped, as for a client missing a credential).
- Sets **`SALES_AGENT_PUBLIC_BASE_URL`** (for example `https://revansa.example`) — the https address
  Telegram can reach. Without it a bot can be saved and tested but not connected (409, "contact your
  administrator"). Telegram only delivers to https on port 443, 80, 88 or 8443.
- Does *not* hold any Telegram credential: unlike WhatsApp/Meta, there is no platform-level app.

## Security

- **Authentication.** Telegram does not sign a request: it repeats the `secret_token` given at
  connection in `X-Telegram-Bot-Api-Secret-Token`. Each client's bot has its own 256-bit secret, so
  the header both authenticates the request and says which client it is for. A request with no known
  secret is refused whole (`401`) before its body is read; compared in constant time against every
  secret.
- **Isolation.** The routing key is the bot's numeric id, and a bot belongs to **one** client:
  refused at connection (`409`, never saying whose), when a runtime is built, and when the webhook
  table is rebuilt (a client set up on another's bot can never take its messages or replace its
  secret). Each client's sends go through its own token only.
- **No identity from text.** A person's words are never an identity. Only two things link a chat to a
  prospect, both verified outside the message text: a `/start` payload that only this client's
  secret could have signed (HMAC-SHA256, 96 bits, bound to the prospect id), and a phone number that
  Telegram marks as the sender's *own* account (`contact.user_id == from.id`).
- **First chat wins.** A prospect is attached to one chat only. A forwarded link, or a second person
  sharing the same number, never replaces or takes over an existing link (audited as
  `inbound.link_refused`).
- **Only private chats with people.** Groups, channels, other bots and edited messages are
  acknowledged and ignored. Phone numbers shared by a person are not kept in the conversation.
- **Duplicates.** A message is identified by `<chat id>:<message id>`; the Engine stores it once however
  often Telegram redelivers it. A failure to store answers `500` so Telegram redelivers.
- **The token never leaves.** It is in no response, log, audit event or error (`TelegramApi` scrubs it;
  tests check this).

## Consent and opt-out

A Telegram chat is an identity like a phone number or an address: `telegram:<chat id>` is one of the
contact keys of the consent registry. An opt-out from a Telegram conversation is recorded against the
person's chat **and** their phone and e-mail, so it binds every record of the same person in the CRM,
and a person who opted out by phone is blocked on Telegram too once linked. Opt-out words are matched
as whole messages (`STOP`, `/stop`).

## Where the chat is kept

The chat id is the one contact detail Revansa *learns* rather than being given, so no source holds it:

- **Google Sheets** — a `telegram_chat_id` column on the `Prospects` tab (system-owned, optional).
  A sheet that does not have it gets it, to the right of the client's own columns, the first time a
  chat has to be written; it can be renamed through `column_mapping` like any other. Nothing already
  there is touched.
- **HubSpot / any CRM** — on the prospect's *state* in Revansa's own operational store (never in the
  CRM). A person who wrote first and is not in the CRM is a local prospect there, with their chat as
  contact. No migration is needed: the state is a JSON document that simply gains a key.

## Limits (known, not hidden)

- **No cold outreach, by Telegram's design.** See the first section. Prospects reachable only by
  e-mail or phone need their link sent through that other means.
- **No automatic link delivery.** Revansa lists the links; it does not send them. (A client on
  Telegram has no second channel configured to send them with.)
- **Strangers engaged, known people duplicated.** On a client that engages strangers, a prospect from
  the sheet who writes to the bot *without* their link is a stranger like any other: a separate
  prospect is created from their chat, and the two are not merged. Their invitation link (or, on a
  client that does not engage strangers, the phone button) is what recognises them.
- **First message after Start is a reply.** The person's `/start` is stored (empty text) and the Brain
  is asked to answer it like any inbound message, so it replaces a first contact that was planned for
  them. A Brain that handles an empty inbound text badly will say something odd: this was designed
  for, but not tested against the production Brain.
- **Plain text only.** No Markdown/HTML, buttons, or attachments are sent; attachments received are
  recorded as a reference (kind and Telegram file id), never downloaded. A message longer than 4096
  characters is sent in pieces; if a later piece fails after an earlier one arrived, the action stays
  in `SENDING` for a human (the outcome is partly unknown).
- **No templates.** A WhatsApp template draft has nothing to say on Telegram and is refused.
- **No reply threading.** Replies are not sent as Telegram "replies" to a given message.
- **No user interface.** The Client API is complete; the web console is still a prototype with no
  channel screen (e-mail has none either).
- **Webhook only.** Telegram refuses a bot webhook and polling at once: a bot connected here cannot
  also be read by another program with `getUpdates`.
- **A bot's token can be moved to another deployment only by connecting it again there** (it takes
  the webhook from the first).

## Tested by automated tests, and what is left to test for real

Automated (every external system simulated): `tests/unit/test_telegram_core.py` (the Core's rules),
`tests/integration/test_telegram_channel.py`, `test_telegram_webhook.py` (sending, parsing, invitations,
the connection probe, against a mocked HTTP transport), `test_sheets_telegram.py` (the sheet),
`tests/app/test_telegram_sourcing.py` (CRM + consent, memory and SQL stores),
`tests/app/test_telegram_channel_api.py` (the Client API) and `tests/app/test_telegram_application_e2e.py`
(the whole application: Sheets in memory, Telegram as a double, the real webhook route and parser,
the real scheduler, restart, isolation, the Admin's authority, failures).

**None of it proves that a real bot, Telegram or a real person's app behaves like the doubles.** To do
by hand with a real bot, a public https address and a Telegram account you own:

1. Create a bot with BotFather; set `SALES_AGENT_PUBLIC_BASE_URL`; as the client, save the token and
   `POST /client/v1/channel/connect`; `POST /client/v1/channel/test` must say `ok`.
2. Add a Sheets prospect whose phone is the one of your Telegram account; start the agent: nothing is
   sent (it waits). `GET /client/v1/channel/invitations` must list it with a link.
3. Open the link in Telegram and press Start: the row must get its `telegram_chat_id`, and a reply must
   be proposed; approve it, and the message must arrive in that chat.
4. Write a text, then send `STOP`: the prospect must become `opted_out` and nothing more is sent.
5. From a second account, write to the bot with no link: it must ask for the number; share it: with a
   number no row holds, it must say so; with a number a row holds, it must link.
6. Block the bot from your account, approve a message to you: the action must fail and the prospect
   be paused.
7. Connect the same token for a second client: it must be refused.
8. Take the channel away in Admin: no message is sent, and Telegram must keep getting `200`.
