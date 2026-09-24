"""Language tags, localised texts and which language a message is written in.

Nothing here names a language. The language comes from what is known about the prospect and from
the client's own configuration, never from where the prospect lives.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from sales_brain_kit.consultative.facts import LANGUAGE_KEY, KnownFacts
from sales_brain_kit.settings.strategy import LanguageSettings
from sales_core.contracts import FactSource, FactStatus


def match_language(tag: str | None, candidates: Iterable[str]) -> str | None:
    """The candidate a tag stands for: an exact match, else one sharing the primary language."""
    if tag is None or not tag.strip():
        return None
    options = {candidate.casefold(): candidate for candidate in candidates}
    wanted = tag.strip().replace("_", "-").casefold()
    if wanted in options:
        return options[wanted]
    primary = wanted.split("-")[0]
    if primary in options:
        return options[primary]
    return next((c for key, c in options.items() if key.split("-")[0] == primary), None)


def localize(
    value: str | Mapping[str, str] | None, language: str | None, default: str | None = None
) -> str | None:
    """Plain text as is; a {language: text} map in `language`, then `default`, then any."""
    if value is None or isinstance(value, str):
        return value
    for tag in (language, default):
        matched = match_language(tag, value)
        if matched is not None:
            return value[matched]
    return next(iter(value.values()), None)


class LanguageBasis(StrEnum):
    EXPLICIT_PREFERENCE = "explicit_preference"  # a confirmed statement of the prospect
    CONVERSATION = "conversation"  # learned from the exchange
    PROSPECT_DATA = "prospect_data"  # the locale the client holds for the prospect
    CLIENT_DEFAULT = "client_default"  # the safe default of the configuration


@dataclass(frozen=True, slots=True, kw_only=True)
class LanguagePolicy:
    """The language to use before the prospect's latest message is read."""

    language: str  # always one of `supported`
    basis: LanguageBasis
    locked: bool  # an explicit preference: the prospect's writing language does not override it
    mirror: bool  # may follow the language the prospect writes in
    supported: tuple[str, ...]

    def expected(self, written: str | None = None, requested: str | None = None) -> str:
        """The language of the reply once the latest message is known.

        A language the prospect just asked for wins; then a locked preference; then, when the
        client lets the Brain mirror, the language the prospect wrote in; then the policy's own.
        Languages the client does not support are never used.
        """
        asked = match_language(requested, self.supported)
        if asked is not None:
            return asked
        if self.locked or not self.mirror:
            return self.language
        return match_language(written, self.supported) or self.language


def resolve_language(
    facts: KnownFacts, locale: str | None, languages: LanguageSettings
) -> LanguagePolicy:
    """Explicit preference, then conversation evidence, then the prospect's locale, then default."""
    supported = tuple(languages.supported)
    mirror = languages.mirror_prospect_language

    known = facts.settled(LANGUAGE_KEY)
    fact_language = match_language(known.value, supported) if known else None
    if known is not None and fact_language is not None:
        if known.status is FactStatus.CONFIRMED:
            return LanguagePolicy(
                language=fact_language,
                basis=LanguageBasis.EXPLICIT_PREFERENCE,
                locked=True,
                mirror=False,
                supported=supported,
            )
        basis = (
            LanguageBasis.CONVERSATION
            if known.source is FactSource.CONVERSATION
            else LanguageBasis.PROSPECT_DATA
        )
        return LanguagePolicy(
            language=fact_language, basis=basis, locked=False, mirror=mirror, supported=supported
        )
    from_locale = match_language(locale, supported)
    if from_locale is not None:
        return LanguagePolicy(
            language=from_locale,
            basis=LanguageBasis.PROSPECT_DATA,
            locked=False,
            mirror=mirror,
            supported=supported,
        )
    return LanguagePolicy(
        language=languages.default,
        basis=LanguageBasis.CLIENT_DEFAULT,
        locked=False,
        mirror=mirror,
        supported=supported,
    )
