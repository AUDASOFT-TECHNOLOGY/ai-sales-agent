# Engine design note

Status: **implemented** in `sales_core/engine/` (plus `approval/` and `safety/`). Section 8 lists the
contract changes that were approved and made; section 10 records what the implementation decided.

The Engine (`sales_core/engine/`) is the orchestrator. It owns *process*; the client Brain owns
*commercial judgement*. It talks only to ports (`SalesRepository`, `Brain`, `MessagingChannel`,
`Clock`) and to the pure Core modules (`planner`, `safety`, `approval`). It imports no vendor code.

```
                      ┌────────────── Engine (one instance per client) ──────────────┐
Google Sheets         │                                                              │
 (SalesRepository) ◄──┤ A. prepare ── planner ─► Brain ─► approval gate              │
                      │ B. dispatch ─ safety ──► MessagingChannel ─► record ─► update │
WhatsApp webhook ─────┤ C. inbound ── safety ──► Brain ─► approval gate ─► (B)        │
 (via sales_app)      └──────────────────────────────────────────────────────────────┘
        OpenAI is behind the Brain's LLMProvider; WhatsApp is behind MessagingChannel.
```

## 1. Exact flow

Outreach is two decoupled phases, because a human approval can take hours or days.

### Phase A: prepare (daily cycle)

1. **Plan.** Read prospects and existing actions from the repository, call `plan_daily_actions`
   (max 20 per client per local day, strict tiers). Persist the returned `PLANNED` actions in
   **one batch before any Brain call**, so the quota is reserved even if the process dies.
2. **Load context.** For each planned action: prospect, `get_conversation`, `ClientContext`
   (from client config), `now` from the `Clock`. Build a `BrainRequest`.
3. **Safety check #1** (cheap, before spending an LLM call): skip opted-out, paused, closed or
   uncontactable prospects.
4. **Call the Brain** (`Brain.decide`). It returns a `BrainDecision`.
5. **Apply the decision** (Core interprets, Brain never writes anything):

   | Decision | Engine does |
   |---|---|
   | `SEND_MESSAGE` | store the draft on the action, status → `PROPOSED` |
   | `SCHEDULE_FOLLOW_UP` | no message; set the prospect's next action; action resolved |
   | `HANDOFF_TO_HUMAN` | prospect → `PAUSED`; action resolved; flagged in the Sheet |
   | `CLOSE` | prospect → `CLOSED`; action resolved |
   | `NO_ACTION` | action resolved; prospect's next action cleared |

6. **Approval gate** (see section 4): decides what happens to a `PROPOSED` action.

### Phase B: dispatch (runs after Phase A and periodically, e.g. every few minutes)

7. **Pick `APPROVED` actions.** The text sent is whatever is stored on the action, so a human
   who edited the draft in the Sheet is honoured.
8. **Safety check #2** on freshly re-read prospect state (it may have opted out, or replied,
   since approval). Blocked → `CANCELLED` (permanent reason) or left `APPROVED` (temporary
   reason such as quiet hours).
9. **Write `SENDING` first**, then call `MessagingChannel.send` with the action's idempotency
   key (section 3).
10. **Record the result:** `SENT` / retry / `FAILED`; append the outbound `Message` to the
    history; append audit events.
11. **Update the prospect** (only on `SENT`): `last_contacted_at`, `follow_up_count`,
    `NEW → ACTIVE`, `stage` from the decision, and the next action (`next_action_type`,
    `next_action_due_at`) from the decision, or cleared if the Brain gave none.

Prospect state changes wait for `SENT` on purpose: a rejected or failed proposal must leave the
prospect exactly as it was.

### Phase C: inbound replies (event-driven, bypasses the quota)

The WhatsApp webhook (in `sales_app`) hands the parsed `InboundMessage` to `engine.receive_inbound`
(steps 1-2, fast), then runs `engine.process_inbound` in the background (steps 3-5):

1. **De-duplicate** on the provider message id (webhooks are redelivered). The stored
   `message_id` is derived from it, so a duplicate append is detected and ignored.
2. Append the message to the history; set `last_inbound_at`.
3. **Opt-out check** (safety, configured per client): opted out → prospect `OPTED_OUT`,
   open outreach `CANCELLED`, stop. No Brain call.
