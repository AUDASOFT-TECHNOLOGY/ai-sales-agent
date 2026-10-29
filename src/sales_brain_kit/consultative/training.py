"""What the client taught the agent, as instructions for the model.

Two things reach the system message:

- the **sales method** (`communication.playbook`): how the client leads a conversation, closes and
  uses proof. Each choice has one fixed wording written by the platform, so a choice always means the
  same tested thing whatever the product or the business;
- the **lessons** (`communication.training`): habits, red lines, answers to situations, to
  objections and to frequent questions, and testimonials.

A client may teach many lessons, but the model is shown only the few that help *this* message, so a
large training costs the same as a small one. Habits and red lines always apply. The others are chosen
by closeness to what the prospect just wrote (plain word overlap: no model call, no cost) and by the
product in play: a lesson tied to a product is shown only when that product is. Whatever is chosen is
taught as a way of speaking, never as a script, and the rules of the prompt always win over a lesson.
"""

import re
import unicodedata
from collections.abc import Sequence

from sales_brain_kit.consultative.context import CommercialContext
from sales_brain_kit.settings.strategy import Playbook, TrainingLesson

# How many lessons of each kind one message shows.
SHOWN = {"example": 4, "objection": 3, "faq": 3, "proof": 2}

APPROACH: dict[str, str] = {
    "consultative": (
        "Sell by understanding first. Let the prospect's own words and situation decide what you "
        "bring up, link every benefit to something they said, and never push a product that does "
        "not fit what they need."
    ),
    "challenger": (
        "Sell by teaching. Bring a useful insight that changes how the prospect sees their "
        "problem, stay respectful when you disagree, then show how the offer follows from it."
    ),
    "guide": (
        "Sell like a trusted friend who knows the field. Be warm and simple, reassure, make the "
        "next step feel easy, and never overwhelm with detail."
    ),
    "direct": (
        "Sell with clarity and speed. Go straight to the point, state the value in a sentence, "
        "and propose the next step as soon as the prospect shows interest."
    ),
}
CLOSING: dict[str, str] = {
    "soft": "Close gently: invite the prospect to the next step and leave them free to say no.",
    "choice": (
        "Close by offering two easy ways forward (for example two slots, or two options) so the "
        "answer is a choice, not a yes or no."
    ),
    "assumptive": (
        "Close as if the next step is natural once interest is clear: say what happens next and "
        "ask for the one detail needed to do it."
    ),
    "direct": (
        "Close plainly: when interest is clear, ask for the decision or the next step in one "
        "short sentence."
    ),
}
URGENCY: dict[str, str] = {
    "none": "Never create urgency or pressure.",
    "real_reasons_only": (
        "You may mention a deadline or a limited availability only when the catalog or a lesson "
        "truly states it; never invent urgency."
    ),
}
PROOF: dict[str, str] = {
    "when_relevant": (
        "When it helps the prospect decide, bring one proof at a time (a result or a testimonial "
        "from the lessons or the verified proof points), tied to their situation."
    ),
    "never": "Do not bring testimonials or results into the conversation.",
}
# Always true, whatever the method: the discipline that makes the agent convincing and honest.
CRAFT = (
    (
        "Make the prospect feel understood before you present anything: name what they said in a "
        "few words."
    ),
    (
        "When a prospect hesitates, find out what is really behind it before you answer it, then "
        "answer that, not the first words."
    ),
    "One idea, one benefit and one next step per message.",
)

_WORD = re.compile(r"\w+")
_SKIP = frozenset(
    {
        *("the", "and", "for", "you", "are", "with", "that", "this", "from", "have", "not"),
        *("but", "your", "can", "was", "our", "what", "when", "how"),
        *("les", "des", "une", "que", "qui", "pour", "dans", "est", "pas", "sur", "vous"),
        *("nous", "mais", "avec", "son", "ses", "aux", "par", "plus", "ceci", "cela"),
        *("quel", "quelle", "quand", "comment"),
    }
)


