"""Reference material: excerpts of the documents a client attached to its products.

The Brain answers from its settings. A client may also attach documents and pages to a product
(brochures, price lists, FAQs); the application extracts their text and can search it. This module is
the Brain's side of that: a `KnowledgeSource` is anything that finds excerpts for some products and a
question, and `retrieve_knowledge` asks it, with strict limits, for the request in hand.

What is guaranteed here, whatever the source returns:

- **Bounded.** At most `MAX_SNIPPETS` excerpts of `MAX_SNIPPET_CHARS` characters each, so a large
  library costs the same as a small one and never fills the model's context.
- **No exact values.** Links, e-mail addresses, amounts with a currency and account-like numbers are
  removed from every excerpt. Prices, payment details and links reach a prospect only through the
  placeholders of `exact.py`, never because a document mentioned them.
- **Optional.** A source that is missing, slow to fail or broken never stops a conversation: the Brain
  simply answers without the material.
- **Untrusted.** The excerpts are quoted data. The prompt says so and the model is not to follow
  instructions found in them.

The Brain claims nothing it was not given: an excerpt is shown only if the application really holds
text extracted from the client's document.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sales_brain_kit.consultative.context import CommercialContext
from sales_brain_kit.consultative.exact import strip_exact_values

log = logging.getLogger(__name__)

MAX_SNIPPETS = 4
MAX_SNIPPET_CHARS = 700
MAX_TITLE_CHARS = 120
MAX_QUERY_CHARS = 500
PRODUCTS_SEARCHED = 5  # when no product is in focus, the first offerable ones


@dataclass(frozen=True, slots=True)
class KnowledgeSnippet:
    product_id: str
    source: str  # the title the client gave the document
    text: str


class KnowledgeSource(Protocol):
    """Finds excerpts of a client's documents for some of its products and a question."""

    def search(
        self, product_ids: Sequence[str], query: str, limit: int
    ) -> Sequence[KnowledgeSnippet]: ...


def _clean(snippet: KnowledgeSnippet) -> KnowledgeSnippet | None:
    text = " ".join(strip_exact_values(snippet.text).split())[:MAX_SNIPPET_CHARS]
    # What is left once the removed values are set aside must still say something.
    if not any(c.isalpha() for c in text.replace("[…]", " ")):
        return None
    title = " ".join(strip_exact_values(snippet.source).split())[:MAX_TITLE_CHARS]
    return KnowledgeSnippet(product_id=snippet.product_id, source=title, text=text)


def retrieve_knowledge(
    context: CommercialContext, source: KnowledgeSource | None
) -> tuple[KnowledgeSnippet, ...]:
    """The excerpts that help answer this request, or none."""
    if source is None:
        return ()
    selection = context.selection
    products = selection.focus or selection.candidates[:PRODUCTS_SEARCHED]
    if not products:
        return ()
    last = context.history.last_inbound
    asked = last.text if last is not None and last.text else ""
    names = " ".join(context.catalog.name_of(p, context.language.language) or "" for p in products)
    query = f"{asked} {names}".strip()[:MAX_QUERY_CHARS]
    if not query:
        return ()
    allowed = {p.id for p in products}
    try:
        found = source.search([p.id for p in products], query, MAX_SNIPPETS)
    except Exception:  # noqa: BLE001 - reference material is optional: answer without it
        log.warning("reference material could not be searched")
        return ()
    cleaned = (_clean(s) for s in found if s.product_id in allowed)
    return tuple(s for s in cleaned if s is not None)[:MAX_SNIPPETS]
