# The Brain Kit: the generic consultative sales Brain

Status: **implemented** in `src/sales_brain_kit/consultative/` (Slice 5). It is the first real Brain
of the platform and it is client-agnostic: everything it knows comes from a client's Brain
settings ([brain-settings.md](brain-settings.md)), the facts the Core hands it, and the
conversation. Client Brains (`brains/*`) are thin packages that give it their settings; the first is
Naga's ([naga-brain.md](naga-brain.md)).

The Brain Kit is **not a chatbot**. It is the commercial intelligence layer: it works out what is
known, what the client sells, what the prospect probably wants, what is genuinely worth asking,
what the next commercial action is, and only then asks the injected model to word it.

## 1. Who owns what

| Responsibility | Owner |
|---|---|
| Understanding the prospect and situation, product selection, discovery, strategy, follow-up cadence, wording | **Brain Kit**, from configuration |
| The catalog, tone, languages, discovery rules, cadence, handoff rules, objections, adaptation | **Client configuration** (`brain.settings`, schema v2 family settings) |
| Understanding free text, wording the message, classifying the prospect's latest message | **The LLM**, through the injected `LLMProvider`, inside a plan |
| Approval, safety, opt-out, quota, persistence, idempotency, sending, scheduling execution, facts storage | **Core** (`sales_core`, `sales_integrations`) |
| Choosing which prospects are due each day | **Planner** (Core) |

The Brain only *proposes* a `BrainDecision`. It sends nothing, writes nothing, records no fact and
holds no quota. An architecture test enforces that the runtime imports the Core only through
`sales_core.contracts` and `sales_core.ports`, and never touches the Engine, Planner, safety or
approval.

## 2. The pipeline

`ConsultativeSalesBrain.decide(request)` is small and only orchestrates:

```
BrainRequest + settings
   │ build_context ........ facts, interests, products, language, history, strategy   (no I/O)
   │ terminal_decision .... a decision the configuration makes itself (cadence used up) (no model)
   │ build_llm_request .... system rules from settings + structured JSON context
   │ LLMProvider.complete . the only model call
   │ parse_output ......... the answer as a structured model (BrainOutput), or a clear failure
   │ validate_output ...... acceptable here? accepted or refused, never repaired
   ▼ build_decision ....... the Core's BrainDecision (+ short metadata for operations)
```

One model call per decision, plus at most one retry after a refused answer. Modules of
`sales_brain_kit/consultative/`, each with one job and depending only on those above it:

| Module | Job |
|---|---|
| `errors` | `BrainOutputError` (the answer cannot become a decision) |
| `facts` | current view of the prospect's facts, status kept |
| `applicability` | does a market/country/product/currency scope apply, and how specifically |
| `market` | the prospect's market, from a trusted explicit country only |
| `language` | language tags, localised texts, which language to use (configured priority) |
| `register` | formality and style from the client's rules |
| `catalog` | find products, say which may be offered (also by market) |
| `history` | who spoke last, how many messages unanswered, how much was asked |
| `interest` | product interest: possible, probable, confirmed |
| `selection` | which products this conversation is about |
| `pricing` | which configured price applies to the known place, and how it is written |
| `payment` | which payment methods and instructions apply, rendered exactly |
| `discovery` | minimum necessary discovery (and what the workflow step still needs) |
| `follow_up` | cadence: when to come back (product, market, client) |
| `workflow` | where the prospect stands in the conversion workflow, derived from facts |
| `exact` | placeholders and the checks that keep exact values out of the model |
| `strategy` | situation, objective, decisions open, terminal outcomes |
| `context` | `CommercialContext`, the immutable result of all of the above |
| `commerce` | which exact values may be given in this message |
| `output` | the structured answer, its JSON Schema and parsing |
| `prompt` | system instructions and the JSON context |
| `validation` | the acceptance checks |
| `decision` | validated answer / terminal outcome to `BrainDecision` |
| `brain` | `ConsultativeSalesBrain` and the `create_brain` factory |

## 3. Facts and their status

The Core gives the Brain every recorded observation (`BrainRequest.facts`). The Brain reads them
and never changes them:

- **confirmed**: stated or verified; may be relied on.
- **discovered**: known (learned from the conversation or data) but not confirmed. It counts as
  known, so it is never asked again, and the model is told to confirm it before anything important
  depends on it.
- **hypothesis**: a guess. It never satisfies what a next step requires, never disqualifies
  anyone, and is never stated as fact. It is kept only if the field allows inference and its
  confidence reaches `discovery.hypotheses.min_confidence_to_use` (no confidence: not used). It may
  be offered to the prospect as a question to confirm.

