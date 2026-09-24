# Brain settings: the "consultative sales" family

Status: **the settings schema, its validation and its ConfigStore integration are implemented**
(`src/sales_brain_kit/`, `src/sales_app/config/`). The Brain that *uses* these settings (discovery
planner, fact ledger, conversation pipeline) is not built yet. Design background:
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
before. The two client Brains are still scaffolds and declare none.

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
| `handoff`, `adaptation`, `templates` | handoff triggers, adaptation inputs and packs, approved templates |

**Products** are dynamic data: `id` (immutable slug), `status` (`active`, `coming_soon`, `inactive`),
name, summary, pricing (with a disclosure policy), audience, value, sales arguments, objections,
qualification and disqualifiers, extra discovery fields and a tighter budget, strategy and call to
action, follow-up overrides, appointment, restrictions. A minimal product is `id`, `name`,
`summary`. Products are deactivated, never deleted.

**Precedence.** Scalars: the product replaces the global value. Keyed lists (objections, fields,
follow-up situations): merged by id, product wins. Restrictions: the product can only add. Discovery
budget: a product can only tighten. Conversation context selects among rules and never overrides
them.

**MVP scope decisions built in:** appointments are a callback carried out by a human
(`appointment.handled_by` can only be `human`; there is no `book_call` call to action); evidence
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

## 7. Not implemented yet

The Brain that reads these settings (fact ledger, discovery planner, LLM pipeline), Core facts and
the `interest` concept, enforcement of the remaining `inbound` limits, the
Admin API and Web Admin, ContactPolicy, and any real client content.
