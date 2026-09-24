"""Checks that need the whole document: references between sections, and guardrails.

Every problem is collected, so an author sees all of them at once. Each starts with the path it
concerns. Nothing here is specific to a client or a business.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from pydantic import BaseModel

from sales_brain_kit.settings.common import Localized, duration_seconds
from sales_brain_kit.settings.followup import MIN_GAP_FLOOR_SECONDS, Situation
from sales_brain_kit.settings.strategy import (
    BUILTIN_ADAPTATION_INPUTS,
    PRACTICAL_ONLY_KEYS,
    PROTECTED_ATTRIBUTES,
)

_BUDGET_FIELDS = (
    "max_questions_per_message",
    "max_consecutive_questions",
    "max_questions_per_conversation",
)


def check_references(settings: Any) -> list[str]:
    """All cross-section problems of a structurally valid settings object."""
    problems: list[str] = []
    languages = set(settings.communication.languages.supported)
    products = settings.catalog.products

    _no_duplicates(problems, "objectives.goals", (g.id for g in settings.objectives.goals))
    _no_duplicates(problems, "stages", (s.id for s in settings.stages))
    _no_duplicates(problems, "templates", (t.id for t in settings.templates))
    _no_duplicates(problems, "adaptation.packs", (p.id for p in settings.adaptation.packs))
    _no_duplicates(problems, "catalog.products", (p.id for p in products))

    _check_objectives(problems, settings)
    field_keys = _check_fields(problems, settings)
    _check_product_names(problems, settings)
    _check_languages(problems, settings, languages)
    _check_facts(problems, settings, field_keys)
    _check_conversation(problems, settings)
    _check_adaptation(problems, settings, field_keys)
    _check_budgets(problems, settings)
    _check_follow_up(problems, settings)
    return problems


# ---- identifiers -----------------------------------------------------------------------------


def _no_duplicates(problems: list[str], where: str, ids: Iterable[str]) -> None:
    for value, count in Counter(ids).items():
        if count > 1:
            problems.append(f"{where}: {value!r} is used {count} times; ids must be unique")


def _check_objectives(problems: list[str], settings: Any) -> None:
    primary = settings.objectives.primary
    ids = {g.id for g in settings.objectives.goals}
    if primary is not None and primary not in ids:
        problems.append(f"objectives.primary: {primary!r} is not one of the goals")


def _check_fields(problems: list[str], settings: Any) -> set[str]:
    """Discovery field keys, global and per product, must be unique and safe to collect."""
    declared: list[tuple[str, Any]] = [
        (f"discovery.fields[{i}]", f) for i, f in enumerate(settings.discovery.fields)
    ]
    for p, product in enumerate(settings.catalog.products):
        if product.discovery is not None:
            declared += [
                (f"catalog.products[{p}].discovery.extra_fields[{i}]", f)
                for i, f in enumerate(product.discovery.extra_fields)
            ]
    protected = PROTECTED_ATTRIBUTES | set(settings.adaptation.extra_protected_attributes)
    seen: set[str] = set()
    for where, field in declared:
        if field.key in seen:
            problems.append(f"{where}.key: {field.key!r} is declared twice")
        seen.add(field.key)
        if field.key in protected and not field.sensitive:
            problems.append(
                f"{where}.key: {field.key!r} is a protected attribute; declare it 'sensitive'"
            )
    return seen


def _check_product_names(problems: list[str], settings: Any) -> None:
    """Names, aliases and ids must not point at two products: a Sheet column matches by them."""
    owners: dict[str, set[str]] = {}
    for product in settings.catalog.products:
        display = [product.name] if isinstance(product.name, str) else list(product.name.values())
        for name in (product.id, *product.aliases, *display):
            owners.setdefault(name.casefold(), set()).add(product.id)
    for name, ids in sorted(owners.items()):
        if len(ids) > 1:
            problems.append(
                f"catalog.products: {name!r} identifies several products ({', '.join(sorted(ids))})"
            )
    source = settings.catalog.source_interest
    if source is not None:
        _no_duplicates(
            problems, "catalog.source_interest.columns", (c.casefold() for c in source.columns)
        )


# ---- languages -------------------------------------------------------------------------------


def _check_languages(problems: list[str], settings: Any, languages: set[str]) -> None:
    def unknown(where: str, used: Iterable[str]) -> None:
        for language in sorted(set(used) - languages):
            problems.append(f"{where}: language {language!r} is not in communication.languages")

    for path, text in _localized_texts(settings):
        unknown(path, text)
    for i, template in enumerate(settings.templates):
        unknown(f"templates[{i}].language", [template.language])
    for i, field in enumerate(settings.discovery.fields):
        unknown(f"discovery.fields[{i}].ask_hints", field.ask_hints)
    for p, product in enumerate(settings.catalog.products):
        if product.restrictions is not None:
            unknown(f"catalog.products[{p}].restrictions.languages", product.restrictions.languages)
        if product.discovery is not None:
            for i, field in enumerate(product.discovery.extra_fields):
                unknown(
                    f"catalog.products[{p}].discovery.extra_fields[{i}].ask_hints", field.ask_hints
                )


def _localized_texts(node: Any, path: str = "") -> Iterator[tuple[str, tuple[str, ...]]]:
    """Every {language: text} value in the settings, with its path and the languages it uses."""
    if isinstance(node, Localized):
        yield path, tuple(node)
    elif isinstance(node, BaseModel):
        for name in type(node).model_fields:
            yield from _localized_texts(getattr(node, name), f"{path}.{name}" if path else name)
    elif isinstance(node, tuple | list):
        for index, item in enumerate(node):
            yield from _localized_texts(item, f"{path}[{index}]")
    elif isinstance(node, dict):
        for key, item in node.items():
            yield from _localized_texts(item, f"{path}.{key}")


# ---- facts a document refers to -----------------------------------------------------------------


def _check_facts(problems: list[str], settings: Any, field_keys: set[str]) -> None:
    known = field_keys | {"interest"}
    for step, requirement in settings.discovery.sufficiency.items():
        for key in (*requirement.all_of, *requirement.any_of):
            if key not in known:
                problems.append(
                    f"discovery.sufficiency.{step}: {key!r} is neither a declared field nor 'interest'"
                )
    for p, product in enumerate(settings.catalog.products):
        where = f"catalog.products[{p}]"
        _no_duplicates(
            problems, f"{where}.sales_arguments", (a.id for a in product.sales_arguments)
        )
        _no_duplicates(problems, f"{where}.objections", (o.code for o in product.objections))
        for i, argument in enumerate(product.sales_arguments):
            for key in argument.use_when or {}:
                if key not in field_keys:
                    problems.append(
                        f"{where}.sales_arguments[{i}].use_when: {key!r} is not a declared field"
                    )
        if product.qualification is not None:
            rules = [*product.qualification.fit, *product.qualification.disqualifiers]
            _no_duplicates(problems, f"{where}.qualification", (r.id for r in rules))
            for rule in rules:
                if rule.fact not in field_keys:
                    problems.append(
                        f"{where}.qualification.{rule.id}: {rule.fact!r} is not a declared field"
                    )
    _no_duplicates(
        problems, "conversation.objections", (o.code for o in settings.conversation.objections)
    )


def _check_conversation(problems: list[str], settings: Any) -> None:
    intents = set(settings.conversation.intents)
    for i, shortcut in enumerate(settings.discovery.shortcuts):
        intent = shortcut.when.intent
        if intent is not None and intent not in intents:
            problems.append(
                f"discovery.shortcuts[{i}].when.intent: {intent!r} is not in conversation.intents"
            )
    disqualifiers = {
        rule.id
        for product in settings.catalog.products
        if product.qualification is not None
        for rule in product.qualification.disqualifiers
    }
    for i, trigger in enumerate(settings.handoff.triggers):
        if trigger.intent is not None and trigger.intent not in intents:
            problems.append(
                f"handoff.triggers[{i}].intent: {trigger.intent!r} is not in conversation.intents"
            )
        if trigger.disqualified is not None and trigger.disqualified not in disqualifiers:
            problems.append(
                f"handoff.triggers[{i}].disqualified: {trigger.disqualified!r} is not a disqualifier"
            )


# ---- adaptation: facts, not stereotypes ---------------------------------------------------------


def _check_adaptation(problems: list[str], settings: Any, field_keys: set[str]) -> None:
    adaptation = settings.adaptation
    protected = PROTECTED_ATTRIBUTES | set(adaptation.extra_protected_attributes)
    known = field_keys | BUILTIN_ADAPTATION_INPUTS

    def check_key(where: str, key: str, *, practical: bool) -> None:
        if key in protected:
            problems.append(
                f"{where}: {key!r} is a protected attribute and cannot drive adaptation"
            )
        elif key in PRACTICAL_ONLY_KEYS and not practical:
            problems.append(
                f"{where}: {key!r} may only adjust practical matters (dimension 'practical')"
            )
        elif key not in known:
            problems.append(f"{where}: {key!r} is not a declared field")

    for name, dimension in adaptation.dimensions.items():
        for key in dimension.inputs:
            check_key(f"adaptation.dimensions.{name}.inputs", key, practical=name == "practical")
        if dimension.covers and name != "practical":
            problems.append(f"adaptation.dimensions.{name}.covers: only 'practical' has 'covers'")
    for i, pack in enumerate(adaptation.packs):
        for key in pack.applies_when:
            check_key(f"adaptation.packs[{i}].applies_when", key, practical=False)


# ---- guardrails -------------------------------------------------------------------------------


def _check_budgets(problems: list[str], settings: Any) -> None:
    budget = settings.discovery.budget
    if budget.max_questions_per_message > settings.communication.message.max_questions:
        problems.append(
            "discovery.budget.max_questions_per_message: cannot exceed "
            "communication.message.max_questions"
        )
    if budget.max_consecutive_questions > budget.max_questions_per_conversation:
        problems.append(
            "discovery.budget.max_consecutive_questions: cannot exceed max_questions_per_conversation"
        )
    for p, product in enumerate(settings.catalog.products):
        override = product.discovery.budget if product.discovery else None
        if override is None:
            continue
        for name in _BUDGET_FIELDS:
            value = getattr(override, name)
            if value is not None and value > getattr(budget, name):
                problems.append(
                    f"catalog.products[{p}].discovery.budget.{name}: a product may only tighten "
                    f"the global limit ({getattr(budget, name)})"
                )


def _check_follow_up(problems: list[str], settings: Any) -> None:
    limits = settings.follow_up.limits
    if duration_seconds(limits.min_gap) < MIN_GAP_FLOOR_SECONDS:
        problems.append("follow_up.limits.min_gap: must be at least one hour (PT1H)")
    situations = [("follow_up.situations", settings.follow_up.situations)]
    for p, product in enumerate(settings.catalog.products):
        if product.follow_up is not None:
            situations.append(
                (f"catalog.products[{p}].follow_up.situations", product.follow_up.situations)
            )
    for base, table in situations:
        for name, situation in table.items():
            _check_situation(problems, f"{base}.{name}", name, situation, limits)


def _check_situation(
    problems: list[str], where: str, name: str, situation: Situation, limits: Any
) -> None:
    if situation.use is not None and name != "callback_promised":
        problems.append(f"{where}.use: only 'callback_promised' can use the prospect's preference")
    forward = [s for s in situation.steps if s.after is not None]
    if len(forward) > limits.max_touches:
        problems.append(
            f"{where}.steps: {len(forward)} steps exceed max_touches ({limits.max_touches})"
        )
    minimum = duration_seconds(limits.min_gap)
    for i, step in enumerate(situation.steps):
        if step.before is not None and name != "appointment_reminder":
            problems.append(f"{where}.steps[{i}].before: only 'appointment_reminder' uses 'before'")
        if step.after is not None and duration_seconds(step.after) < minimum:
            problems.append(
                f"{where}.steps[{i}].after: shorter than follow_up.limits.min_gap ({limits.min_gap})"
            )