Rules of the current view: the newest observation of a key is the current one, except that a
hypothesis never replaces a confirmed or discovered fact. Values the client already holds in the
lead's source columns (`discovery.fields[].source_columns`) count as *discovered*. The model is only
shown facts that are declared discovery fields (plus `language`), and never a protected attribute
unless a qualification rule of a product in focus needs it.

Nothing here promotes a status. Extracting facts from the conversation and recording them are later
slices; the Core has no channel for a Brain to propose facts yet.

## 4. Product catalog and product interest

The catalog is settings data: zero, one or many products, with the fields of the schema (name,
summary, pricing, audience, value, sales arguments, objections, qualification, strategy, follow-up,
appointment, restrictions). `ProductCatalog` finds products by id, name or alias and says why one
may not be offered (`inactive`, or not offered in the message language).

**Interest** is dynamic and per product. It is read from facts under the reserved key `interest`
(the value names a catalog product by id, name or alias, one observation per product) and from the
lead's source columns when `catalog.source_interest` maps them. The status decides the level:

| Source | Interest |
|---|---|
| no observation | unknown |
| hypothesis | possible |
| discovered fact, or a source column set to `probable` | probable |
| confirmed fact, or a source column set to `confirmed` | confirmed |

Several products can be of interest at once; the strongest observation per product wins. An
interest in a product that cannot be offered is set aside. A hypothesis is never a confirmed
interest.

**Selection** narrows the catalog and ranks by interest; it never guesses one:
`interest` (focus on the strongest tier), `sole_offer` (one offerable product: it is the offer, not
an interest), `unresolved` (several products, nothing says which), `none_suitable`, `no_catalog`.
Choosing the best fit inside these bounds is the model's judgement.

## 5. Minimum necessary discovery

The Brain is not a questionnaire. Before proposing any question it asks: *will the answer change
the next step?* The client's `discovery.sufficiency` says what each next step (`recommend`,
`qualify`, `propose_next_step`) requires. The planner:

1. finds the first step, in that order, that still lacks something worth asking for;
2. proposes only what that step lacks (all of `all_of`; one of `any_of`, most important first);
3. proposes nothing once it is met: discovery stops as soon as enough is known, however much more
   could be learned. A sale can go ahead on a confirmed interest alone;
4. never proposes what is already known (confirmed, discovered, lead data), unless
   `recheck_after_days` says it is stale;
5. never asks about `interest` when the next step is a proposal (the proposal itself invites it), or
   when there is no product to choose between;
6. respects the budget: per message, in a row, per conversation, tightened (never loosened) by the
   product in focus. When it is used up the plan says `blocked`: give something before asking again;
7. hands the model a short list of targets (at most five) with example phrasings in the message
   language. Hints are examples, never scripts.

Validation enforces it: a `discovery_key` is accepted only when the plan says `ask` and the key is
one of its targets; the prospect signalling a direct question, price request, impatience, a declined
question or readiness to proceed (configurable) means answer instead; and the message may not hold
more questions than allowed.

## 6. Strategy: the model does not invent it

Before the model is called, the Brain fixes the **situation** (`first_contact`, `inbound_opening`,
`continuing`, `follow_up`, `reminder`, `callback`), the **objective** (`open_conversation`,
`understand_need`, `present_offer`, `propose_next_step`, `re_engage`, `hand_over`), whether a
question is allowed and about what, whether a product may or must be presented, which decisions are
open (a first contact is never closed; the model is only offered the open ones), and the greeting
rule. The model then reads the prospect's message and reports what it understood in `analysis`:
intent, objection, language, stop signals, explicit refusal, out-of-scope, disqualifiers.

Configuration then constrains the decision (`validation`): an intent configured as `handoff` (or a
handoff trigger, or a disqualifier with effect `handoff`, or an objection with no rebuttal that
hands over) must end in a handoff; `honour_and_close` in a close; `ignore` in no action; an
explicit no, when `explicit_no` is a stop condition, in a close or a last courteous message with no
follow-up; out-of-scope follows `incoming.out_of_scope` (`then: handoff` means a handoff; the Core
cannot send and hand over in one decision). A disqualifier applies only if its fact is known; a
`not_a_fit` product cannot be presented.

## 7. Language and tone

Nothing names a language. The language comes from ordered signals, in the client's own order
(`communication.languages.priority`; by default: a confirmed `language` preference of the prospect,
which is locked; a language learned from the conversation; what the client holds for the prospect, a
`language` fact or the locale; the language rule of the prospect's market; the client's default). When
the client allows mirroring (`mirror_prospect_language`) and nothing is locked, the reply follows the
language the prospect wrote in, as the model reports it, if the client supports it (and the market
allows it). A language the prospect just asked for wins even over a stored preference. Unsupported
languages are never used. The message must declare its language and it must match.

