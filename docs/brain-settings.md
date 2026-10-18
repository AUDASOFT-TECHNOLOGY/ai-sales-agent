# Brain settings: the "consultative sales" family

Status: **the settings schema, its validation and its ConfigStore integration are implemented**
(`src/sales_brain_kit/`, `src/sales_app/config/`). The Brain that *uses* these settings, the generic
consultative sales Brain, is implemented too: see [brain-kit.md](brain-kit.md). Design background:
[web-admin-product-design.md](web-admin-product-design.md) and
[client-configuration-design.md](client-configuration-design.md).

## 1. Who owns what

| Piece | Owner | Where |
|---|---|---|
| The `BrainSettingsSpec` **shape** (family, version, JSON Schema, validate, prepare) | Core port, generic | `sales_core.ports.brain` |
| The "consultative sales" **schema** and its rules | the shared kit, generic, no client names | `sales_brain_kit` |
| Which schema a client's Brain uses | the Brain package: a module attribute `SETTINGS_SPEC` next to its factory | `brains/<client>/...` |
| Finding, calling and applying the spec | `sales_app` (via `importlib`, never a static import) | `sales_app/config/brain_settings.py` |
| The client's actual values | the client, stored as versioned JSON | ConfigStore, `brain.settings` |

`sales_core` never imports the kit and knows no product, discovery or follow-up vocabulary (an
architecture test enforces it). A Brain without a `SETTINGS_SPEC` keeps opaque settings exactly as
before. The generic `consultative` engine declares the consultative spec unchanged (it adds no rules). The generic
Brain is built from any valid settings with `sales_brain_kit.create_brain`.

Another Brain family can bring its own schema through the same hook; a future Web Admin only calls
`json_schema()` and renders forms.

## 2. Versions

- **Document schema** (`schema_version`, now **2**): the client document in the ConfigStore. Version
  1 documents keep loading (section 6).
- **Brain settings version** (`brain.settings_version`, per family, now **1**): a rename, removal or
  change of meaning of a Brain setting is a new settings version with a migration. Additive optional
  fields with defaults need none. Only version 1 exists, so there are no migrations yet.
- **Migrating is publishing:** a stored version is never rewritten.

## 3. What a settings document contains

Required: `business.name` and `communication.languages` (default and supported). Everything else has
a cautious default, so a client can start with a catalog and a tone.

| Section | Holds |
|---|---|
| `business`, `communication` | name, languages, tone, formality, principles, message limits |
| `objectives`, `stages` | the client's goals and funnel labels (stages may carry a generic milestone) |
| `catalog` | **any number of products**, source-interest mapping, presentation limits |
| `discovery` | fields worth knowing, what each next step requires, question budget, stop signals, shortcuts |
| `conversation` | answer-first, calls to action, objections, intents, media handling |
| `follow_up` | cadence per situation, limits, stop conditions |
| `incoming` | how the Brain behaves when a prospect writes first |
| `handoff`, `adaptation`, `templates` | handoff (on or off, triggers, message), adaptation inputs and packs, approved templates |
| `markets`, `payment`, `conversion` | markets by country, payment methods and exact instructions, conversion workflows and the default appointment (see [brain-commerce.md](brain-commerce.md)) |

`communication` also holds `register_rules` and the language `priority`; a product's `pricing` may hold
per-market or per-country `variants`, and a product may name its `workflow`.

**Products** are dynamic data: `id` (immutable slug), `status` (`active`, `coming_soon`, `inactive`),
name, summary, pricing (with a disclosure policy), audience, value, sales arguments, objections,
qualification and disqualifiers, extra discovery fields and a tighter budget, strategy and call to
action, follow-up overrides, appointment, restrictions. A minimal product is `id`, `name`,
`summary`. Products are deactivated, never deleted.

**Precedence.** Scalars: the product replaces the global value. Keyed lists (objections, fields,
follow-up situations): merged by id, product wins. Restrictions: the product can only add. Discovery
budget: a product can only tighten. Conversation context selects among rules and never overrides
them.