def _tokens(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    out = set()
    for word in _WORD.findall(folded):
        if len(word) < 3 or word in _SKIP:
            continue
        out.add(word[:-1] if word.endswith("s") and len(word) > 4 else word)
    return out


def playbook_lines(playbook: Playbook) -> list[str]:
    """The sales method as prompt lines: the client's choices, then the craft every method shares."""
    lines = [
        f"- Approach: {APPROACH[playbook.approach]}",
        f"- Closing: {CLOSING[playbook.closing]}",
        f"- Urgency: {URGENCY[playbook.urgency]}",
        f"- Proof: {PROOF[playbook.social_proof]}",
    ]
    if playbook.pitch:
        lines.append(f"- The business in the client's own words: {playbook.pitch}")
    if playbook.ideal_customer:
        lines.append(f"- Who the business serves best: {playbook.ideal_customer}")
    lines += [f"- Craft: {item}" for item in CRAFT]
    return lines


def _relevant(
    lessons: Sequence[TrainingLesson], message: set[str], limit: int
) -> list[TrainingLesson]:
    """The `limit` lessons closest to the message, the others in the client's order when none is."""
    scored = [
        (len(message & _tokens(f"{l.when or ''} {l.text}")), -position, l)
        for position, l in enumerate(lessons)
    ]
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [l for _, _, l in scored[:limit]]


def chosen_lessons(context: CommercialContext) -> list[TrainingLesson]:
    """The lessons this message is taught: red lines and habits, then the closest of each other kind
    among those that hold for the products in play."""
    lessons = context.settings.communication.training
    if not lessons:
        return []
    in_play = {p.id for p in context.selection.focus}
    last = context.history.last_inbound
    message = _tokens(last.text) if last is not None and last.text else set()
    out = [l for l in lessons if l.kind == "never"] + [l for l in lessons if l.kind == "habit"]
    for kind, limit in SHOWN.items():
        if kind == "proof" and context.settings.communication.playbook.social_proof == "never":
            continue
        # Without a product in focus only the lessons that hold for every product are shown.
        pool = [
            l for l in lessons if l.kind == kind and (l.product is None or l.product in in_play)
        ]
        out += _relevant(pool, message, limit)
    return out


def lesson_line(lesson: TrainingLesson) -> str:
    where = f" (about {lesson.product})" if lesson.product else ""
    why = f" Reason: {lesson.why}" if lesson.why else ""
    match lesson.kind:
        case "habit":
            return f"- Habit of the business: {lesson.text}"
        case "never":
            return f"- Never: {lesson.text}"
        case "example":
            return (
                f"- Taught answer{where}. When {lesson.when}, the business answers: "
                f'"{lesson.text}" Keep the spirit and the wording style, adapt it to the '
                f"conversation, never copy it blindly.{why}"
            )
        case "objection":
            return (
                f"- Taught objection handling{where}. When the prospect says something like "
                f'"{lesson.when}", the business answers: "{lesson.text}" Keep the approach, '
                f"adapt the words to what was said.{why}"
            )
        case "faq":
            return (
                f'- Frequent question{where}: "{lesson.when}" The business answers: '
                f'"{lesson.text}" Use it only if the prospect really asks this.{why}'
            )
        case _:  # proof
            who = f" ({lesson.source})" if lesson.source else ""
            return (
                f'- Proof the business stands behind{where}{who}: "{lesson.text}" '
                "Quote it only as given, only when it helps this prospect decide."
            )


def training_lines(context: CommercialContext) -> list[str]:
    """The sections of the system message about the method and the lessons."""
    communication = context.settings.communication
    lines = ["", "Sales method", *playbook_lines(communication.playbook)]
    chosen = chosen_lessons(context)
    if chosen:
        lines += [
            "",
            "Taught by the business (shapes how you speak and sell; the Rules below always win)",
            *(lesson_line(l) for l in chosen),
        ]
    return lines
