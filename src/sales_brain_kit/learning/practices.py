"""The closed list of things the agent can learn, with their fixed wording.

Everything the agent learns from its results is one of these. A model may *label* a conversation
with these codes; it can never write a rule. That is deliberate:

- a rule cannot hold a name, a price, a link or a quotation of a prospect, because its wording is
  written here, once, by the platform;
- a prospect (or a test message) cannot plant an instruction like "always say X": whatever they
  write, the only thing that can come out is a code from this list;
- no rule can rest on a protected attribute, a place or a language: none of them is a code here
  (see `tests/kit/test_learning_catalog.py`);
- the behaviours that are never acceptable have no code: writing more often, insisting after a
  refusal, creating urgency, getting around a stop request. They cannot be learned whatever they
  seem to earn.

`how` says how the code is detected: from the numbers of the trace (`measured`), or from the words of
the conversation by a model (`read`). A measured code needs no model and no data beyond the trace.
"""

from dataclasses import dataclass
from typing import Literal

# Themes a note belongs to. `general` notes can always help; the others only when the prospect's
# message is about that theme (the same themes as the client's own lessons, `training.THEMES`).
GENERAL = "general"


@dataclass(frozen=True, slots=True, kw_only=True)
class Practice:
    code: str
    kind: Literal["do", "avoid"]
    how: Literal["measured", "read"]
    theme: str
    wording: str  # what the model is told, in English; the client sees it in their own language
    meaning: str  # what the reviewing model is asked to look for


CATALOG: tuple[Practice, ...] = (
    # ---- what tends to go well --------------------------------------------------------------
    Practice(
        code="short_messages",
        kind="do",
        how="measured",
        theme=GENERAL,
        wording="Keep messages short: a few lines the person can read at a glance.",
        meaning="Most of the agent's messages are short.",
    ),
    Practice(
        code="single_question",
        kind="do",
        how="measured",
        theme=GENERAL,
        wording="End with one clear question, not several.",
        meaning="Most of the agent's messages ask exactly one question.",
    ),
    Practice(
        code="spaced_follow_ups",
        kind="do",
        how="measured",
        theme="time",
        wording="When you follow up, leave a few days since the last message.",
        meaning="Every follow-up came at least two days after the previous message.",
    ),
    Practice(
        code="acknowledge_first",
        kind="do",
        how="read",
        theme=GENERAL,
        wording="Show you understood what the person said before you present anything.",
        meaning="The agent first reflects back what the person said or needs, then moves on.",
    ),
    Practice(
        code="answer_doubt_first",
        kind="do",
        how="read",
        theme="think",
        wording="When the person hesitates or doubts, answer that doubt before moving on.",
        meaning="When the person raised a doubt or an objection, the agent addressed it directly.",
    ),
    Practice(
        code="ask_before_proposing",
        kind="do",
        how="read",
        theme="need",
        wording="Understand what the person needs before you propose anything.",
        meaning="The agent asked about the need or situation before proposing an offer or a step.",
    ),
    Practice(
        code="easy_next_step",
        kind="do",
        how="read",
        theme=GENERAL,
        wording="Finish with one easy, concrete next step.",
        meaning="Messages end with a single, simple, concrete next step.",
    ),
    # ---- what tends to lose people ----------------------------------------------------------
    Practice(
        code="long_messages",
        kind="avoid",
        how="measured",
        theme=GENERAL,
        wording="Avoid long messages: say one thing at a time.",
        meaning="The agent's messages were long.",
    ),
    Practice(
        code="quick_follow_up",
        kind="avoid",
        how="measured",
        theme="time",
        wording="Do not follow up too quickly: give the person time to answer first.",
        meaning="A follow-up came less than a day after the previous message, before any answer.",
    ),
    Practice(
        code="pushy_after_hesitation",
        kind="avoid",
        how="read",
        theme="think",
        wording="When the person hesitates, do not push: acknowledge it and leave the door open.",
        meaning="The person hesitated or pulled back and the agent kept pushing the offer.",
    ),
    Practice(
        code="doubt_ignored",
        kind="avoid",
        how="read",
        theme="think",
        wording="Never move on without answering the doubt the person just raised.",
        meaning="The person raised a doubt or objection and the agent did not answer it.",
    ),
    Practice(
        code="needless_question",
        kind="avoid",
        how="read",
        theme="need",
        wording="Do not ask for something the person already said, or that you do not need.",
        meaning="The agent asked something already answered, or not needed to move forward.",
    ),
    Practice(
        code="unkept_promise",
        kind="avoid",
        how="read",
        theme="trust",
        wording="Do not promise what is not confirmed (a call, a document, a date).",
        meaning="The agent promised something it did not deliver or could not confirm.",
    ),
    Practice(
        code="generic_message",
        kind="avoid",
        how="read",
        theme=GENERAL,
        wording="Do not send a generic message: tie it to what this person said.",
        meaning="The message could have been sent to anyone; it ignored what the person said.",
    ),
    Practice(
        code="proposal_too_early",
        kind="avoid",
        how="read",
        theme="need",
        wording="Do not propose the next step before you know what the person needs.",
        meaning="The agent proposed an offer or a step before understanding the need.",
    ),
    Practice(
        code="repeating_yourself",
        kind="avoid",
        how="read",
        theme=GENERAL,
        wording="Do not repeat what you already said: bring something new or stay quiet.",
        meaning="The agent repeated an earlier message or argument.",
    ),
)

BY_CODE: dict[str, Practice] = {p.code: p for p in CATALOG}
DO_CODES = tuple(p.code for p in CATALOG if p.kind == "do")
AVOID_CODES = tuple(p.code for p in CATALOG if p.kind == "avoid")
READ_CODES = tuple(p.code for p in CATALOG if p.how == "read")