4. Cancel the prospect's open outreach actions (context changed) and clear the stale schedule.
5. Create a `REPLY` action (`client:prospect:reply:<inbound id>`), process it at once through
   steps 2–6, and dispatch if the gate allows. In approval mode it stops at `PROPOSED` like any
   other message; "immediately" means *no waiting for the daily cycle*, not *no approval*.

`REPLY` actions are excluded from the 20-action quota by the planner. The webhook only stores
the message and returns 200; processing is a background task, and each daily cycle re-scans for
stored inbound messages that have no `REPLY` action, so a crash loses nothing.

## 2. Responsibilities

| Brain (per client, no I/O of its own) | Core Engine (generic) |
|---|---|
| Offer, tone, **language**, wording | Selecting who to work on (planner), the daily cap |
| Qualification, objection handling | Loading context; persisting every state change |
| **Follow-up cadence**: whether, when, what type | Validating and applying decisions |
| Whether to close or hand off | Approval mode, safety rules, idempotency |
| Stage labels (opaque to the Core) | Sending, retries, error handling, audit |
| Prompts and templates; talks to `LLMProvider` | Deciding *nothing* about content |

- **Cadence lives in the Brain** (its `rules.yaml` and logic). The Planner only asks "is
  something due?"; it never knows why. The Brain reads `follow_up_count` and history and answers
  with the next date, or `CLOSE` after its own maximum.
- **OpenAI is an implementation detail behind `LLMProvider`**, which only Brains hold. The Core
  and Engine never call a model. **Google Sheets** is behind `SalesRepository`, **WhatsApp**
  behind `MessagingChannel`. Swapping any of them changes one adapter.

## 3. Idempotency (no duplicate sends)

Layered, because Google Sheets has no transactions and the WhatsApp Cloud API has **no native
idempotency key**, so the Engine itself must guarantee at-most-once delivery:

1. **Planning:** key `client:prospect:type:local-date`; the planner skips existing keys, open
   actions and prospects already touched today; the repository rejects two actions with one key.
2. **`SENDING` before send:** the state is persisted *before* the channel call. Only `APPROVED`
   actions are ever picked up, so a second worker or a re-run cannot pick the same action.
3. **Crash between the channel call and the result write:** the action is left in `SENDING`.
   The Engine **never auto-resends `SENDING`**. It raises an audit event
   (`action.send_unconfirmed`) for a human, who checks the WhatsApp side. We prefer a missed
   message to a duplicate to a real prospect.
4. **Bookkeeping is replay-safe:** outbound `message_id` is derived from the action id, so
   re-writing history after a partial failure is detected, not duplicated.
5. **Single writer per client:** one Engine instance per client, serialised by an in-process
   lock (webhook tasks and the daily cycle share it). Sufficient for the MVP.

## 4. Where approval mode fits

Between the Brain and the channel. The `approval` module is a pure policy that maps
`(mode, PROPOSED action)` to the next state:

| Mode | Result |
|---|---|
| `approval` | stays `PROPOSED`; a human sets `APPROVED` or `REJECTED` in the Sheet's Actions tab; nothing is sent until then |
| `autonomous` | `PROPOSED → APPROVED` immediately (later phase) |
| `dry_run` | Brain is called, only audit events are written, nothing is stored or sent |

Humans act only by editing the Sheet; they never call the Engine. Phase B reads their decisions.
Rejected actions are terminal, and the prospect keeps its schedule.

## 5. Where safety rules fit

`sales_core/safety` holds pure checks the Engine calls at two checkpoints (before the Brain,
before the send). Safety can only **block or defer**, never write content, and the Brain cannot
bypass it. Planned checks: opted-out / paused / closed, missing contact data, quiet hours in the
client's timezone, channel constraints (e.g. WhatsApp's 24-hour window requires a template
outside it), and a final daily-cap check. Opt-out *keywords* are client configuration (they are
language-specific); *enforcement* is Core.

## 6. Errors

Principles: **fail closed** (when unsure, do not send), **isolate** (one bad prospect never stops
the cycle), **record** (every failure is an audit event and visible in the Sheet).

