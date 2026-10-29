"""Draft lessons found in the documents a client attached to a product.

A brochure, an FAQ page or a page of testimonials already holds what the agent should know how to say.
This reads the passages the application extracted and proposes, for the client to accept or ignore:

- **faq**: a question (a sentence ending with "?") and the sentences that answer it;
- **proof**: a quoted passage, with the name that follows it when there is one; every passage of a
  document the client filed as a testimonial is a candidate too.

Nothing here calls a model, costs anything or changes anything: a suggestion becomes a lesson only when
the client adds it. A passage that mentions a price, a link, an e-mail address or an account number is
not proposed (a lesson never holds one: those reach a prospect through the catalog and the payment
methods); it is counted so the client knows it was left out.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from sales_app.resources.models import Chunk, Resource

MAX_SUGGESTIONS = 12
QUESTION_CHARS = (12, 200)
ANSWER_CHARS = 450
QUOTE_CHARS = (40, 400)
LESSON_CHARS = 600  # the longest text a lesson holds
_SENTENCE = re.compile(r"(?<=[.!?؟])\s+")
_QUOTE = re.compile(r"[“«\"]\s*([^”»\"]{40,400}?)\s*[”»\"]\s*(?:[—–-]\s*([^.\n\"“”«»]{3,60}))?")
_PREFIX = re.compile(r"(?i)^\s*(?:q|question|r|réponse|reponse|a|answer)\s*[:.)-]\s*")


@dataclass(frozen=True, slots=True)
class Suggestion:
    kind: str  # faq | proof
    text: str
    when: str | None  # the question, for a faq
    source: str  # the title of the document it comes from
    attribution: str | None  # whose words, for a proof


@dataclass(frozen=True, slots=True)
class Suggestions:
    items: tuple[Suggestion, ...]
    left_out: int  # passages that mention a price, a link or an account number


# What a lesson refuses (a link, an e-mail address, an amount with a currency, an account-like
# number). The Brain's settings are the authority and they refuse these again when the lesson is saved;
# this only keeps such passages out of the suggestions. (The app never imports the Brain's kit.)
_EXACT = re.compile(
    r"(?i:(?:https?://|www\.)\S+)|[\w-]+(?:\.[\w-]+)+/\S*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|\d{9,}|(?<!\d)\d{3,4}(?:[  ]\d{3,4}){2,}(?!\d)"
    r"|(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])\s?\d|\d\s?(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])"
    r"|[$€£¥₹₺₽₩₦₫₪]\s?\d|\d\s?[$€£¥₹₺₽₩₦₫₪]"
)


def _holds_exact_value(text: str) -> bool:
    return _EXACT.search(text) is not None


def _key(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return re.sub(r"\W+", "", "".join(c for c in folded if not unicodedata.combining(c)))


def _clean(text: str) -> str:
    return " ".join(_PREFIX.sub("", text).split())


def suggest(resources: Sequence[Resource], chunks: Sequence[Chunk]) -> Suggestions:
    testimonials = {r.id for r in resources if r.category == "testimonial"}
    seen: set[str] = set()
    found: list[Suggestion] = []
    left_out = 0

    def offer(item: Suggestion) -> None:
        nonlocal left_out
        parts = [item.text, item.when or "", item.attribution or ""]
        if any(_holds_exact_value(p) for p in parts):
            left_out += 1
            return
        key = _key(f"{item.kind}{item.when or ''}{item.text}")
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    for chunk in chunks:
        sentences = [
            _clean(part)
            for line in chunk.text.splitlines()
            for part in _SENTENCE.split(line.strip())
            if part.strip()
        ]
        for i, sentence in enumerate(sentences):
            if not (
                QUESTION_CHARS[0] <= len(sentence) <= QUESTION_CHARS[1] and sentence.endswith("?")
            ):
                continue
            answer: list[str] = []
            for follower in sentences[i + 1 : i + 4]:
                # A line without a closing mark is a heading, not part of the answer.
                if follower[-1] not in ".!…؟" or len(" ".join([*answer, follower])) > ANSWER_CHARS:
                    break
                answer.append(follower)
            if answer:
                offer(
                    Suggestion("faq", _clean(" ".join(answer)), _clean(sentence), chunk.title, None)
                )
        for quoted in _QUOTE.finditer(chunk.text):
            text, who = _clean(quoted.group(1)), (quoted.group(2) or "").strip() or None
            if QUOTE_CHARS[0] <= len(text) <= QUOTE_CHARS[1]:
                offer(Suggestion("proof", text, None, chunk.title, who))
        if chunk.resource_id in testimonials and len(chunk.text) >= QUOTE_CHARS[0]:
            offer(
                Suggestion(
                    "proof", _clean(chunk.text)[:LESSON_CHARS], None, chunk.title, chunk.title
                )
            )

    ordered = sorted(found, key=lambda s: s.kind != "faq")  # stable: the document's order otherwise
    return Suggestions(tuple(ordered[:MAX_SUGGESTIONS]), left_out)
