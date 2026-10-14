"""A client's lanes: its sources, the channel each one is written to on, and the products offered.

A client that configured nothing has **one implicit lane**, `default`, made of the source and channel
it has always had and every product of its catalog: `effective_lanes` builds it, so the rest of the
application can ask for "the lanes" without caring whether the client chose any. A client that lists
`sources` and `lanes` gets exactly those, and `lane_problems` says, in words, what is wrong with
them. Neither function runs anything or reads a secret.

Rules (the structural ones, such as unique ids and "a source belongs to one lane", are the schema's):

- a lane lists at least one product, each of which must exist in the Brain's catalog, or says
  `ALL` (the whole catalog as it is when the Brain is asked, so new products are included);
- every provider named is known, plays the role it is given, and its settings are acceptable;
- a channel is judged against the channels the Admin allows the client, when they are known;
- at most one source instance per provider that needs a secret of the client's own (two HubSpot
  accounts would need two tokens, which the credential store does not hold yet).

The id of a source instance is the prefix of its prospects' ids. The implicit lane of a client on
Google Sheets keeps the ids it has always had, with no prefix: see `keeps_native_ids`.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from sales_app.config.schema import ClientFile, LaneSection, SourceInstance
from sales_app.secret_store import ScopeKind
from sales_app.wiring.providers import ProviderKind, ProviderRegistry, default_registry
from sales_app.wiring.providers.types import Requirement
from sales_core.contracts import ALL_PRODUCTS

DEFAULT_LANE = "default"


@dataclass(frozen=True, slots=True)
class EffectiveLanes:
    """The sources and lanes a client runs, whether it chose them or not."""

    sources: tuple[SourceInstance, ...]
    lanes: tuple[LaneSection, ...]
    explicit: bool  # False: synthesised from the client's single source and channel
    default_lane: str | None  # the lane unattributed prospects fall into; None: no such lane
    keeps_native_ids: frozenset[str]  # source ids whose prospects have no prefix (legacy Sheets)

    def lane(self, lane_id: str) -> LaneSection | None:
        return next((lane for lane in self.lanes if lane.id == lane_id), None)

    def source(self, source_id: str) -> SourceInstance | None:
        return next((s for s in self.sources if s.id == source_id), None)


def effective_lanes(spec: ClientFile) -> EffectiveLanes:
    """The client's lanes. Without any configured, the one implicit `default` lane (no products
    listed: the whole catalog), so an existing client is unchanged."""
    if spec.sources is not None and spec.lanes is not None:
        return EffectiveLanes(
            sources=spec.sources,
            lanes=spec.lanes,
            explicit=True,
            default_lane=None,
            keeps_native_ids=frozenset(),
        )
    provider = spec.source_provider
    source = SourceInstance(id=provider, provider=provider, settings=spec.source_settings)
    lane = LaneSection.model_construct(  # no product listed: the lane offers the whole catalog
        id=DEFAULT_LANE, source=source.id, channel=spec.channel_provider, products=()
    )
    return EffectiveLanes(
        sources=(source,),
        lanes=(lane,),
        explicit=False,
        default_lane=DEFAULT_LANE,
        keeps_native_ids=frozenset() if spec.source_apart_from_store else frozenset({source.id}),
    )


def catalog_product_ids(brain_settings: Mapping[str, Any]) -> frozenset[str]:
    """The ids of the products in a Brain's settings (empty if it has no catalog)."""
    catalog = brain_settings.get("catalog")
    products = catalog.get("products") if isinstance(catalog, Mapping) else None
    if not isinstance(products, list | tuple):
        return frozenset()
    return frozenset(
        str(p["id"]) for p in products if isinstance(p, Mapping) and isinstance(p.get("id"), str)
    )


def lane_problems(
    spec: ClientFile,
    *,
    registry: ProviderRegistry | None = None,
    allowed_channels: Collection[str] | None = None,
) -> list[str]:
    """Everything wrong with the client's lanes, in words. Empty for a client with none configured
    (its implicit lane is its legacy configuration, judged by the rest of the validation).

    `allowed_channels`: the channels the Admin lets this client use; None when not known here (the
    runtime judges it again every time the configuration is resolved)."""
    if spec.sources is None or spec.lanes is None:
        return []
    registry = registry if registry is not None else default_registry()
    problems: list[str] = []

    for index, source in enumerate(spec.sources):
        path = f"sources[{index}]"
        descriptor = registry.get(source.provider)
        if descriptor is None:
            problems.append(f"{path}.provider: unknown provider {source.provider!r}")
            continue
        if not descriptor.plays(ProviderKind.SOURCE):
            problems.append(f"{path}.provider: {source.provider!r} cannot be used as a source")
            continue
        model = descriptor.settings_models.get(ProviderKind.SOURCE)
        if model is not None and source.settings:
            try:
                model.model_validate(source.settings)
            except ValidationError as error:
                problems.extend(
                    f"{path}.settings.{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                    for e in error.errors()
                )

    problems.extend(_one_account_per_provider(spec, registry))

    known = catalog_product_ids(spec.brain.settings)
    for index, lane in enumerate(spec.lanes):
        path = f"lanes[{index}]"
        channel = registry.get(lane.channel)
        if channel is None:
            problems.append(f"{path}.channel: unknown channel {lane.channel!r}")
        elif not channel.plays(ProviderKind.CHANNEL):
            problems.append(f"{path}.channel: {lane.channel!r} is not a channel")
        elif allowed_channels is not None and lane.channel not in allowed_channels:
            problems.append(f"{path}.channel: the {lane.channel} channel is not authorized")
        problems.extend(
            f"{path}.products: {product!r} is not a product of the catalog"
            for product in lane.products
            if product != ALL_PRODUCTS and product not in known
        )
    return problems


def _one_account_per_provider(spec: ClientFile, registry: ProviderRegistry) -> list[str]:
    """A provider whose credential belongs to the client holds one account per client."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for source in spec.sources or ():
        descriptor = registry.get(source.provider)
        if descriptor is None:
            continue
        needs_own_secret = any(
            c.scope is ScopeKind.CLIENT and c.required_when is Requirement.ALWAYS
            for c in descriptor.credentials
        )
        if not needs_own_secret:
            continue
        if source.provider in seen:
            problems.append(
                f"sources: {source.id!r} and {seen[source.provider]!r} are both "
                f"{descriptor.label} accounts; one account per client is supported"
            )
        seen.setdefault(source.provider, source.id)
    return problems
