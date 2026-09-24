"""Is a client's configuration complete enough to talk to real prospects?

Validity is a different question: a configuration with an empty catalog is valid and the Brain runs
on it (it opens conversations and hands over what it cannot answer). Readiness lists what a
business user still has to provide before a client is switched on, so an Admin can show it as a
checklist, whatever the client. Nothing here blocks anything; `enabled` in the client file remains
the switch.

Unfinished text is recognised by its marker: any text containing `TODO(` and any link on the
reserved `.invalid` domain (see `docs/templates/product.template.yaml`).
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from sales_brain_kit.settings import ConsultativeSalesSettings

PLACEHOLDER_MARKER = "TODO("  # what an unfinished text says
PLACEHOLDER_HOST = ".invalid"  # a reserved top-level domain: a URL nobody has filled in


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing


def check_readiness(settings: ConsultativeSalesSettings) -> ReadinessReport:
    missing: list[str] = []
    products = [p for p in settings.catalog.products if p.status != "inactive"]
    if not any(p.status == "active" for p in products):
        missing.append("catalog: no active product; there is nothing to offer")
    for product in products:
        missing.extend(_incomplete(product))
    if settings.handoff.enabled and settings.handoff.message is None:
        missing.append("handoff.message: not set; the prospect is not told a person will follow up")
    missing.extend(f"{path}: still a placeholder" for path in _placeholders(settings))
    return ReadinessReport(tuple(missing))


def _incomplete(product: Any) -> Iterator[str]:
    where = f"catalog.products[{product.id}]"
    if not product.description:
        yield f"{where}.description: not set"
    if product.pricing is None:
        yield f"{where}.pricing: not set (use model 'on_request' if the price is not shared)"
    if not product.url:
        yield f"{where}.url: not set"
    if product.audience is None or not product.audience.suited_for:
        yield f"{where}.audience.suited_for: not set; the Brain cannot tell who it is for"
    if product.value is None or not product.value.benefits:
        yield f"{where}.value.benefits: not set"
    if not product.sales_arguments:
        yield f"{where}.sales_arguments: none"
    if product.strategy is None or product.strategy.cta is None:
        yield f"{where}.strategy.cta: not set (the global call to action applies)"


def _placeholders(settings: ConsultativeSalesSettings) -> list[str]:
    return sorted(_walk(settings.model_dump(mode="json"), ""))


def _walk(node: Any, path: str) -> Iterator[str]:
    if isinstance(node, str):
        if PLACEHOLDER_MARKER in node or PLACEHOLDER_HOST in node:
            yield path
    elif isinstance(node, Mapping):
        for key, item in node.items():
            yield from _walk(item, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]")
