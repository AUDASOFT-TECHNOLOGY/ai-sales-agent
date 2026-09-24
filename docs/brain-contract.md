# Brain contract

A Brain is one client's commercial intelligence behind a single port, [ports/brain.py](../src/sales_core/ports/brain.py):

```python
class Brain(Protocol):
    def decide(self, request: BrainRequest) -> BrainDecision: ...
```

- **Input** (`BrainRequest`): the client context (with the Brain's own validated settings), the
  prospect, the conversation, the action that asks (first contact, follow-up, reply...) and `now`.
- **Output** (`BrainDecision`): send a message, schedule a follow-up (date and type), hand off to a
  human, close, or do nothing, plus an optional stage label and a rationale for the audit trail.
- A Brain holds no I/O of its own. It receives its collaborators (an `LLMProvider`) at construction,
  through the entry point named in `client.yaml` (`brain.factory`, a `BrainFactory`:
  `(llm, settings) -> Brain`).
- A Brain owns offer, tone, language, qualification, objection handling, follow-up cadence and
  wording. The Core owns none of these: it owns state, persistence, safety, approval, idempotency,
  the daily quota and delivery. See [engine-design.md](engine-design.md).

## Declaring settings

A Brain package may also expose, next to its factory, a module attribute:

```python
SETTINGS_SPEC: BrainSettingsSpec  # family, schema_version, json_schema(), validate(), prepare()
```

The application uses it to validate a client's Brain settings before they become a stored version
and when they are loaded, and to hand the Brain its settings normalised to the latest version. A
future admin interface renders forms from `json_schema()`. Without a spec, settings stay opaque.
The shared "consultative sales" schema is in [brain-settings.md](brain-settings.md).

## Facts and conversation origin

Two generic additions carry what a Brain needs to reason about a prospect. Both are additive and
default to "nothing", so existing Brains and callers are unaffected.

- `BrainRequest.facts`: a tuple of `Fact`. A fact is a free-form `key`, a text `value`, a
  `FactStatus` (`confirmed`, `discovered`, `hypothesis`), an optional `confidence` (0..1), optional
  `FactEvidence` and timestamps. Which keys exist is the Brain's business; the Core names none.
- `FactEvidence` (`FactSource`: prospect, client_data, conversation, system, human,
  brain_inference; plus optional message id, excerpt, time, recorder) is for facts whose source
  matters: sensitive ones, or ones decisive for qualification or recommendation. Ordinary
  conversational facts carry none. The Core does not enforce when evidence is required.
- The status distinction is explicit and nothing promotes it: a hypothesis stays a hypothesis until
  something else records a new fact. The only structural guard is that a fact resting on a
  `brain_inference` cannot be `confirmed`.
- `Conversation.origin` (`ContactOrigin`: `outbound` when the Sales Agent started it, `inbound`
  when the prospect did, `unknown` for history that does not say). A Brain reads it as
  `request.conversation.origin`. It defaults to `unknown`.
- `BrainDecision` is unchanged.

The Engine fills them in per request: `origin` is `inbound` for a reply to a prospect's message,
`outbound` for a first contact and otherwise what the repository reports (`unknown`); `facts` come
from an optional `FactsReader` (`sales_core.ports`) and are empty without one: every recorded
observation of the prospect, oldest first. The same key can appear several times (it may take
another value later); the newest is the current one. Recording is the separate append-only
`FactsWriter` port, implemented by the Google Sheets adapter (`Facts` tab) and the fake repository;
an observation is identified by `fact_identity`, so recording it twice keeps one. Nothing extracts
or applies facts yet, and repositories do not persist the origin.

## Not part of the contract yet

Product interests, constraints on what a decision may be, applying facts from a decision, extracting
them, and persisting the origin. They will extend `BrainRequest` and `BrainDecision` additively when
the Core work for them lands.