| Failure | Handling |
|---|---|
| Brain / LLM raises, or returns an invalid decision | Nothing is sent. Action stays `PLANNED` with `attempts + 1` and `last_error`; retried next cycle up to 3 times, then `FAILED`. Transient LLM retries belong inside the provider adapter. |
| Repository read fails | Abort the run before doing anything. |
| Repository write fails before `SENDING` | Do not send. |
| Repository write fails after a send | Action stays `SENDING` (section 3.3); nothing is resent. |
| Send returns `FAILED_TRANSIENT` | `attempts + 1`, back to `APPROVED`, retried by a later dispatch; `FAILED` after 3. |
| Send returns `FAILED_PERMANENT` | `FAILED`; prospect → `PAUSED` for a human (e.g. bad number). |
| Safety blocks | `CANCELLED` (permanent) or left `APPROVED` (temporary), with reason. |
| Webhook duplicate / unknown sender | Ignored / stored and flagged for a human. |

## 7. How the Engine knows a conversation is closed or needs a follow-up

Only from explicit state, never by reading message text:

- **Closed:** `status = CLOSED` (Brain `CLOSE` or a human edit) or `OPTED_OUT` (safety).
  `PAUSED` means "waiting for a human" (handoff or permanent send failure). None of these are
  ever selected by the planner.
- **Needs a follow-up:** `schedule.next_action_type` and `next_action_due_at` are set, the
  prospect is `NEW` or `ACTIVE`, and the time has come. The planner reads exactly this.
- **Idle:** `ACTIVE` with no next action. Nothing happens until the prospect writes or a human
  acts. A Brain that wants a follow-up must say so.
- **Awaiting reply:** last message is outbound; the pending follow-up is the safety net.

## 8. Contract changes (approved and implemented)

1. **`ActionStatus.COMPLETED`**: an action resolved without sending (schedule, handoff, close,
   no-action). Proposed: it **counts toward the daily 20** (a one-line planner change), so a Brain
   that keeps answering "no action" cannot cause unbounded LLM calls.
2. **`BrainDecision.next_action_type`** (default `FOLLOW_UP`): today a Brain can only give a date,
   so it could never schedule reminders or callbacks, which the planner supports.
3. **`SalesAction` carries the post-send prospect update** (`new_stage`, next action type/date):
   the decision must survive from proposal to approval, possibly days and a restart later, and a
   human should be able to edit it in the Sheet.
4. **`SalesRepository.find_prospect_by_contact(phone/email)`** for mapping inbound messages.

## 9. Layout

```
sales_core/engine/
  engine.py      Engine(client, repository, brain, channel, clock, mode); prepare(), dispatch(), handle_inbound()
  applier.py     pure: (decision, prospect, action, now) -> new prospect + action states
  errors.py      failure classification and attempt limits
```
Built per client by `sales_app/bootstrap.py`; tested end to end with the fakes plus a scripted
Brain and `FakeMessagingChannel.fail_next`.

## 10. Implementation notes

- **Entry points** (`Engine`, one per client, one lock): `prepare()`, `dispatch()`, `run_cycle()`,
  `receive_inbound()` (fast webhook path), `process_inbound()`, `recover_pending_replies()`.
  Each returns a `RunReport` (`outcomes`, `errors`); `receive_inbound` returns an `InboundReceipt`.
- **Contracts added:** `ActionStatus.COMPLETED` (counts toward the quota),
  `BrainDecision.next_action_type` (defaults to `follow_up` when a date is given),
  `SalesAction.next_stage / next_action_type / next_action_due_at`, `InboundMessage`,
  `SalesRepository.find_prospect_by_contact`.
- **Idempotency of replies:** every inbound message gets exactly one `REPLY` action keyed
  `client:prospect:reply:<message id>`; an opt-out gets one too (status `COMPLETED`, no Brain call).
- **Opt-out:** whole-message match against `EngineConfig.opt_out_keywords` (default `{"stop"}`;
  clients add localized words in config).
- **Permanent send failure** sets the prospect to `PAUSED` (lifecycle only); stage and schedule are
  never touched by a failed or rejected send.
- **Audit vocabulary** lives in `engine/events.py`. Events are buffered per action and written in
  batches; if a write fails they are kept and retried at the next flush.
- **Known limits:** no quiet-hours check yet; a rejected proposal is re-proposed the next day
  (the prospect keeps its schedule); `recover_pending_replies` reads one conversation per prospect
  with an unanswered inbound message; a `SENDING` action stays flagged until a human resolves it.
