# Commerce in the Brain Kit: markets, language, prices, payment and conversion

Status: **implemented** in `src/sales_brain_kit/` (Slice 6b), on top of the consultative Brain of
[brain-kit.md](brain-kit.md) and the settings of [brain-settings.md](brain-settings.md). Everything
here is a **generic capability driven by a client's configuration**. No client, product, price,
country, currency, language, bank detail or commercial process is named anywhere in the code; the
architecture tests fail if one appears (section 10).

## 1. Capability versus data

| The code knows how to | The client's configuration says |
|---|---|
| place a prospect in a market from a trusted, explicit country | which markets exist, which countries each lists, where the country comes from |
| choose a language and register from ordered signals | which languages, in which priority, which register per market and language |
| choose a price for the prospect's known place | the prices, per product, market and country, and their currencies |
| choose payment methods and instructions by product, market, country and currency | every method, its scope, its exact wording, its confirmation mode |
| give exact values to the prospect without a model handling them | the values themselves |
| derive where a prospect stands in a workflow, and what that step requires | the steps, what completes them, who is involved, what follows |
| switch a handoff on or off, require it or allow it, per step | the rules |

Adding a client is adding configuration (and, where genuinely needed, a thin package of wiring such
as `brains/naga`). It never means editing the Core, the Engine, the kit or the Planner.

## 2. Markets and the country they rest on

A **market** (`markets.markets[]`) is a client-defined group of countries with optional rules for
allowed languages and a default language, a preferred style, which products it offers (`only` or
`exclude`), a conversion workflow and follow-up cadence overrides.

A prospect is placed in a market only when their **country of residence** is known:

- it is read from one configured fact (`markets.country_fact`, default `country`), which must be a
  declared discovery field with `infer.allowed: false` and cannot be a protected attribute;
- the fact must be **settled** (never a hypothesis), and either **confirmed**, or come from a source in
  `markets.trusted_sources`: `prospect` (they said it), `client_data` (the client's own lead columns),
  `human`, `system` by default; `conversation` only if the client opts in. A guess made by a Brain is
  never a trusted source;
- the country is matched, ignoring case and spacing, against the spellings the market lists.

**Nothing is inferred.** Not from the name, the phone number, the region of a locale, an accent, a
nationality, a column nobody declared as the country, or what the model says. Nationality is a
protected attribute and is never used. An unknown country means no market, and every rule written for
a market or country then simply does not apply. Tests cover each of these.

What a market drives, always through explicit configuration: the language pool and default, the
register, the products offered, the follow-up cadence, the workflow, and (through scopes) prices and
payment methods.

## 3. Language and register

The language is chosen from these signals, in the client's own order
(`communication.languages.priority`, default below), the first that yields a supported language winning:

1. `explicit_preference`: a language the prospect confirmed (locked; a request always stands, and
   this signal cannot be removed from the priority);
2. `conversation`: a language seen in the exchange;
3. `profile`: a language fact or locale the client holds for the prospect;
4. `market`: the language rule of the prospect's market;
5. `default`: the client's default, always the last resort.

A market's `languages.allowed` narrows what the conversation, profile and market signals (and
mirroring) may pick; an explicit request still stands. Unsupported languages are never used. The result
is deterministic: the same facts give the same language.

The **register** (formality, whether it may follow the prospect's, a style) comes from
`communication.register_rules`: the first rule whose `when` matches the known market and the language in
use applies, else the client's `tone`. A market can add a preferred style line. The rule that decided
is named in the context for the audit trail. Nothing here says which languages or registers exist.

## 4. Prices per place

`pricing` is the product's default price. `pricing.variants[]` gives a price for a market or country
(`applies_to.markets` / `applies_to.countries`), with its own currency and optionally its own
disclosure rule. The most specific variant that applies to the known place wins (a country beats a
market), ties go to the one declared first, and without a match the default applies. A configured price
is authoritative: **nothing converts currencies, adds tax, discounts or computes**. A priced model must
cost more than zero (`free` says nothing costs). With no price configured for the product, or an
`on_request` price, there is no amount to state and the Brain has none to invent. A price that varies by
place while the place is unknown is flagged to the model as such. Customer segments are not supported.

## 5. Payment

`payment.methods[]` are the client's ways of getting paid. A method has a `kind` (`online`, `offline`,
`other`), a scope (`applies_to`: products, markets, countries, currencies; empty means any),
`instructions[]`, a `confirmation` (`manual` or `none`, with a note for the prospect: a payment is
always confirmed by a person at the client, never by a provider) and
reference metadata that is never shown. An instruction is exact data: its `text`, its `fields`
(`label: value` lines) and its payment `url`, each optionally per language, plus its own scope. An
`online` method needs a link, an `offline` one needs text or fields.

