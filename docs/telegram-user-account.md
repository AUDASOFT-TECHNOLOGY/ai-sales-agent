# Telegram from the client's own account

Status: **implemented, not yet validated against the real Telegram.** The client signs in with its own
Telegram account and Revansa writes to its prospects from it, replies, follows up and honours `STOP`.
Everything below says what is built, what Telegram's rules allow and forbid, and what only a test with a
real account can prove. The bot (`docs/telegram-channel.md`) stays as the other mode; a client chooses.

Code: `src/sales_integrations/telegram/account.py` (the port, the channel, the inbox),
`telethon_client.py` (the real MTProto client, loaded only when used), `src/sales_app/telegram_account.py`
(sign-in, session storage, state), `wiring/providers/telegram.py` (mode `account`).

## 1. What Telegram's own rules say (read on 2026-10-19)

- Terms of Service (telegram.org/tos): the user agrees not to "Use our service to send spam or scam
  users", and Telegram "prohibits data scraping".
- Anti-spam FAQ (telegram.org/faq_spam): "Please only contact people if you're sure that they are
  expecting messages from you." Commercial and unsolicited advertising is singled out as unwelcome;
  reports go to human moderators; the account then becomes **limited** (first time usually a few days,
  repeated offences longer, "continued unwanted messaging to strangers can mean losing the ability to
  message them permanently"). A limited account can still message saved contacts and reply to people
  who wrote first.
- API Terms (core.telegram.org/api/terms): own `api_id` (2.1); "interfering with the basic
  functionality" is forbidden, including "making actions on behalf of the user without the user's
  knowledge and consent" (1.4); breaches lead to API access being discontinued for the application
  (4.1-4.2). Flood limits are not in these pages: they show up at run time as `FLOOD_WAIT_x` and
  `PEER_FLOOD`.

**Consequence.** Cold outreach from a client's account to people who never expected it is what Telegram
defines as spam. It is technically possible and contractually forbidden, and the risk falls on the
client (its personal account is limited or lost) and on Revansa (one `api_id` serves every client and
can be cut for all of them). **Revansa does not offer it**: no number rotation, no contact-import tricks,
no throttling designed to dodge detection.

## 2. What is built

| Need | How it works |
|---|---|
| **Use the client's own identity** | The client signs in with Telegram itself (phone number, the code Telegram sends to its app, its two-step password if any) from its channel page. Revansa keeps the resulting **session** only. |
| **Write first, compliantly** | Only to a prospect the client marked, in its own source column (`consent_field`, default `telegram_consent`, value "yes"/"oui"/…), as expecting the contact, or who wrote first. Anyone else *waits*: not failed, not paused, no quota spent. The mark is the client's statement; Revansa cannot verify it. |
| **Find the person** | By the phone number the client holds for them: Telegram is asked who holds it (this adds the number to the client's Telegram contacts, as writing to a number requires). A number Telegram does not know is not asked again for a day. Nothing is guessed from a name. |
| **Rationing** | At most `max_new_conversations_per_day` (1 to 30, default 10, per client, UTC day, counted in the database) **first** messages. A reply or a follow-up on a conversation the person started is not rationed. |
| **Receive replies** | The inbox polls the account every two minutes and reads **only** conversations with the client's own prospects: other chats (family, friends, groups, channels, bots) are never opened, stored or logged. Nothing written before the connection is read. The position is kept per account in the database, moves only past messages the Engine stored, and survives restarts. |
| **Attach replies to prospects** | A message is `<account id>:<message id>`. The sender is matched by the account id linked to the prospect, else by the phone number Telegram itself reports for the sender. A person who is not a prospect is never engaged on this channel (unlike the bot, there is no "spontaneous conversation" for a personal account). |
| **Conversations, follow-ups** | Unchanged Engine behaviour (Brain, approval, cadence): a follow-up goes to the same account. |
| **Unsubscribe** | `STOP` (or the client's own opt-out words) is honoured immediately and everywhere, as for every channel: the account id is a contact key of the consent registry, and an opted-out prospect is never written to again. |
| **Errors** | Telegram unreachable: transient, retried. `FLOOD_WAIT`: deferred, not counted as an attempt. `PEER_FLOOD`: **stops every first message for that client** (replies go on) and says so on both screens; it is lifted only by the Admin once Telegram has lifted it (@SpamBot) and is never "tried again to get through". A person who blocked the account or cannot be reached: permanent for that person (the Engine pauses them). A timeout during a send: unknown outcome, left for a human, never retried. |
| **Reconnection** | A session Telegram ended (revoked from "Devices", password changed, account banned) is detected at the next use: the client is shown "sign in again", nothing is sent, and signing in again resumes where it was. |
| **Disconnection** | The client (or the Admin) disconnects: the session is ended at Telegram (`log_out`) and forgotten here whatever Telegram answers; if Telegram could not be told, the client is told to end it from Telegram's Devices list. |

## 3. Security

- **The session is a full access key to the account.** It is stored encrypted in the client's scope of
  the secret store (`telegram.user_session`), never returned by an API, never logged, never in an error.
  Those keys are not in the Admin's credential catalogue: only the sign-in flow writes them.
- **The code and the password are not kept**: they go to Telegram and are forgotten. Between two steps
  of a sign-in only a temporary session and the code's hash are held, in memory, for ten minutes, with
  five attempts and five sign-ins per hour per client. A request body is never echoed in an answer.
- **One account, one client.** The account id is stored unique across clients (`telegram.user_account`):
  a second client signing in with the same account is refused and the session just made is ended.
- **Isolation.** Each client has its own session, key by client id. A sign-in, a read, a send and a
  disconnect use only the client in the cookie's session. The inbox reads a position named by a digest of
  the account id.
- **After a restart** nothing needs rebuilding: sessions and positions are in the stores, and a sign-in
  under way simply has to be started again (one worker, in memory).
- **One short connection per operation.** No socket stays open between polls.

## 4. Limits, stated plainly

- **No cold outreach.** Not built, not offered, whatever the client wants. A prospect nobody marked as
  expecting the contact and who never wrote waits forever; the alternative that always works is the bot
  with a personal invitation link sent through a channel the client already owns.
- **The consent mark is the client's word.** Revansa records it only as the presence of "yes" in a
  column; it cannot prove the person agreed. The client is told so in its own interface.
- **A limited or banned account is possible**, at any time, even when every rule above is followed:
  Telegram decides, and its thresholds are not published. The rationing lowers the risk; it does not
  remove it. The client uses a personal account at its own risk and Revansa says so.
- **A shared `api_id`.** All clients use the platform's application. If Telegram restricts it, every
  client on this mode stops. The operator registers it with its own account (my.telegram.org).
- **Only text is sent and read as text.** Files that arrive are recorded as a kind, never downloaded.
- **A prospect without a phone number** cannot be found, whatever their mark says: the client sees them
  as "marked, but no phone number".
- **A person whose number Telegram hides** from Revansa's account cannot be found by number; they are
  matched once they write first and Telegram shows their number or they were already linked.
- **Not validated against the real service.** The MTProto client (`telethon_client.py`) was written from
  Telethon's documented API (checked against 1.45: the error classes and calls it uses exist) and the
  rest is tested with a fake (`FakeAccountClient`). **No real sign-in, send, read, flood or limit has been
  tried.** Possible surprises: how Telegram resolves a number after a contact import, which numbers are
  shown to the account, how fast `FLOOD_WAIT` comes, and how `get_input_entity` behaves for a session
  string that has no address book (the code loads contacts and recent chats on demand). Prove it once
  with a throw-away account before any client uses it (section 6).
- **Messages edited, deleted, read receipts and typing indicators** are not handled or sent.
- **Positions only cover the 200 most recent private conversations** of the account at each poll.

## 5. What the Admin configures after hosting

1. Install the project as usual: `telethon` is a normal dependency (`uv sync`). The platform shows
   "the Telegram library on the server (telethon)" among what is missing otherwise.
2. Register a Telegram application with the **operator's own** account at <https://my.telegram.org>
   (API development tools). It gives an `api_id` and an `api_hash`.
3. Store them in **Settings → Integrations** (owner): `telegram.api_id` (public, shown) and
   `telegram.api_hash` (write-only). `GET /admin/v1/settings/integrations/telegram-account/status`
   says what is still missing, never a value. No restart is needed.
4. A database must be configured (the daily count of new conversations and the reading position live
   there). Without it the channel says "Revansa is setting this up" and sends no first message.
5. Allow the Telegram channel to the client (**Settings → Channels** or the client's channels).
   `SALES_AGENT_PUBLIC_BASE_URL` is **not** needed for this mode (no webhook), only for the bot mode.
6. The client then, from its channel page: chooses "Use my Telegram account", signs in, adds the
   consent column to its prospects (the page names it) and starts the agent.
7. Revansa runs one worker (as for Gmail): do not start `uvicorn --workers N`.

Per client, the Admin can see where the account stands (connected, to reconnect, limited), disconnect
it, and lift a Telegram limit once @SpamBot says the account is free:
`GET .../integrations/telegram/account`, `POST .../account/disconnect`, `POST .../account/clear-limit`.

## 6. To do once by hand with a throw-away Telegram account (and a second account as the "prospect")

1. Store `telegram.api_id`/`telegram.api_hash`; the platform status must say `configured`.
2. Choose the account mode as a test client, sign in (code, then password if the account has one). The
   state must read "Connected"; the account must appear in Telegram's Devices list as "Revansa".
3. Put a prospect whose phone is the second account's, with `telegram_consent` = `yes`, start the agent in
   approval mode: the second account must be found, a first message proposed; approve it: it must arrive.
4. Reply from the second account: the reply must be stored on that prospect and a answer proposed. Reply
   `STOP`: nothing more must be sent.
5. Write from a third account that is not a prospect: nothing of it may appear in Revansa.
6. Set the daily maximum to 1: the second first message of the day must be deferred, not failed.
7. End the session from Telegram's Devices list: the client must be asked to sign in again and nothing
   must be sent meanwhile. Sign in again: it must resume.
8. Disconnect from Revansa: the session must disappear from the Devices list.
9. Never test `PEER_FLOOD` on purpose.
