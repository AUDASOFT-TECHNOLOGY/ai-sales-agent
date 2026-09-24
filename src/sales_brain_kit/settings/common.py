"""Building blocks shared by every part of the settings schema.

Everything here is generic: formats, size limits and the base model. Unknown keys are rejected
everywhere, so a typo fails loudly instead of being ignored.
"""

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _no_control_characters(value: str) -> str:
    for character in value:
        if unicodedata.category(character) == "Cc" and character not in "\n\t":
            raise ValueError("must not contain control characters")
    return value


def _text(max_length: int) -> object:
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length),
        AfterValidator(_no_control_characters),
    ]


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _exact(max_length: int) -> object:
    """Text that is data, not prose: kept exactly as written (no trimming, no reformatting).
    Payment details and any other value that must never be altered use it."""
    return Annotated[
        str,
        StringConstraints(min_length=1, max_length=max_length),
        AfterValidator(_not_blank),
        AfterValidator(_no_control_characters),
    ]


Hint = _text(200)
ShortText = _text(300)
Text = _text(3000)
ExactShort = _exact(200)
ExactText = _exact(1500)

# Identifiers chosen by the client (product ids, fact keys, stage ids, ...). Stable and URL-safe.
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
# A language tag such as "fr", "ar" or "ar-TN".
Lang = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
HttpsUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https://[^\s/$.?#][^\s]*$")]


class Localized(dict[str, str]):
    """Text in several languages. Its own type so the schema can check the languages it uses."""


def _localize(value: object) -> object:
    if isinstance(value, dict):
        if not value:
            raise ValueError("must give text in at least one language")
        return Localized(value)
    return value


# Plain text (in the client's default language) or a {language: text} map.
LocalizedShort = Annotated[ShortText | dict[Lang, ShortText], AfterValidator(_localize)]  # type: ignore[operator]
LocalizedText = Annotated[Text | dict[Lang, Text], AfterValidator(_localize)]  # type: ignore[operator]
LocalizedExact = Annotated[ExactText | dict[Lang, ExactText], AfterValidator(_localize)]  # type: ignore[operator]
LocalizedExactShort = Annotated[ExactShort | dict[Lang, ExactShort], AfterValidator(_localize)]  # type: ignore[operator]

_DURATION = re.compile(r"^P(?=\d|T\d)(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def duration_seconds(value: str) -> int:
    """Length of an ISO-8601 duration limited to weeks, days, hours and minutes ("P2D", "PT2H")."""
    match = _DURATION.match(value)
    if match is None:
        raise ValueError(f"{value!r} is not a duration such as 'P2D' or 'PT6H'")
    weeks, days, hours, minutes = (int(part or 0) for part in match.groups())
    return (((weeks * 7 + days) * 24 + hours) * 60 + minutes) * 60


def _check_duration(value: str) -> str:
    if duration_seconds(value) <= 0:
        raise ValueError("must be longer than zero")
    return value


Duration = Annotated[str, AfterValidator(_check_duration)]