Selection is deterministic: a method applies when it is active and its scope matches what is known
(a restricted dimension whose value is unknown never matches; the currency is that of the price in
play); within a method the most specific matching instruction is used, ties to the first declared.
Country-specific, market-specific, product-specific and currency-specific payment are all just scopes.

## 6. Exact values: how payment details never pass through a model

The security rule is structural, not a prompt. Prices, payment instructions and booking links are
exact values:

1. **The model is never given them.** Its context lists only *placeholders* for what may be said now:
   `[[price:<product>]]`, `[[payment:<method>]]`, `[[booking:<step>]]`, and what each stands for.
2. It writes its message with placeholders where the values belong.
3. **Validation refuses** a placeholder that is not available now (for example a payment method that does
   not apply, or payment details outside a payment step), a malformed placeholder, and anything the
   model wrote itself that looks like an exact value: a web address that is not a configured link
   (payment and booking links are never free-use links), an amount next to a currency code or symbol,
   or an account-like number (long digit runs, grouped digits, IBAN-like text). The model is told why
   and gets one more try; after that there is no decision.
4. Only then does deterministic code replace each placeholder with the configured text, character for
   character (no trimming, reformatting or translation of values; labels and wording are chosen by the
   message language). The message limit applies to what the model wrote; the expanded message has its
   own ceiling.

The guarantee is 1 and 4; 3 is defence in depth and is deliberately conservative and language-neutral.
It cannot recognise an amount spelled out in words, or a bare number that is not a price, which is why
the values never reach the model in the first place. The final guard remains a person approving each
draft.

## 7. The conversion workflow

`conversion.workflows[]` are ordered lists of typed steps (not a process engine). Which workflow a
conversation uses: the product's `workflow`, else the prospect's market's, else
`conversion.default_workflow`, else none (then the Brain behaves exactly as before).

| Kind | Meaning |
|---|---|
| `recommend`, `qualify` | the discovery ladder already configured under `discovery.sufficiency`: done when what it asks is known (and, for `recommend`, a product is in focus) |
| `inform` | informational; never blocks (blocks only if it has `completes_when`) |
| `register` | `registration.mode`: `self_service` (a link), `agent_assisted` (the agent collects `collects` facts), `human_assisted` (a person) |
| `appointment`, `callback` | uses the step's, the product's or the client's `appointment` settings |
| `payment` | offers the payment methods the product enables (optionally a subset); done when the prospect claims or proves a payment, never confirmed by it |
| `payment_confirmation` | done only when a person at the client confirmed the payment (`core_payment_confirmed`); no `completes_when` |
| `handoff`, `close` | end the workflow: hand the conversation to a person, or close it |
| `custom` | any other client-defined step, with its own completion |

A step has an `objective` (guidance for the wording), `requires` (steps and facts that must come first),
`human` (`none`, `optional`, `required`; default `optional`, `required` for a handoff, `none` for a
close), `completes_when` (facts, optionally with allowed values and a minimum status, and/or a stage),
`next` (default: the following step), `fallback` and a suggested `stage`.

**Where a prospect stands is derived, never stored.** A step is done when its condition holds for the
prospect's settled facts (a guess never completes a step) and stage. The current step is the first that
is not done, after following any required step. From it come: what to ask for (the step's missing
prerequisite facts and, for registration, what it collects; discovery's budget still applies, and the
Slice 5 shortcut skips the ladder but not this), whether a person is involved, which payment methods
or booking link may be given, and what the configuration insists on.

