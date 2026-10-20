"""How a name, an address or a number is searched. Pure; shared by the index writer, the SQL reader
and the in-memory reader so that all three find the same people.

The searchable text of a prospect is its lower-cased name, e-mail address and phone number, plus the
phone number's digits alone (so "20 000 001" finds "+216 20 000 001" and the other way round). A query
is split on spaces and every term must be found (AND). A term of 3 digits or more is also looked up
as digits alone. Matching is a substring match.
"""

MIN_DIGITS = 3
MAX_TERMS = 8
MAX_TERM_LENGTH = 100


def digits_of(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def search_text(name: str | None, email: str | None, phone: str | None) -> str:
    parts = [(name or "").lower(), (email or "").lower(), (phone or "").lower()]
    if phone and (digits := digits_of(phone)):
        parts.append(digits)
    return " ".join(p for p in parts if p)


def search_terms(query: str | None) -> list[tuple[str, str | None]]:
    """(term, digits-only variant or None) for each word of the query, lower-cased."""
    terms: list[tuple[str, str | None]] = []
    for word in (query or "").lower().split()[:MAX_TERMS]:
        word = word[:MAX_TERM_LENGTH]
        digits = digits_of(word)
        terms.append((word, digits if len(digits) >= MIN_DIGITS else None))
    return terms


def matches(text: str, query: str | None) -> bool:
    return all(
        term in text or (digits is not None and digits in text)
        for term, digits in search_terms(query)
    )
