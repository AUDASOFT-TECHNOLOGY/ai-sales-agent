"""Finding the passages of a client's documents that answer a prospect's question.

The strategy is deliberately simple and cheap: **no model call, no embedding service, no cost per
search.** Each ready resource was cut into passages when it was added; a search ranks the passages of
the products in play with BM25 (word statistics, accent- and case-insensitive) and returns a handful.
That keeps three promises:

- *context limits*: the caller asks for a few passages, never a whole document;
- *cost*: a search is local arithmetic over text already extracted;
- *confidentiality*: only the passages of the asking client's own products are ever considered, and
  the caller strips links, amounts and account-like numbers before anything reaches a model.

Indexes are kept in memory per client and product and rebuilt when the store says the text changed.
The Brain's side of this contract (`sales_brain_kit.consultative.knowledge.KnowledgeSource`) is
satisfied structurally: this package does not import the kit.
"""

import math
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sales_app.resources.models import Chunk
from sales_app.resources.store import ResourceStore

K1 = 1.5
B = 0.75
MAX_PER_RESOURCE = (
    2  # passages of one document in a single answer, so one long file does not crowd out the rest
)
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "we",
        "what",
        "with",
        "you",
        "your",
        "au",
        "aux",
        "ce",
        "ces",
        "dans",
        "de",
        "des",
        "du",
        "elle",
        "en",
        "est",
        "et",
        "il",
        "je",
        "la",
        "le",
        "les",
        "leur",
        "ma",
        "mon",
        "ne",
        "nous",
        "on",
        "ou",
        "par",
        "pas",
        "pour",
        "que",
        "qui",
        "se",
        "sa",
        "son",
        "sur",
        "te",
        "ton",
        "un",
        "une",
        "vos",
        "votre",
        "vous",
    ]
)


def _tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    out = []
    for word in re.findall(r"\w+", folded):
        if len(word) < 2 or word in STOPWORDS:
            continue
        out.append(word[:-1] if word.endswith("s") and len(word) > 3 else word)
    return out


@dataclass(frozen=True, slots=True)
class Snippet:
    """What the Brain asks for: an excerpt, the product it belongs to and the document's title."""

    product_id: str
    source: str
    text: str


@dataclass(slots=True)
class _Index:
    stamp: str
    chunks: list[Chunk]
    counts: list[Counter[str]]
    lengths: list[int]
    document_frequency: Counter[str]
    average_length: float


def _build(stamp: str, chunks: list[Chunk]) -> _Index:
    counts = [Counter(_tokens(c.text)) for c in chunks]
    lengths = [sum(c.values()) for c in counts]
    frequency: Counter[str] = Counter()
    for c in counts:
        frequency.update(c.keys())
    average = (sum(lengths) / len(lengths)) if lengths else 0.0
    return _Index(stamp, chunks, counts, lengths, frequency, average)


class KnowledgeIndex:
    """Searches the documents of any client; `for_client` binds one."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], _Index] = {}

    def __repr__(self) -> str:
        return "KnowledgeIndex()"

    def for_client(self, client_id: str) -> "ClientKnowledge":
        return ClientKnowledge(self, client_id)

    def _index(self, client_id: str, product_id: str) -> _Index:
        stamp = self._store.stamp(client_id, product_id)
        key = (client_id, product_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.stamp == stamp:
                return cached
        built = _build(stamp, self._store.chunks(client_id, product_id))
        with self._lock:
            self._cache[key] = built
        return built

    def search(
        self, client_id: str, product_ids: Sequence[str], query: str, limit: int
    ) -> list[Snippet]:
        wanted = [t for t in dict.fromkeys(_tokens(query))]
        scored: list[tuple[float, Chunk]] = []
        first: list[Chunk] = []
        for product_id in dict.fromkeys(product_ids):
            index = self._index(client_id, product_id)
            if not index.chunks:
                continue
            seen_resources: set[str] = set()
            for chunk in index.chunks:
                if chunk.resource_id not in seen_resources:
                    seen_resources.add(chunk.resource_id)
                    first.append(chunk)
            total = len(index.chunks)
            for position, chunk in enumerate(index.chunks):
                counts, length = index.counts[position], index.lengths[position]
                score = 0.0
                for token in wanted:
                    frequency = counts.get(token, 0)
                    if frequency == 0:
                        continue
                    df = index.document_frequency[token]
                    idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                    norm = frequency + K1 * (1 - B + B * length / (index.average_length or 1))
                    score += idf * frequency * (K1 + 1) / norm
                if score > 0:
                    scored.append((score, chunk))
        scored.sort(key=lambda pair: -pair[0])
        chosen: list[Chunk] = []
        per_resource: Counter[str] = Counter()
        for _, chunk in scored:
            if per_resource[chunk.resource_id] >= MAX_PER_RESOURCE:
                continue
            per_resource[chunk.resource_id] += 1
            chosen.append(chunk)
            if len(chosen) >= limit:
                break
        if not chosen and len(set(product_ids)) == 1:
            # Nothing matched a question like "tell me more": the opening of the documents is the
            # best overview there is, and only for the one product in play.
            chosen = first[:MAX_PER_RESOURCE]
        return [
            Snippet(product_id=c.product_id, source=c.title, text=c.text) for c in chosen[:limit]
        ]


class ClientKnowledge:
    """One client's documents, as the Brain consults them."""

    def __init__(self, index: KnowledgeIndex, client_id: str) -> None:
        self._index = index
        self._client_id = client_id

    def search(self, product_ids: Sequence[str], query: str, limit: int) -> list[Snippet]:
        return self._index.search(self._client_id, product_ids, query, limit)
