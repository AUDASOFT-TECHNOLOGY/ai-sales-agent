# The Naga Brain

Status: **wiring implemented, business data not entered.** `brains/naga/naga_brain/` is a thin package;
everything Naga-specific is *data* that does not exist in the repository yet, and none is shipped.

> **No Naga business fact is written anywhere in this repository**: no product, price, URL, language,
> market, payment method, bank detail, promise, cadence or sales process, and no "provisional" default
> standing in for one. Naga's Brain settings are `{}` until Naga (through the client-facing
> configuration interface, later) enters them. Until then Naga cannot be enabled: its settings do not
> validate, and the application says exactly what is missing.

## 1. What the package is

| File | Job |
|---|---|
| `naga_brain/brain.py` | `SETTINGS_SPEC` (the kit's consultative spec), `NagaBrain`, `create_brain`: wiring only |
| `clients/naga/client.yaml` | operator wiring (Sheets, LLM, WhatsApp, schedule) and `brain.settings: {}` |

`NagaBrain` is the kit's `ConsultativeSalesBrain` with no override: it implements the Core's `Brain`
port and adds no interface. The package imports only `sales_core` (contracts, ports) and
`sales_brain_kit` and holds no product, price, language, market, payment, cadence or handoff logic (tests
enforce it). It adds **no rules of its own** to the settings: the validation and readiness checks that
first lived here were generic, and now live in the kit for every client
([brain-settings.md](brain-settings.md), `sales_brain_kit.check_readiness`).

## 2. How Naga is configured

Naga is configured like any client: its Brain settings follow the consultative schema
(`ConsultativeSalesSpec`), are validated when published (`sales-agent config publish naga FILE`) and when
loaded, and are stored versioned by the ConfigStore. The client-facing configuration interface will let
Naga enter its catalog, specifications, commercial strategy, languages, market rules, payment
configuration, conversion workflow and follow-up rules; the later multi-client Admin will manage Naga and
other clients through the same schema. The templates in `docs/templates/` show every field.

Which capabilities are available to Naga, all generic and all driven by that configuration:

- multiple products and categories, product recommendation, discovery, qualification, objections,
  follow-up, reminders, callbacks, appointments ([brain-kit.md](brain-kit.md));
- markets by country of residence, adaptive language and register, prices per market and country
  ([brain-commerce.md](brain-commerce.md));
- online and offline payment, by product, market, country and currency, with exact values that never
  pass through the model;
- a conversion workflow (registration, payment, appointment, handoff, close) whose use of a person is
  chosen step by step;
- outbound and inbound conversations with the same Brain.

## 3. What Naga must provide

Everything Naga-specific. Nothing below is known to the repository:

1. **Languages**: which it serves, the default, the priority of the signals, whether the Agent may follow
   the prospect's language, and register rules (formality and style) by market and language.
2. **Markets**: whether it has any, the countries of each, the language, catalog, workflow and cadence of
   each, and where a prospect's country comes from (a lead column, the prospect's own statement, a person).
3. **The catalog**: for each product its id, names, descriptions, audience, benefits, verified proof
   points, sales arguments, objections, qualification rules, restrictions, URL and call to action, and
   its price and currency per market or country (or a statement that it is on request).
4. **Payment**: each method (online, offline, other), where and for which products and currencies it is
   available, the exact instructions, account details or payment links, and how a payment is confirmed.
5. **The conversion workflow**: which steps take a convinced prospect to a completed sale (for example
   registration, payment, confirmation, or a handoff or an appointment instead), what shows each step
   is done, and where a person is involved, required or not.
6. **Handoff**: when a person must, may or need not be involved; whether handoff exists at all.
7. **Follow-up**: the cadence per situation, by market or product where it differs.
8. **Discovery**: which facts matter for its qualification, and the Sheet columns that carry a product,
   a language or a country for a lead.
9. **Voice**: tone, formality, principles, message limits.
10. **Operational values** unrelated to the Brain: spreadsheet id, model, WhatsApp accounts.

`sales_brain_kit.check_readiness` turns this list into a checklist an interface can show; nothing
blocks the Brain except invalid settings, and `enabled` in the client file is the switch.

## 4. What is code and what is data

**Data (Naga's, never Python):** every value above.

**Generic invariants (the same for every client, not editable):** a hypothesis is never a confirmed
fact and never places a prospect in a market or completes a step; claims come only from configuration;
exact values (prices, payment details, links) never reach a model and are inserted by deterministic
code; the model proposes and validation decides; one call plus one correction; at most two questions
per message; follow-ups at least an hour apart; no follow-up after a "no"; protected attributes and
nationality never drive anything; the Brain sends, stores and schedules nothing: the Core does.

## 5. Limitations

- Naga cannot run until its settings exist and are valid.
- The generic limitations of [brain-commerce.md](brain-commerce.md) section 12 apply, notably that a
  Brain cannot yet propose facts (so what registration collects, or that a payment proof arrived, must be
  recorded by staff or another system) and that a payment provider's confirmation has no inbound path.
