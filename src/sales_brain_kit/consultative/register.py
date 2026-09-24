"""The register (formality and style) to write in, from the client's rules and the actual context.

Rules are the client's own (`communication.register_rules`): each names a market and/or a language
and the register that goes with it. The first rule that matches the prospect's known market and the
language in use applies; without one the client's `tone` does. Nothing is assumed from who the
prospect might be: a market counts only when it is known from a trusted source (see `market.py`).
"""

from dataclasses import dataclass

from sales_brain_kit.consultative.language import localize, match_language
from sales_brain_kit.consultative.market import MarketContext
from sales_brain_kit.settings.strategy import CommunicationSettings


@dataclass(frozen=True, slots=True, kw_only=True)
class Register:
    formality: str  # formal, neutral or informal
    allow_switch: bool  # may the Brain move to the prospect's own register?
    style: str  # the client's tone, or the matching rule's
    market_style: str | None = None  # the preferred style of the prospect's market
    rule_id: str | None = None  # which rule decided, for the audit trail


def resolve_register(
    communication: CommunicationSettings, *, language: str, market: MarketContext
) -> Register:
    tone = communication.tone
    default = communication.languages.default
    market_id = market.market.id if market.market else None
    market_style = localize(market.market.style, language, default) if market.market else None
    for rule in communication.register_rules:
        if rule.when.markets and market_id not in rule.when.markets:
            continue
        if rule.when.languages and match_language(language, rule.when.languages) is None:
            continue
        return Register(
            formality=rule.formality or tone.formality.default,
            allow_switch=(
                rule.allow_switch if rule.allow_switch is not None else tone.formality.allow_switch
            ),
            style=rule.style or tone.style,
            market_style=market_style,
            rule_id=rule.id,
        )
    return Register(
        formality=tone.formality.default,
        allow_switch=tone.formality.allow_switch,
        style=tone.style,
        market_style=market_style,
    )
