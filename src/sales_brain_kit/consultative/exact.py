"""Exact values: prices, payment details and links reach the prospect without passing through a model.

Some values must be reproduced character for character and must never be invented, completed,
reformatted or calculated: an amount, an account number, a payment URL, a payment instruction. The
mechanism does not rely on asking a model to be careful:

1. The model is never given these values. It is given *placeholders* for what may be said now:
   `[[price:<product>]]`, `[[payment:<method>]]`, `[[booking:<step>]]`.
2. It writes its message with the placeholders in it.
3. Validation refuses any placeholder that is not available now, any malformed one, and any web
   address, amount with a currency or account-like number the model wrote itself.
4. Only then does deterministic code replace each placeholder with the configured text.

What is caught in step 3 is deliberately conservative and language-neutral; it cannot recognise an
amount spelled out in words. That is why the values themselves never reach the model: the
structural guarantee is 1 and 4, and 3 is defence in depth.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sales_brain_kit.settings import ConsultativeSalesSettings

KINDS = ("price", "payment", "booking")
MAX_EXPANDED_CHARS = 3900  # what one message may grow to once the exact values are in

_PLACEHOLDER = re.compile(r"\[\[(price|payment|booking):([a-z0-9][a-z0-9_-]{0,63})\]\]")
_LOOKS_LIKE_ONE = re.compile(r"(?i)[\[{(<]\s*(?:price|payment|booking)\s*:")
_HTTP = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'\]]+")
_BARE_DOMAIN = re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/[^\s<>\"'\]]*")
_CURRENCY_SYMBOLS = "$€£¥₹₺₽₩₦₫₪"
_AMOUNT = re.compile(
    rf"(?:(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z])\s?\d|\d\s?(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z])"
    rf"|[{_CURRENCY_SYMBOLS}]\s?\d|\d\s?[{_CURRENCY_SYMBOLS}])"
)
_LONG_DIGITS = re.compile(r"\d{9,}")
_DIGIT_GROUPS = re.compile(r"(?<!\d)\d{3,4}(?:[  ]\d{3,4}){2,}(?!\d)")
_IBAN_LIKE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,}\b")
_TRAILING = ".,;:!?)\"'"


@dataclass(frozen=True, slots=True)
class Placeholder:
    kind: str
    ref: str

    @property
    def token(self) -> str:
        return f"[[{self.kind}:{self.ref}]]"


def find_placeholders(text: str) -> tuple[Placeholder, ...]:
    """Every placeholder in the text, in order (repeats kept)."""
    return tuple(Placeholder(m.group(1), m.group(2)) for m in _PLACEHOLDER.finditer(text))


def without_placeholders(text: str) -> str:
    return _PLACEHOLDER.sub(" ", text)


def expand(text: str, values: Mapping[Placeholder, str]) -> str:
    """The text with each placeholder replaced by its exact configured value."""
    return _PLACEHOLDER.sub(lambda m: values[Placeholder(m.group(1), m.group(2))], text)


def unexpected_content(text: str, allowed_urls: Iterable[str]) -> list[str]:
    """What a model must not have written itself: reasons, empty when the text is clean.

    Expects text with its placeholders already removed.
    """
    problems: list[str] = []
    if "[[" in text or "]]" in text or _LOOKS_LIKE_ONE.search(text):
        problems.append("a placeholder is malformed (use exactly [[kind:id]])")
    allowed = {_normalise_url(url) for url in allowed_urls}
    remaining = text
    for match in _HTTP.finditer(text):
        url = _normalise_url(match.group(0))
        if url not in allowed:
            problems.append(f"the address {url!r} is not a configured link; do not write links")
    remaining = _HTTP.sub(" ", remaining)
    for match in _BARE_DOMAIN.finditer(remaining):
        url = _normalise_url(match.group(0))
        if url not in allowed and f"https://{url}" not in allowed:
            problems.append(f"the address {url!r} is not a configured link; do not write links")
    if _AMOUNT.search(text):
        problems.append("an amount with a currency was written: use the [[price:id]] placeholder")
    if _LONG_DIGITS.search(text) or _DIGIT_GROUPS.search(text) or _IBAN_LIKE.search(text):
        problems.append(
            "an account-like number was written: payment details come only from a "
            "[[payment:id]] placeholder"
        )
    return problems


def _normalise_url(url: str) -> str:
    return url.rstrip(_TRAILING).rstrip("/")


def configured_links(settings: ConsultativeSalesSettings) -> frozenset[str]:
    """Every link the client configured for free use in a message. Payment and booking links are
    not among them: they are delivered only through their placeholders."""
    urls: set[str] = set()
    if settings.business.website:
        urls.add(settings.business.website)
    if settings.conversation.cta.url:
        urls.add(settings.conversation.cta.url)
    for product in settings.catalog.products:
        if product.url:
            urls.add(product.url)
        if product.strategy and product.strategy.cta and product.strategy.cta.url:
            urls.add(product.strategy.cta.url)
    for workflow in settings.conversion.workflows:
        for step in workflow.steps:
            if step.registration is not None and step.registration.url:
                urls.add(step.registration.url)
    return frozenset(_normalise_url(url) for url in urls)
