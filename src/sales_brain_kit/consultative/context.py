"""The commercial context: everything the Brain works out about a request before asking the model.

It is built from the request and the client's settings alone, with no I/O, and it is immutable:
prompt building and decision validation both read the same context, so what the model was told and
what it is held to can never drift apart.
"""

from dataclasses import dataclass
from datetime import datetime

from sales_brain_kit.consultative.catalog import ProductCatalog
from sales_brain_kit.consultative.facts import KnownFacts, known_facts
from sales_brain_kit.consultative.follow_up import FollowUpPolicy
from sales_brain_kit.consultative.history import HistoryView, view_history
from sales_brain_kit.consultative.interest import derive_interests
from sales_brain_kit.consultative.language import LanguagePolicy, resolve_language
from sales_brain_kit.consultative.selection import ProductSelection, select_products
from sales_brain_kit.consultative.strategy import ConversationPlan, plan_conversation
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_brain_kit.settings.discovery import DiscoveryField
from sales_core.contracts import BrainRequest


@dataclass(frozen=True, slots=True, kw_only=True)
class CommercialContext:
    request: BrainRequest
    settings: ConsultativeSalesSettings
    catalog: ProductCatalog
    facts: KnownFacts
    language: LanguagePolicy
    history: HistoryView
    selection: ProductSelection
    follow_up: FollowUpPolicy
    plan: ConversationPlan

    @property
    def now(self) -> datetime:
        return self.request.now


def _declared_fields(settings: ConsultativeSalesSettings) -> list[DiscoveryField]:
    fields = list(settings.discovery.fields)
    for product in settings.catalog.products:
        if product.discovery is not None:
            fields += product.discovery.extra_fields
    return fields


def build_context(request: BrainRequest, settings: ConsultativeSalesSettings) -> CommercialContext:
    prospect = request.prospect
    hypotheses = settings.discovery.hypotheses
    fields = _declared_fields(settings)
    catalog = ProductCatalog(settings.catalog, settings.communication.languages.default)

    facts = known_facts(
        request.facts,
        attributes=prospect.attributes,
        columns_by_key={f.key: f.source_columns for f in fields if f.source_columns},
        min_hypothesis_confidence=hypotheses.min_confidence_to_use,
        no_hypothesis_for=frozenset(f.key for f in fields if not f.infer.allowed),
    )
    language = resolve_language(facts, prospect.locale, settings.communication.languages)
    interests = derive_interests(
        catalog,
        request.facts,
        prospect.attributes,
        min_hypothesis_confidence=hypotheses.min_confidence_to_use,
    )
    selection = select_products(
        catalog,
        interests,
        language=language.language,
        presentation=settings.catalog.presentation,
    )
    history = view_history(request.conversation)
    follow_up = FollowUpPolicy(settings.follow_up)
    plan = plan_conversation(
        settings,
        request=request,
        history=history,
        facts=facts,
        selection=selection,
        language=language.language,
        follow_up=follow_up,
    )
    return CommercialContext(
        request=request,
        settings=settings,
        catalog=catalog,
        facts=facts,
        language=language,
        history=history,
        selection=selection,
        follow_up=follow_up,
        plan=plan,
    )
