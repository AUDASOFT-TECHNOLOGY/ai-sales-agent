# Telegram from the client's own account: feasibility study (decision pending)

Status: **study only, nothing implemented.** The need: the client connects its own Telegram account
and Revansa uses it to contact prospects proactively, hold the conversation, follow up and convert.
Today's channel (`docs/telegram-channel.md`) is a Bot API bot, which cannot write first to a person who
never pressed Start. This document says what a user account can and cannot do, and what is proposed.

## 1. Technically possible

A Telegram **user account** can be driven through MTProto (libraries such as Telethon, Pyrogram or
TDLib) instead of the Bot API. Such an account can start a private chat with someone it can resolve
(a public `@username`, a saved contact, a phone number added to its contacts), send messages, receive
replies as events and keep the session. Login is the person's own: phone number, a code Telegram sends
to their app, and the two-step password if one is set. The platform needs an `api_id`/`api_hash` of its
own (Telegram API ToS 2.1: "You must obtain your own api_id for your application"). Revansa would keep
the resulting **session string** (it is a full access key to the account) encrypted in the client's
scope of the secret store, never the phone code or the password.

## 2. What Telegram's own rules say (read on 2026-10-19)

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
  knowledge and consent" (1.4); breaches lead to API access being discontinued for the app (4.1-4.2).
  Flood limits and ban rules are not in these pages (they appear as `FLOOD_WAIT_x` / `PEER_FLOOD`
  errors at run time).

## 3. Consequence for the requested behaviour

**Cold outreach from a client's account to people who never expected it is the behaviour Telegram
defines as spam.** It is technically doable and contractually forbidden. The risk falls on (a) the
client, whose personal account gets limited or lost, and (b) Revansa, whose single `api_id` serves every
client and can be cut for all of them (API ToS 4.2). No workaround is proposed: no number rotation,
no contact-import tricks, no throttling designed to dodge detection.

## 4. Closest authorised solution (proposed, not built)

A **user-account channel limited to people who expect to hear from the client**:

1. *Consent gate.* A proactive first message is sent only to a prospect with a recorded consent for the
   Telegram channel (the consent registry already exists: `docs/consent-registry.md`) or who wrote first.
   A prospect without it waits, exactly as today with the bot: not failed, not paused, no quota spent.
2. *Replies and follow-ups* on conversations the person opened or accepted: allowed without restriction
   beyond the client's quota and STOP handling.
3. *Conservative, honest limits.* A small per-client daily cap for new conversations, the same cap
   machinery as e-mail; `PEER_FLOOD`, a `FLOOD_WAIT` or a "limited" answer from `@SpamBot` stops the
   channel for that client and shows it as `action_required` (never retried to get through).
4. *Connection by the client:* phone number, then code, then 2FA password if any, in one Revansa
   screen; Revansa keeps the encrypted session only. *Revocation:* "Disconnect" logs the session out at
   Telegram and deletes it; the client can also end it from Telegram's Devices list.
5. *Isolation:* one session per client, keyed by client id in the secret store; the listener routes by
   client; a chat id is meaningful only inside its client.
6. *Risks to accept in writing:* the client's personal account is used; Telegram can limit it;
   a unique `api_id` is shared by all clients.

Alternative that already works within the rules: keep the **bot**, and give the prospect a personal
invitation link through a channel the client already owns (e-mail, SMS, QR code on a flyer or website).
It is the only way Telegram offers to reach someone who never contacted you, and it carries no risk
for the client's account.

## 5. What is not verified and cannot be from here

No real MTProto login, send, receive, flood behaviour or limitation has been tried. Anything built must
first be proven with a throw-away account by the platform operator. The numbers above (a first-offence
limit of "a few days") are Telegram's wording, not a measured limit.

## 6. Decision needed

Build (4) with the consent gate (about the size of the Gmail channel: port + Telethon adapter,
session store, login flow, listener, tests with a fake client), or keep the bot with invitation links.
Cold outreach without consent is not offered under either option.