**Scope decisions built in:** an appointment is configured, not assumed: `appointment.mode` is a callback
the agent schedules, a configured booking link, or a person arranging it, and `handled_by` says whether a
person or the agent carries a scheduled callback out (there is no `book_call` call to action); evidence
(the prospect's own words) is required for `critical` and `sensitive` discovery fields and optional
for ordinary ones; nothing in the schema fixes how many LLM calls a reply uses.

## 4. Validation

Run when settings are published (`sales-agent config publish | import | rollback`) and again when
they are loaded at start-up.

1. **Structure** (pydantic): types, formats (slugs, language tags, ISO durations, HTTPS URLs,
   currencies), sizes, control characters, **unknown keys rejected everywhere**.
2. **References and guardrails**, checked once the structure is valid (so structural problems and
   cross-reference problems arrive in two passes, each with every problem listed):
   - ids are unique (products, goals, stages, templates, packs, sales arguments, objections, rules,
     discovery fields); product ids, names and aliases never point at two products;
   - every fact key a rule uses is a declared field (or `interest`); intents, disqualifiers and the
     primary goal exist; every language used is declared in `communication.languages`;
   - **questions are capped**: at most 2 per message, the discovery budget cannot exceed the message
     limit, a product can only tighten the budget;
   - **follow-up is bounded**: minimum gap of at least one hour, steps not sooner than the gap, no
     more steps than `max_touches`; `before` only for appointment reminders;
   - **adaptation uses facts, not stereotypes**: protected attributes (gender, religion, ethnicity,
     nationality, age, ...) can never drive adaptation and can only be collected if declared
     `sensitive`; place (`country`, `city`, `region`) may adjust only practical matters (currency,
     availability, delivery); a client may protect more attributes, never fewer;
   - reserved fact keys (`interest`, `asked`) cannot be declared.
3. **Size:** at most 200 products and about 1 MB of settings.
4. **Secrets:** the ConfigStore's secret-key lint still applies to the whole document.

## 5. How it plugs into the ConfigStore

`brain.settings` and `brain.settings_version` are part of the client document. `sales_app`:

1. loads the Brain module from `brain.factory` and looks for `SETTINGS_SPEC`;
2. `spec.validate(settings, version)`; any problem is reported as `brain.settings.<path>: ...` and
   **nothing is written** (publishing) or the running configuration **stays active** (loading);
3. `spec.prepare(...)` returns the settings at the latest version with every default written out;
   that normalised form is what is stored and what the Brain is built with.

Publishing needs the Brain module to be importable (otherwise the settings cannot be validated);
loading leaves an unimportable module to the runtime build, which reports it with more context.

## 6. Schema 1 compatibility, and the new `inbound` section

Document schema 2 also adds the client-level **`inbound`** policy (spontaneous conversations on or
off, what to do with unknown senders, anti-abuse limits, allowed decisions):

- New documents default to the safe side: spontaneous conversations **off**, unknown contacts
  **ignored**. A prospect is created for an unknown sender only with policy `engage`, which requires
  spontaneous conversations to be enabled.
- A **schema 1 document** or a `client.yaml` from before this section keeps today's behaviour: it is
  upgraded in memory (or `config import` writes it explicitly) with spontaneous conversations
  enabled and unknown senders ignored. Version 1 files are never rewritten.
- A schema 1 document cannot contain `inbound` or `brain.settings_version`.
- **The Engine enforces part of `inbound`.** `spontaneous_conversations.enabled` and
  `unknown_contacts.policy` decide what happens to a message from a sender who is not a prospect
  (ignored, or a prospect is created and the message answered; see
  [engine-design.md](engine-design.md)). Messages from known prospects are always handled.
  `unknown_contacts.max_new_per_day` caps the prospects created that way per local day.
  `when_disabled`, `allowed_decisions` and `limits` are stored and validated but not enforced yet.

## 7. How the Brain uses each section

| Section | Used by the Brain for |
|---|---|
| `business`, `communication` | the system instructions (who speaks, voice, principles), languages, message limits (length, questions, emoji, greeting) |
| `objectives`, `stages` | the primary goal handed to the model; the only stage labels a decision may set |
| `catalog` | which products may be offered, source-interest mapping (`Product` column to interest), presentation limits, product detail given to the model |
| `discovery` | what each next step requires (`sufficiency`), the question budget, stop signals, shortcuts, hypotheses policy, `when_no_product.outbound` |
| `conversation` | answer-first, calls to action, global objections, intent behaviours (`handoff`, `honour_and_close`, `ignore`, ...) |
| `follow_up` | the cadence (delay per situation), stop conditions; a product's own cadence wins |
| `incoming` | inbound goals, greeting, topics, out-of-scope handling, `no_product` |
| `handoff` | triggers, the message the prospect is told, whether a briefing is required |
| `adaptation` | packs and dimensions chosen by observed facts, provenance level, extra protected attributes |

**The `interest` convention.** `interest` is a reserved fact key. A prospect's interest in a
product is recorded as facts with key `interest` and a value naming the product (id, name or alias),
one per product; the fact's status gives the level (confirmed, discovered = probable, hypothesis =
possible). The lead's source columns can give the same through `catalog.source_interest`. A
requirement's `accept` is the weakest level that satisfies `interest`.

## 7b. Commerce sections

Markets, price variants, payment, register rules, the language priority, the handoff switch and the
conversion workflows are described in [brain-commerce.md](brain-commerce.md), including how exact values
(prices, payment details, links) reach the prospect without a model handling them. Their validation is
in the same two passes: structure first, then references (every id, market, product, fact key, stage,
step and payment method a rule names must exist; a country that drives a market must be a declared
discovery field that forbids inference; a workflow step needs how it is known to be done; nothing hands
over to a person when handoff is off; a step cannot require a fact that can never be learned).

## 8. Not implemented yet

Fact extraction and recording by the Brain, rebuttal counting (`after_rebuttals`), provider
templates (`templates`), `conversation.media_handling`, enforcement of the remaining `inbound`
limits, the Admin API and Web Admin, ContactPolicy, and any real client content.
`discovery.when_no_product.inbound` is superseded by `incoming.no_product` and is not read. Also not
implemented: customer segments in pricing. A payment is never confirmed by a provider: only by a person at the client (see core-facts-and-payments.md).