**What the configuration insists on** is enforced by validation: a step that requires a person, a
human-assisted registration, a person-arranged appointment, a `handoff` step, a `close` step, or an
unavailable step's fallback each force one decision (a handoff, a close, or waiting). A person asking
for one, or an explicit stop, still outranks it. At a step with `human: none` a handoff the
configuration does not ask for is refused. **A step is unavailable** when the resource it needs is not
configured for this prospect (no payment method applies, no appointment is configured); its
`fallback` then applies: `handoff`, `close`, `park`, `skip`, or another step. The default is a handoff,
or waiting when handoffs are off.

`handoff.enabled: false` removes the decision altogether; validation then refuses every part of the
configuration that would hand over (intents, triggers, cadences, steps, appointment modes).

## 8. Handoff and appointment configuration

| Situation | Where the client says it |
|---|---|
| a person is required / allowed / not involved at a step | `human` on the step |
| a handoff on a phrase or a rule | `handoff.triggers`, `conversation.intents` |
| no handoff ever | `handoff.enabled: false` |
| registration by the agent, a link, or a person | `registration.mode` |
| appointment through a booking link, a callback, or a person arranging it | `appointment.mode` (`booking_url`, `callback`, `human_scheduling`) |
| who carries a scheduled callback out when it falls due | `appointment.handled_by` (`human`: hand over; `agent`: the agent contacts the prospect) |
| notice needed for a callback | `appointment.min_notice_hours` |

## 9. The Core, and the payment model of Slice 6c

Slice 6b itself did not modify `sales_core`. The exact text is delivered through the existing
`MessageDraft`; workflow position is derived from facts the Core already hands the Brain
(`BrainRequest.facts`, written by staff or another system through the Facts tab) and from the prospect's
`stage` (which the Brain already sets through `new_stage`). `sales_core` names none of these concepts,
except the payment claim, proof and confirmation of Slice 6c (an architecture test).

Slice 6c ([core-facts-and-payments.md](core-facts-and-payments.md)) added what the Core owed the kit: a
Brain can **propose facts** (validated and recorded by the Core), and the payment model is fixed:
**the agent never confirms a payment.** A prospect saying they paid is the fact `payment_claimed`, a
receipt they send is `payment_proof_received` (both only *discovered*), and only a person authorised by
the client confirms (`core_payment_confirmed`, source `human`, through `PaymentConfirmationService`).
`PAID` is derived from that fact alone. A product declares `payment.online` / `payment.offline`
(`receipt_required`): a disabled option is never offered and its link cannot be sent. A stage set by the
model is never proof that money arrived.

## 10. Keeping it generic

Tests enforce that no client name, geography or currency vocabulary appears in the Core, the kit, the
adapters or the app wiring; that nothing branches on a client id, display name, product id, country or
market id; that `sales_core` names no payment, market, workflow or price concept; that no account-like
number or link is written in source; that the exact-value modules import no storage or network; and that
the runtime's modules depend only one way. A client package (`brains/<client>`) holds wiring only.

## 11. For the client-facing configuration interface and the multi-client Admin

The settings are one typed schema (`ConsultativeSalesSpec.json_schema()`), validated once with every
problem listed with its path (`brain.settings.payment.methods.0.instructions: ...`), normalised on
publish, versioned by the ConfigStore and checked by `check_readiness` (what a business user still has
to fill in). A form-driven interface for one client and an Admin for many clients read the same schema;
there is no Naga-only structure. The templates in `docs/templates/` show every field
(`product.template.yaml`, `commerce.template.yaml`) with clearly marked placeholders that readiness
flags.

## 12. Limitations

- Exact-value checks cannot see amounts spelled out in words or prices written as bare numbers.
- No currency conversion, taxes, discounts, instalments or customer segments.
- A market rests on one configured country fact; a prospect whose country is unknown gets no market rule.
- The register is resolved before the prospect's latest message; a mirrored language does not re-resolve it.
- The kit records only payment claims and proofs as proposals; other facts (what registration collected)
  still come from staff or another system until the kit proposes them.
- Nothing calls the Core's payment confirmation yet: the CRM/Admin that will is not built.
- Approved provider message templates are still not used.
