# Brain-proposed facts and the payment model (Core)

Two generic Core capabilities. Neither knows a client, a market, a product, a price or a payment
provider; both keep the same rule: **the Brain proposes, the Core validates and records.**

The business rule for payment, fixed for the MVP: **the agent never confirms that a payment was made.**
It records what the prospect says and sends. Only a person authorised by the client verifies the
payment and confirms it in the client's CRM/Admin.

## 1. Brain-proposed facts

A Brain that learns something during a conversation returns it in `BrainDecision.proposed_facts`, a
tuple of `FactProposal` (at most 20 per decision). A proposal is a statement, not a record:

| Field | Meaning |
|---|---|
| `key`, `value` | what was learned (free-form, the Brain's vocabulary) |
| `status` | the `FactStatus` the Brain proposes; the Core decides whether that is allowed |
| `source` | the `FactSource` the Brain says it comes from; the Core checks it can be claimed |
| `confidence` | optional, 0..1 |
| `message_id` | the stored message it was read from (a reference, not a copy) |
| `excerpt` | optional; must appear verbatim in that message |

A proposal carries **no time, no author and no identity**. The Brain cannot forge them because it
cannot set them: the Core does.

### The validation boundary

`sales_core.facts.review_proposals` is a pure function. The Engine calls it after the Brain answers
and before the decision takes effect, then records what it accepted through the `FactsWriter` port.
The Brain never holds a writer (an architecture test enforces it, and `BrainRequest` carries none).

Each proposal is accepted or refused on its own, with a reason (`ProposalRejection`):

| Rule | Reason when broken |
|---|---|
| the prospect and conversation are this client's | `wrong_client` |
| key and value are non-empty, at most 128 / 2000 characters, and make a valid `Fact` | `malformed` |
| the key does not start with `core_` (keys only the Core writes) | `reserved_key` |
| the Brain claims only `prospect`, `conversation` or `brain_inference` | `source_not_allowed` |
| the source may carry the status (matrix below) | `status_not_allowed` |
| a `prospect` statement cites a message | `message_required` |
| the cited message is in this prospect's conversation | `unknown_message` |
| a `prospect` statement cites a message the prospect wrote | `not_a_prospect_message` |
| an excerpt, if given, is in the message | `excerpt_not_in_message` |
| a different value does not displace a *confirmed* fact from `system`, `human` or `client_data` | `conflicts_with_authoritative_fact` |

| Source a Brain may claim | confirmed | discovered | hypothesis |
|---|---|---|---|
| `prospect` (they said so) | yes | yes | no |
| `conversation` (read from the exchange) | no | yes | yes |
| `brain_inference` | **never** | **never** | yes |

The existing structural rule stands (a fact resting on a Brain inference cannot be confirmed) and is
one step stricter here: an inference cannot even be `discovered`, because the kit treats a discovered
fact as known. What the Brain can never claim (`client_data`, `system`, `human`) is recorded by others.

Two families of keys get stricter treatment (`sales_core.contracts.payment`):

- **`core_*` keys are the Core's alone.** No proposal may use one.
- **`payment_claimed` and `payment_proof_received`** (`UNVERIFIED_CLAIM_KEYS`) record what the
  prospect said or sent. A Brain may propose them only as `discovered`, from `prospect` or
  `conversation`, citing an inbound message of the prospect. They are never confirmed, never guessed.

Refusals are audited (`fact.proposal_rejected`, with key and reason, never the value) and cost the
Brain that fact only, not the decision. Acceptances are audited too (`fact.proposal_accepted`).
Without a configured writer, proposals are refused with reason `no_writer`, not silently lost.

The generic Brain kit fills `proposed_facts` from what the prospect said (`learned_facts`, see
[brain-kit.md](brain-kit.md) section 3b) and from payment claims and receipts; the Core rules above apply
to all of them, whatever the Brain says.

### Provenance, idempotency, history

An accepted proposal becomes a `Fact` whose evidence is `FactEvidence(source=<claimed source>,
message_id=..., excerpt=..., observed_at=<the stored message's timestamp>,
recorded_by="brain-proposal:<action_id>")`. It is dated by its action (`recorded_at =
action.due_at`, persisted before the Brain is asked), so the same result processed twice, or the
action retried after a failure, has the same `fact_identity` and the store keeps one row. History is
append-only: nothing is overwritten, nothing is promoted (a hypothesis proposed ten times is ten
hypotheses), and a later trusted fact is a new record with its own evidence.

Limitation: the "authoritative fact" rule works by key and cannot know that a key is multi-valued (for
example one observation per product); the refusal is audited.

## 2. The payment model

Three different things, never confused:

| Concept | Meaning | Fact | Who can record it |
|---|---|---|---|
| **claimed** | the prospect says they paid | `payment_claimed` (discovered, source prospect) | the Brain, as a proposal the Core validates |
| **proof received** | the prospect sent something that may be a receipt | `payment_proof_received` (discovered, source prospect) | the Brain, likewise |
| **confirmed** | a person authorised by the client verified the payment | `core_payment_confirmed` (confirmed, source `human`) | **only** `PaymentConfirmationService`, from an explicit `PaymentConfirmation` |

`PaymentProgress` (`none`, `claimed`, `proof_received`, `paid`) is **derived** from facts by
`payment_progress`, never stored. `PAID` exists only while a valid confirmation does: the fact
`core_payment_confirmed`, `CONFIRMED`, with source `human` or `client_data`. A claim, a proof, a
WhatsApp reply, an invoice, an opened payment link, a Brain decision, a stage, and a fact with the
same key from the `system`, the prospect or the conversation never produce it. There is no
provider-driven path: the MVP has no payment-provider intake, and a provider event cannot confirm
anything.

### The confirmation (`PaymentConfirmationService`)

```
client CRM / Admin  -- authenticates the user, grants permissions -->  PaymentConfirmation
  -> PaymentConfirmationService.confirm   (sales_core.payments)
  -> facts, source HUMAN, evidence names the person
  -> payment_progress = PAID, visible to the Brain on its next request
```

`PaymentConfirmation` is the trust boundary. Whatever builds one asserts that the person was
authenticated; it carries their `ConfirmingActor` (`actor_id`, optional `display_name`,
`permissions`). The Core checks that `payments.confirm` is among the permissions, and records who
confirmed. It authenticates nobody and grants nothing. No HTTP API exists yet (there is no Admin UI);
the service is a plain Core object the future CRM/Admin calls.

For a confirmation the Core appends `CONFIRMED` facts from `FactSource.HUMAN`, with
`recorded_by="human:<actor_id>|confirmation:<confirmation_id>"`, `observed_at`/`recorded_at` = the
moment the person confirmed, and `message_id` = the proof message they checked, if given:

| Key | Value |
|---|---|
| `core_payment_reference` | the client's or the bank's reference (if given) |
| `core_payment_amount` | the amount exactly as entered (if given) |
| `core_payment_currency` | the currency as entered (if given) |
| `core_payment_confirmed` | `confirmed` (written last: never visible without its details) |

No conversion, tax or discount exists. Prospect status, stage and actions are untouched; the Brain
decides what follows on its next request (the kit's workflow then reaches the closing step).

**Idempotency and conflicts**

- Same `confirmation_id`, same terms: recorded once (`duplicate`, reason `confirmation_id`), also after
  a restart. Same id, different terms (reference, amount, currency or proof): refused
  (`conflicting_confirmation`).
- Same `payment_reference`, other id, same amount and currency: the same payment (`duplicate`, reason
  `payment_reference`); other amount or currency: refused. Nothing else is treated as the same payment.
- Refusals record nothing and are audited (`payment.confirmation_rejected`): `unknown_client`,
  `not_authorized`, `unknown_prospect`, `invalid_timestamp`, `unknown_proof_message`,
  `conflicting_confirmation`. Storage errors are raised (nothing was recorded; retry). Audit events
  (`payment.confirmation_received`, `payment.confirmed`, `payment.confirmation_duplicate`,
  `payment.confirmation_rejected`) name the person and never carry credentials or provider payloads.

A receipt the prospect sends is only *referenced*: the channel adapter keeps the message's kind and,
when the provider gives one, the media id in `Message.metadata` (`type`, `media_id`). Nothing is
downloaded or stored by the platform.

### In the Brain kit

- **Product payment options.** A product declares `payment.online` (`enabled`, `methods`) and
  `payment.offline` (`enabled`, `methods`, `receipt_required`). An option that is not enabled is never
  offered: none of its methods can be given to the prospect, no `[[payment:id]]` placeholder resolves
  for them, and no link can be written by the model. An enabled online option needs a method of kind
  `online` whose instruction carries the configured URL (validated when settings are loaded). A
  product without the block makes no declaration (each method's own scope decides, as before). Exact
  values (URLs, account details, prices) still reach the prospect only through placeholders, inserted
  by code exactly as configured.
- **Fixed completion.** A `payment` step is done when the prospect has claimed or proved a payment; a
  `payment_confirmation` step only when `payment_progress` is `PAID`. Neither accepts `completes_when`,
  and a workflow with a `payment` step must contain a `payment_confirmation` step. So a claim or a proof
  moves the conversation to the confirmation step, and only a person's confirmation moves it past.
  `confirmation.mode` is `manual` or `none`; `provider` is rejected when settings are loaded.
- **Recording claims and proofs.** The model reports `analysis.payment_claimed` and
  `analysis.payment_method`. The kit turns a claim into a `payment_claimed` proposal, and an inbound
  image or document at a payment step into a `payment_proof_received` proposal, both anchored to the
  prospect's own latest message (chosen by code, not by the model), only on a reply, and once.
- **Asking for the receipt.** If the product's offline option has `receipt_required` and a receipt is
  not in hand, then after a claim (or a claim this turn) a message must ask for the receipt
  (`requests_receipt: true`), or the answer is refused. Someone who says they paid through an online
  method is not asked. The prompt tells the model it never confirms a payment.
- **No message presents a payment as confirmed.** The model declares `payment_statement` (`none`,
  `awaiting_verification`, `confirmed`). `confirmed` is offered in the response schema, and accepted,
  only when `payment_progress` is `PAID`; otherwise the answer is refused, whatever the prospect said or
  sent and even when a receipt is requested in the same message. A stage whose milestone is `won` is
  likewise refused before `PAID` in a workflow that has a `payment_confirmation` step. This rests on the
  structured declaration, not on searching the text for words.

### What is not enforced

The Core guarantees who can write the confirmation and that a claim or proof is never one. The kit
refuses an answer that *declares* it presents the payment as confirmed. A model that declares
`none` but words the message as if paid is not caught (no word search by design); it still changes no
state.

## Limitations

- No CRM, Admin UI or HTTP endpoint calls `PaymentConfirmationService` yet, and nothing in the
  application wires it.
- `PaymentConfirmation` permissions are vouched for by the caller; the Core does not verify them
  against a directory.
- No revocation or correction of a confirmation; a mistaken one needs a new mechanism.
- A payment step cannot yet be completed by a client-specific fact; completion is fixed.
- A product without a `payment` block makes no explicit declaration (methods' scopes apply).
- Duplicate and conflict detection read the prospect's own facts.
- No provider integration, webhook, file storage, currency conversion, taxes or discounts.
