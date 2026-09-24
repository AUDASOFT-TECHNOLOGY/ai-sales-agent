"""Language tags, localised texts and which language a message is written in.

Nothing here names a language. The language comes from what is known about the prospect and from
the client's own configuration, in the order the client configured; where the prospect lives matters
only through a market rule the client wrote for a country that is known from a trusted source.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from sales_brain_kit.consultative.facts import LANGUAGE_KEY, KnownFacts
from sales_brain_kit.consultative.market import MarketContext
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
    PROSPECT_DATA = "prospect_data"  # the language or locale the client holds for the prospect
    MARKET = "market"  # the language rule of the prospect's market
    CLIENT_DEFAULT = "client_default"  # the safe default of the configuration


@dataclass(frozen=True, slots=True, kw_only=True)
class LanguagePolicy:
    """The language to use before the prospect's latest message is read."""

    language: str  # always one of `supported`
    basis: LanguageBasis
    locked: bool  # an explicit preference: the prospect's writing language does not override it
    mirror: bool  # may follow the language the prospect writes in
    supported: tuple[str, ...]
    allowed: tuple[str, ...] = ()  # what mirroring may pick from (the market's, else `supported`)

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
        return match_language(written, self.allowed or self.supported) or self.language


def resolve_language(
    facts: KnownFacts,
    locale: str | None,
    languages: LanguageSettings,
    market: MarketContext | None = None,
) -> LanguagePolicy:
    """Pick the language from the signals the client configured, in the order it configured.

    The signals are an explicit preference the prospect confirmed, what the conversation showed,
    what the client holds for the prospect (a language fact or locale), the language rule of the
    prospect's market, and the client's default. The first signal that yields a supported language
    wins, so conflicting signals are settled by the configured priority and never by chance. A
    market only narrows what may be picked from, except that an explicit request always stands.
    """
    supported = tuple(languages.supported)
    mirror = languages.mirror_prospect_language
    in_market = market.market.languages if market is not None and market.market else None
    pool = (
        tuple(
            language
            for language in supported
            if in_market is None or not in_market.allowed or language in in_market.allowed
        )
        or supported
    )

    known = facts.settled(LANGUAGE_KEY)

    def from_fact(*, conversation: bool) -> str | None:
        if known is None or known.status is FactStatus.CONFIRMED:
            return None
        if (known.source is FactSource.CONVERSATION) is not conversation:
            return None
        return match_language(known.value, pool)

    def signal(name: str) -> tuple[str, LanguageBasis] | None:
        match name:
            case "explicit_preference":
                chosen = (
                    match_language(known.value, supported)
                    if known is not None and known.status is FactStatus.CONFIRMED
                    else None
                )
                return (chosen, LanguageBasis.EXPLICIT_PREFERENCE) if chosen else None
            case "conversation":
                chosen = from_fact(conversation=True)
                return (chosen, LanguageBasis.CONVERSATION) if chosen else None
            case "profile":
                chosen = from_fact(conversation=False) or match_language(locale, pool)
                return (chosen, LanguageBasis.PROSPECT_DATA) if chosen else None
            case "market":
                chosen = (
                    match_language(in_market.default, pool)
                    if in_market and in_market.default
                    else None
                )
                return (chosen, LanguageBasis.MARKET) if chosen else None
            case _:
                return None

    for name in languages.priority:
        found = signal(name)
        if found is not None:
            language, basis = found
            locked = basis is LanguageBasis.EXPLICIT_PREFERENCE
            return LanguagePolicy(
                language=language,
                basis=basis,
                locked=locked,
                mirror=mirror and not locked,
                supported=supported,
                allowed=pool,
            )
    return LanguagePolicy(
        language=languages.default,
        basis=LanguageBasis.CLIENT_DEFAULT,
        locked=False,
        mirror=mirror,
        supported=supported,
        allowed=pool,
    )
