"""How two contacts are recognised as the same person.

The rule is the one every repository already follows: phone numbers are compared by their digits
(spaces, `+` and dashes ignored), e-mail addresses ignoring case and surrounding blanks. It is
written once here, with no dependency on a provider, so a source, the store and the code that
composes them can never disagree about it. It must stay equal to the Sheets adapter's own
comparison (`sales_integrations.google_sheets.codecs.digits`); a parity test is due with the
contract tests.
"""

from sales_core.contracts import ContactInfo

PHONE = "phone"
EMAIL = "email"
TELEGRAM = "telegram"


def normalize_phone(value: str | None) -> str:
    """The digits of a phone number, or "" if there are none (or no value)."""
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def normalize_email(value: str | None) -> str:
    """The address in a comparable form, or "" if there is none."""
    if not value:
        return ""
    return value.strip().casefold()


def contact_keys(contact: ContactInfo) -> frozenset[str]:
    """Every comparable identity a contact carries, as `phone:<digits>`, `email:<address>` and
    `telegram:<chat id>`.

    Two prospects are the same contact if their key sets meet. A contact with neither a usable
    phone nor an e-mail has no key and is never matched to anything.
    """
    keys: set[str] = set()
    digits = normalize_phone(contact.phone)
    if digits:
        keys.add(f"{PHONE}:{digits}")
    address = normalize_email(contact.email)
    if address:
        keys.add(f"{EMAIL}:{address}")
    chat = (contact.telegram_chat_id or "").strip()
    if chat:
        keys.add(f"{TELEGRAM}:{chat}")
    return frozenset(keys)


def lookup_keys(
    *, phone: str | None = None, email: str | None = None, telegram_chat_id: str | None = None
) -> frozenset[str]:
    """The keys a lookup by phone, e-mail and/or Telegram chat asks for (same form as
    `contact_keys`)."""
    return contact_keys(ContactInfo(phone=phone, email=email, telegram_chat_id=telegram_chat_id))


def same_contact(first: ContactInfo, second: ContactInfo) -> bool:
    return bool(contact_keys(first) & contact_keys(second))
