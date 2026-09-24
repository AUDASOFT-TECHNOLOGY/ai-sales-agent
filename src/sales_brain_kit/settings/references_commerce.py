"""Cross-checks for markets, pricing, payment, conversion workflows and handoff availability.

Like `references.py` it collects every problem, each starting with the path it concerns, and it
knows no client: everything checked is what the client itself declared.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Any

from sales_brain_kit.settings.conversion import (
    FALLBACK_KEYWORDS,
    PAYMENT_KINDS,
    Workflow,
    WorkflowStep,
)
from sales_brain_kit.settings.strategy import PROTECTED_ATTRIBUTES


def check_commerce(problems: list[str], settings: Any, field_keys: set[str]) -> None:
    _check_markets(problems, settings)
    _check_register_rules(problems, settings)
    _check_pricing(problems, settings)
    _check_payment(problems, settings)
    _check_conversion(problems, settings, field_keys)
    _check_handoff_availability(problems, settings)
    _check_reachable_discovery(problems, settings)


def _duplicates(problems: list[str], where: str, ids: Iterable[str]) -> None:
    for value, count in Counter(ids).items():
        if count > 1:
            problems.append(f"{where}: {value!r} is used {count} times; ids must be unique")


def _product_ids(settings: Any) -> set[str]:
    return {product.id for product in settings.catalog.products}


def _market_ids(settings: Any) -> set[str]:
    return {market.id for market in settings.markets.markets}


def _scope_keys(scope: Any) -> tuple[frozenset[str], ...]:
    return (
        frozenset(scope.markets),
        frozenset(c.casefold() for c in scope.countries),
        frozenset(getattr(scope, "products", ())),
        frozenset(getattr(scope, "currencies", ())),
    )


# ---- markets and the country they rest on --------------------------------------------------------


def _uses_place(settings: Any) -> bool:
    if settings.markets.markets:
        return True
    if any(rule.when.markets for rule in settings.communication.register_rules):
        return True
    for product in settings.catalog.products:
        if product.pricing is not None and product.pricing.variants:
            return True
    for method in settings.payment.methods:
        if method.applies_to.restricts_place or any(
            i.applies_to.restricts_place for i in method.instructions
        ):
            return True
    return False


def _check_markets(problems: list[str], settings: Any) -> None:
    markets = settings.markets
    products, languages = _product_ids(settings), set(settings.communication.languages.supported)
    workflows = {w.id for w in settings.conversion.workflows}
    _duplicates(problems, "markets.markets", (m.id for m in markets.markets))
    _duplicates(problems, "markets.trusted_sources", markets.trusted_sources)

    seen: dict[str, str] = {}
    for m, market in enumerate(markets.markets):
        where = f"markets.markets[{m}]"
        for country in market.countries:
            key = country.strip().casefold()
            if key in seen and seen[key] != market.id:
                problems.append(
                    f"{where}.countries: {country!r} is already in market {seen[key]!r}; "
                    "a country belongs to one market"
                )
            seen[key] = market.id
        if market.languages is not None:
            used = [
                *market.languages.allowed,
                *([market.languages.default] * bool(market.languages.default)),
            ]
            for language in sorted(set(used) - languages):
                problems.append(
                    f"{where}.languages: language {language!r} is not in communication.languages"
                )
        if market.catalog is not None:
            for name, ids in (("only", market.catalog.only), ("exclude", market.catalog.exclude)):
                for product_id in ids:
                    if product_id not in products:
                        problems.append(f"{where}.catalog.{name}: {product_id!r} is not a product")
        if market.workflow is not None and market.workflow not in workflows:
            problems.append(f"{where}.workflow: {market.workflow!r} is not a workflow")

    if not _uses_place(settings):
        return
    fact = markets.country_fact
    declared = {f.key: f for f in settings.discovery.fields}
    protected = PROTECTED_ATTRIBUTES | set(settings.adaptation.extra_protected_attributes)
    if fact in protected:
        problems.append(
            f"markets.country_fact: {fact!r} is a protected attribute; a market rests on the "
            "country of residence, never on nationality or similar"
        )
    elif fact not in declared:
        problems.append(
            f"markets.country_fact: {fact!r} is not a declared discovery field "
            "(markets, prices or payment by place need to know the country)"
        )
    elif declared[fact].infer.allowed:
        problems.append(
            f"discovery.fields.{fact}.infer.allowed: a country that drives markets is never "
            "guessed: set infer.allowed to false"
        )


def _check_register_rules(problems: list[str], settings: Any) -> None:
    rules = settings.communication.register_rules
    _duplicates(problems, "communication.register_rules", (r.id for r in rules))
    markets, languages = _market_ids(settings), set(settings.communication.languages.supported)
    for i, rule in enumerate(rules):
        for market in rule.when.markets:
            if market not in markets:
                problems.append(
                    f"communication.register_rules[{i}].when.markets: {market!r} is not a market"
                )
        for language in sorted(set(rule.when.languages) - languages):
            problems.append(
                f"communication.register_rules[{i}].when.languages: "
                f"language {language!r} is not in communication.languages"
            )


# ---- prices --------------------------------------------------------------------------------------


def _check_pricing(problems: list[str], settings: Any) -> None:
    markets = _market_ids(settings)
    for p, product in enumerate(settings.catalog.products):
        pricing = product.pricing
        if pricing is None or not pricing.variants:
            continue
        where = f"catalog.products[{p}].pricing.variants"
        _duplicates(problems, where, (v.id for v in pricing.variants))
        scopes = Counter(_scope_keys(v.applies_to) for v in pricing.variants)
        for v, variant in enumerate(pricing.variants):
            for market in variant.applies_to.markets:
                if market not in markets:
                    problems.append(f"{where}[{v}].applies_to.markets: {market!r} is not a market")
            if scopes[_scope_keys(variant.applies_to)] > 1:
                problems.append(
                    f"{where}[{v}].applies_to: the same place is priced twice; "
                    "give one variant per place"
                )


# ---- payment -------------------------------------------------------------------------------------


def _check_payment(problems: list[str], settings: Any) -> None:
    products, markets = _product_ids(settings), _market_ids(settings)
    _duplicates(problems, "payment.methods", (m.id for m in settings.payment.methods))

    def check_scope(where: str, scope: Any) -> None:
        for product_id in scope.products:
            if product_id not in products:
                problems.append(f"{where}.products: {product_id!r} is not a product")
        for market in scope.markets:
            if market not in markets:
                problems.append(f"{where}.markets: {market!r} is not a market")

    for m, method in enumerate(settings.payment.methods):
        where = f"payment.methods[{m}]"
        check_scope(f"{where}.applies_to", method.applies_to)
        _duplicates(problems, f"{where}.instructions", (i.id for i in method.instructions))
        scopes = Counter(_scope_keys(i.applies_to) for i in method.instructions)
        for i, instruction in enumerate(method.instructions):
            check_scope(f"{where}.instructions[{i}].applies_to", instruction.applies_to)
            if scopes[_scope_keys(instruction.applies_to)] > 1:
                problems.append(
                    f"{where}.instructions[{i}].applies_to: two instructions apply to the same "
                    "scope, so which one is used would be arbitrary"
                )


# ---- conversion workflows ------------------------------------------------------------------------


def _check_conversion(problems: list[str], settings: Any, field_keys: set[str]) -> None:
    conversion = settings.conversion
    workflows = {w.id for w in conversion.workflows}
    _duplicates(problems, "conversion.workflows", (w.id for w in conversion.workflows))
    if conversion.default_workflow is not None and conversion.default_workflow not in workflows:
        problems.append(
            f"conversion.default_workflow: {conversion.default_workflow!r} is not a workflow"
        )
    for p, product in enumerate(settings.catalog.products):
        if product.workflow is not None and product.workflow not in workflows:
            problems.append(
                f"catalog.products[{p}].workflow: {product.workflow!r} is not a workflow"
            )
    if conversion.appointment is not None and not conversion.appointment.supported:
        problems.append("conversion.appointment: a default appointment must be 'supported'")
    for w, workflow in enumerate(conversion.workflows):
        _check_workflow(problems, settings, f"conversion.workflows[{w}]", workflow, field_keys)


def _successor(workflow: Workflow, index: int) -> str | None:
    step = workflow.steps[index]
    if step.next is not None:
        return step.next
    return workflow.steps[index + 1].id if index + 1 < len(workflow.steps) else None


def _check_workflow(
    problems: list[str], settings: Any, where: str, workflow: Workflow, field_keys: set[str]
) -> None:
    ids = [step.id for step in workflow.steps]
    _duplicates(problems, f"{where}.steps", ids)
    known = set(ids)
    for s, step in enumerate(workflow.steps):
        _check_step(problems, settings, f"{where}.steps[{s}]", step, known, field_keys)

    # The path from the first step must end: `next` may not lead back to a step already visited.
    by_id = {step.id: i for i, step in enumerate(workflow.steps)}
    seen: set[str] = set()
    current: str | None = ids[0]
    while current is not None and current in by_id:
        if current in seen:
            problems.append(f"{where}.steps: the steps loop back to {current!r}")
            break
        seen.add(current)
        current = _successor(workflow, by_id[current])


def _check_step(
    problems: list[str],
    settings: Any,
    where: str,
    step: WorkflowStep,
    step_ids: set[str],
    field_keys: set[str],
) -> None:
    handoff_enabled = settings.handoff.enabled
    stages = {stage.id for stage in settings.stages}

    if step.id in FALLBACK_KEYWORDS:
        problems.append(f"{where}.id: {step.id!r} is reserved (it is a fallback keyword)")
    if step.next is not None and step.next not in step_ids:
        problems.append(f"{where}.next: {step.next!r} is not a step of this workflow")
    if step.fallback is not None:
        if step.fallback == step.id:
            problems.append(f"{where}.fallback: a step cannot fall back to itself")
        elif step.fallback not in step_ids and step.fallback not in FALLBACK_KEYWORDS:
            problems.append(
                f"{where}.fallback: {step.fallback!r} is neither a step nor one of "
                f"{', '.join(FALLBACK_KEYWORDS)}"
            )
    if step.requires is not None:
        for other in step.requires.steps:
            if other not in step_ids:
                problems.append(f"{where}.requires.steps: {other!r} is not a step of this workflow")
            elif other == step.id:
                problems.append(f"{where}.requires.steps: a step cannot require itself")
        for key in step.requires.facts:
            if key not in field_keys:
                problems.append(f"{where}.requires.facts: {key!r} is not a declared field")
    if step.completes_when is not None:
        for c, condition in enumerate(step.completes_when.facts):
            if condition.key not in field_keys:
                problems.append(
                    f"{where}.completes_when.facts[{c}].key: {condition.key!r} is not a declared field"
                )
        if step.completes_when.stage is not None and step.completes_when.stage not in stages:
            problems.append(
                f"{where}.completes_when.stage: {step.completes_when.stage!r} is not a stage"
            )
    if step.stage is not None and step.stage not in stages:
        problems.append(f"{where}.stage: {step.stage!r} is not a stage")
    if step.registration is not None:
        for key in step.registration.collects:
            if key not in field_keys:
                problems.append(f"{where}.registration.collects: {key!r} is not a declared field")
    if step.kind in PAYMENT_KINDS:
        _check_payment_step(problems, settings, where, step)
    if step.kind in ("appointment", "callback"):
        _check_appointment_step(problems, settings, where, step)
    if step.effective_human == "required" and not handoff_enabled:
        problems.append(f"{where}.human: a person is required but handoff.enabled is false")


def _check_payment_step(problems: list[str], settings: Any, where: str, step: WorkflowStep) -> None:
    methods = {m.id: m for m in settings.payment.methods}
    if not any(m.status == "active" for m in methods.values()):
        problems.append(f"{where}: a {step.kind} step needs at least one active payment method")
    for method_id in step.payment.methods if step.payment else ():
        if method_id not in methods:
            problems.append(f"{where}.payment.methods: {method_id!r} is not a payment method")
        elif methods[method_id].status != "active":
            problems.append(f"{where}.payment.methods: {method_id!r} is not active")


def _check_appointment_step(
    problems: list[str], settings: Any, where: str, step: WorkflowStep
) -> None:
    own, default = step.appointment, settings.conversion.appointment
    if own is not None and not own.supported:
        problems.append(f"{where}.appointment: must be 'supported'")
    available = (
        own is not None
        or default is not None
        or any(
            p.appointment is not None and p.appointment.supported for p in settings.catalog.products
        )
    )
    if not available:
        problems.append(
            f"{where}: no appointment is configured (on the step, in conversion.appointment "
            "or on a product)"
        )


# ---- handoff can be switched off ------------------------------------------------------------------


def _check_handoff_availability(problems: list[str], settings: Any) -> None:
    if settings.handoff.enabled:
        return

    def conflict(where: str) -> None:
        problems.append(f"{where}: hands over to a person but handoff.enabled is false")

    if settings.handoff.triggers:
        conflict("handoff.triggers")
    for intent, behaviour in settings.conversation.intents.items():
        if behaviour == "handoff":
            conflict(f"conversation.intents.{intent}")
    if settings.conversation.media_handling.voice_note == "handoff":
        conflict("conversation.media_handling.voice_note")
    if settings.incoming.out_of_scope.then == "handoff":
        conflict("incoming.out_of_scope.then")
    for o, objection in enumerate(settings.conversation.objections):
        if objection.then == "handoff":
            conflict(f"conversation.objections[{o}].then")
    tables = [("follow_up.situations", settings.follow_up.situations)]
    for p, product in enumerate(settings.catalog.products):
        where = f"catalog.products[{p}]"
        for o, objection in enumerate(product.objections):
            if objection.then == "handoff":
                conflict(f"{where}.objections[{o}].then")
        if product.qualification is not None:
            for rule in product.qualification.disqualifiers:
                if rule.effect == "handoff":
                    conflict(f"{where}.qualification.{rule.id}")
        if product.follow_up is not None:
            tables.append((f"{where}.follow_up.situations", product.follow_up.situations))
        if product.appointment is not None and product.appointment.mode == "human_scheduling":
            conflict(f"{where}.appointment.mode")
    for m, market in enumerate(settings.markets.markets):
        if market.follow_up is not None:
            tables.append(
                (f"markets.markets[{m}].follow_up.situations", market.follow_up.situations)
            )
    for base, table in tables:
        for name, situation in table.items():
            if situation.then == "handoff":
                conflict(f"{base}.{name}.then")
    conversion = settings.conversion
    if conversion.appointment is not None and conversion.appointment.mode == "human_scheduling":
        conflict("conversion.appointment.mode")
    for w, workflow in enumerate(conversion.workflows):
        for s, step in enumerate(workflow.steps):
            where = f"conversion.workflows[{w}].steps[{s}]"
            if step.kind == "handoff" or step.fallback == "handoff":
                conflict(where)
            if step.appointment is not None and step.appointment.mode == "human_scheduling":
                conflict(f"{where}.appointment.mode")
            if step.registration is not None and step.registration.mode == "human_assisted":
                conflict(f"{where}.registration.mode")


# ---- a requirement must be satisfiable -----------------------------------------------------------


def _check_reachable_discovery(problems: list[str], settings: Any) -> None:
    """A step cannot require what can never be learned.

    When no question is allowed at all, a fact can only come from the lead's own columns (or the
    prospect volunteering it, which cannot be relied on), so a requirement resting on any other
    fact could never be met.
    """
    budget = settings.discovery.budget
    silent = 0 in (
        budget.max_questions_per_message,
        budget.max_questions_per_conversation,
        settings.communication.message.max_questions,
    )
    if not silent:
        return
    sourced = {f.key for f in settings.discovery.fields if f.source_columns}
    for step, requirement in settings.discovery.sufficiency.items():
        for key in (*requirement.all_of, *requirement.any_of):
            if key != "interest" and key not in sourced:
                problems.append(
                    f"discovery.sufficiency.{step}: {key!r} can never be learned because no "
                    "question is allowed and the field has no source column"
                )
