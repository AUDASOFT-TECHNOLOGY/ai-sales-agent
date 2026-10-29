"""What looks like an exact value in a text: a link, an e-mail address, an amount with a currency, an
account-like number. A price, a payment detail or a link reaches a prospect only through the catalog and
the payment methods, so the settings refuse them in free text (a lesson) and the Brain strips them from
the material it is shown. One definition, here, because the settings schema may import only itself."""

import re

HTTP = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'\]]+")
BARE_DOMAIN = re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/[^\s<>\"'\]]*")
CURRENCY_SYMBOLS = "$€£¥₹₺₽₩₦₫₪"
AMOUNT = re.compile(
    rf"(?:(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z])\s?\d|\d\s?(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z])"
    rf"|[{CURRENCY_SYMBOLS}]\s?\d|\d\s?[{CURRENCY_SYMBOLS}])"
)
LONG_DIGITS = re.compile(r"\d{9,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A number with its currency (a three-letter code or a symbol), removed whole, whichever side it is on.
AMOUNT_FULL = re.compile(
    rf"(?:(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z])\s?\d[\d  .,]*"
    rf"|\d[\d  .,]*\s?(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z])"
    rf"|[{CURRENCY_SYMBOLS}]\s?\d[\d  .,]*|\d[\d  .,]*\s?[{CURRENCY_SYMBOLS}])"
)
DIGIT_GROUPS = re.compile(r"(?<!\d)\d{3,4}(?:[  ]\d{3,4}){2,}(?!\d)")
IBAN_LIKE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,}\b")


def strip_exact_values(text: str) -> str:
    """The text without anything that looks like an exact value: links, e-mail addresses, amounts
    with a currency, account-like numbers. Material shown to the model for reference goes through
    this, so it can never become a source of prices, links or payment details."""
    cleaned = HTTP.sub("[…]", text)
    cleaned = BARE_DOMAIN.sub("[…]", cleaned)
    cleaned = EMAIL.sub("[…]", cleaned)
    cleaned = IBAN_LIKE.sub("[…]", cleaned)
    cleaned = DIGIT_GROUPS.sub("[…]", cleaned)
    cleaned = LONG_DIGITS.sub("[…]", cleaned)
    # An amount is a number next to a currency: drop the number with its currency, keep the words.
    cleaned = AMOUNT_FULL.sub("[…]", cleaned)
    return cleaned
