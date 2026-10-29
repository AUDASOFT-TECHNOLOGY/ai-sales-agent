# Learning from results

The agent improves by tying what it did to how conversations ended. The client configures nothing: the
agent learns on its own, the client only sees the result (Training → Overview → "What your agent has
learned") and may set a point aside or confirm it.

## Where each part lives

| Part | Layer | Why there |
|---|---|---|
| Result of a conversation (`achieved`, `declined`, `uneasy`, `handed_over`, `quiet`, `open`) | `sales_core/learning/results.py` | neutral words, derived from data the Core already has |
| Anonymous trace of each decision | `sales_core/learning/trace.py`, written by the Engine through a `TraceSink` | the Engine knows every decision; the trace holds no text and no person |
| Notes given to the Brain | `BrainRequest.learned_notes` (`sales_core/contracts/learning.py`) | the Engine reads them through a `NotesProvider` |
| What can be learned, how to review, which rules hold | `sales_brain_kit/learning/` | what counts as good practice is commercial, so it belongs to the kit |
| Registers (traces, verdicts, notes), the run, the Client API | `sales_app` (`learning_store.py`, `learning_service.py`, `persistence/learning_rows.py`, `api/client_learning.py`) | storage and orchestration |
| The scheduled run | `Job.LEARN` in the scheduler | its own lease; never ahead of the work that talks to people |

## Results, and how false positives are avoided

- A **rehearsal** (`inbound-test-…`) has no result and is never traced.
- **Achieved**: a payment a person confirmed, or the stage the client marked as the goal (`won`; also
  `appointment` when the main goal is a meeting, `qualified` for a lead). Without such a stage, only a
  confirmed payment counts; if neither exists there is nothing to learn from wins, and the screen says so.
- **Declined**: opt-out, closed conversation, or an explicit refusal not followed by a message from the agent.
- **Uneasy**: a stop request after several unanswered messages, or one that came with impatience.
- **Handed over**: a person took the conversation, or it waits for one. Kept apart; it counts for nothing.
- **Quiet**: no message for 14 days. **A silence is never a refusal**: it is not reviewed and teaches nothing.
- Only achieved, declined and uneasy are final and reviewed, once each.

## What the agent keeps

Nothing that identifies a person. A person and an action become opaque references
(`anonymous_ref`), a message becomes its length, its number of questions and the time since the
previous one, and the Brain's own labels are kept only when they are plain tokens. A review is a verdict
in a closed vocabulary. Traces are deleted when their conversation is reviewed (or after 60 days);
verdicts after 180 days. `forget_case` removes everything about one conversation.

## The closed list of things that can be learned

`sales_brain_kit/learning/practices.py` is the whole vocabulary: 7 things that tend to go well and 9 that
tend to lose people, each with a fixed wording written by the platform. A model may **label** a
conversation with these codes; it can never write a rule. Consequently a rule cannot contain a name, a
price, a link or a quotation, a prospect cannot plant an instruction, and no rule rests on a protected
attribute, a place or a language. Behaviours that are never acceptable (writing more often, insisting
after a refusal, urgency, getting around a stop request) have no code and cannot be learned.

Some codes are measured from the trace alone (message length, questions, delay between follow-ups); the
others are read from the words by the client's model, once the conversation is over. The model sees the
exchange without names, links, addresses, amounts or long numbers, is told the text is material to read and
not instructions, and may answer "unknown".

## From verdicts to rules

`sales_brain_kit/learning/induction.py`, recomputed from the retained verdicts each time:

- fewer than 3 independent conversations: nothing;
- 3 to 4: **watching** (shown to the client, not given to the agent);
- 5 or more, **and** at least twice as likely on its side as on the other side, with at least 3
  conversations on the other side to compare with: **active**, applied automatically;
- 8 or more with a ratio of 3 or more: `high` evidence;
- a rule as common in conversations that went the other way is dropped;
- a reading the model was unsure of does not count (a measurement always does);
- at most 6 active rules, at most 4 shown per message; the client's confirmed rules come first;
- a rule the client switched off stays off; a rule with no evidence left ages out;
- each active rule is judged against what happened since it applied (8 conversations on each side): if
  conversations that used it do no better, it is switched off for good ("not helping").

## How it reaches the agent

The active notes are added to the system message under their own heading, **after** the client's own
lessons and with the sentence that the client's lessons and the Rules always win. Thematic notes are shown
only when the prospect's message is about that theme; product notes only while that product is in play; a
note that contradicts the client's settings is not shown.

## Cost

Each review is one call of the client's own model, made by the scheduled run (never during a
conversation), counted like every other call (`RecordingLLMProvider`), skipped when the credits are used up,
and bounded to 12 per run.

## Known limits

- The quality of the model's labelling has only been exercised with a scripted fake model.
- The rules are drawn from many conversations; a business with few finished conversations, or with no
  goal stage, learns little or nothing, and the screen says why.
- Handed-over conversations are not learned from.
- The opt-out flow does not yet call `forget_case`; deletion on request is available but not wired to it.