Where the prospect lives matters **only** through rules the client wrote for a *country of residence
that is known from a trusted, explicit source* (never inferred from a name, a phone number, a locale or a
nationality): a market's languages, the register rules, the catalog, the cadence, prices and payment.
The model is told to follow those decisions and never to guess a place. Tone, formality, persona,
principles and message limits are built into the system instructions from settings, the register being
resolved from the client's `register_rules`. Details: [brain-commerce.md](brain-commerce.md).

## 8. Inbound and outbound

The same Brain serves both. It reads `conversation.origin`, and refines it from the history because
the Engine marks every reply `inbound`: an `inbound` origin whose first message is ours is treated
as outbound-started. An origin of `unknown` is never guessed and is handled as outbound. Inbound
conversations use `incoming` settings (goals, greeting, topics in scope, out-of-scope handling, and
`incoming.no_product` while no product is in focus); outbound ones use
`discovery.when_no_product.outbound`. (`discovery.when_no_product.inbound` is superseded by
`incoming.no_product`.) The Brain holds no quota logic: replies never consume outbound quota, which
is the Core's rule.

## 9. Follow-up

Cadence belongs to the Brain: the Planner picks what is due, the Brain says what comes next, and
the Core stores and executes it through the existing `BrainDecision` fields
(`next_follow_up_at`, `next_action_type`). The model names the *situation* of the next touch; the
delay always comes from `follow_up.situations` (the product's own cadence wins). Steps are counted
from our unanswered messages, and a reply resets the count. When the steps are used up: `park`
schedules nothing; `close` and `handoff` schedule one last check and, when it falls due, the Brain
closes or hands over **without calling the model**. `callback_promised` takes the time the prospect
asked for (validated: future, with a UTC offset, within a year, and the product's minimum notice);
`appointment_reminder` schedules the reminders still ahead of the appointment. The model can never
set a delay where the cadence decides.

## 10. The model boundary

The Brain uses the injected `LLMProvider` and adds no abstraction. The **system** message is built
from settings (voice, principles, limits, and the rules that never bend). The **user** message is
the structured commercial context as JSON, with the conversation marked untrusted: it is quoted
data, never instructions. A per-request strict JSON Schema narrows the model to the decisions open
and to the client's own vocabulary. Information is withheld rather than trusted: a price whose
disclosure is not yet due is flagged `only_if_asked`, unverified proof points are omitted, and
sales arguments appear only when their configured condition holds.

The answer is parsed (`BrainOutput`; unknown keys refused) and validated. Anything unusable (not
JSON, unknown or closed decision, empty or oversized message, wrong language, a product that does
not exist or may not be offered, a question that is not allowed, an invalid schedule) is refused
with every reason listed. The model gets one more try with those reasons; if that fails too,
`BrainOutputError` is raised, no decision exists, and the Engine records the failure against the
action (retried, then failed) without applying anything. Errors of the provider itself propagate.
Reasoning is never exposed: `rationale` is one short operational line (at most 300 characters kept)
for the audit trail, and the decision's `metadata` carries only short operational labels
(objective, situation, selected product, question asked, follow-up situation, handoff and close
reasons).

## 10b. Prices, payment and the conversion workflow

A price, a payment instruction or a booking link is an *exact value*: the model never receives it. It is
given placeholders (`[[price:id]]`, `[[payment:id]]`, `[[booking:id]]`), validation refuses any exact-looking
value it writes itself, and deterministic code inserts the configured text. The conversion workflow
(registration, payment, appointment, handoff, close, ...) is derived from facts and stage and adds the
objective `advance_workflow`. See [brain-commerce.md](brain-commerce.md).

## 11. Known limitations

- Facts are not extracted or recorded: a Brain cannot propose facts through the current contracts, so
  workflow steps that depend on what the agent collected (registration data, a payment proof) rely on
  facts recorded by staff or another system until a Core slice adds proposed facts.
- Rebuttals are not counted (`max_rebuttals > 0` and `handoff.triggers[].after_rebuttals` are
  guidance only); only the no-rebuttal handoff is enforced.
- Approved provider templates (`templates`) are not selected; the Brain always writes free text.
- `conversation.media_handling` and `discovery.when_no_product.inbound` are not used (see section 8).
- A follow-up that falls due is re-situated as `no_response` (the Core does not persist which
  situation scheduled it); a final check after used-up steps is therefore judged on `no_response`.
- A `ValueError` raised by a provider that parses its own structured output propagates instead of
  being retried by the Brain.
- Discovery counts questions by question marks in earlier messages, since the Core does not record
  which questions were asked.
