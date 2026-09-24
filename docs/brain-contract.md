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

## Not part of the contract yet

Facts learned about a prospect, product interests, the origin of a conversation (outbound or
inbound) and constraints on what a decision may be are designed but not built; they will extend
`BrainRequest` and `BrainDecision` additively when the Core work for them lands.
