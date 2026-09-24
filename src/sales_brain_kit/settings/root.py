"""The settings of the "consultative sales" Brain family, as one document.

Only `business` and `communication.languages` are required. Everything else has a safe default, so
a client can start with a catalog and a tone and add strategy as it needs it. The catalog is
dynamic: any number of products, each free to override the global rules (see docs/brain-settings.md).
"""

from pydantic import Field, model_validator

from sales_brain_kit.settings.catalog import CatalogSettings
from sales_brain_kit.settings.common import Model
from sales_brain_kit.settings.conversion import ConversionSettings
from sales_brain_kit.settings.discovery import DiscoverySettings
from sales_brain_kit.settings.followup import FollowUpSettings
from sales_brain_kit.settings.market import MarketSettings
from sales_brain_kit.settings.payment import PaymentSettings
from sales_brain_kit.settings.references import check_references
from sales_brain_kit.settings.strategy import (
    AdaptationSettings,
    BusinessSettings,
    CommunicationSettings,
    ConversationSettings,
    HandoffSettings,
    IncomingSettings,
    Objectives,
    Stage,
    Template,
)


class ConsultativeSalesSettings(Model):
    business: BusinessSettings
    communication: CommunicationSettings
    objectives: Objectives = Field(default_factory=Objectives)
    stages: tuple[Stage, ...] = Field(default=(), max_length=20)
    catalog: CatalogSettings = Field(default_factory=CatalogSettings)
    markets: MarketSettings = Field(default_factory=MarketSettings)
    payment: PaymentSettings = Field(default_factory=PaymentSettings)
    conversion: ConversionSettings = Field(default_factory=ConversionSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    conversation: ConversationSettings = Field(default_factory=ConversationSettings)
    follow_up: FollowUpSettings = Field(default_factory=FollowUpSettings)
    incoming: IncomingSettings = Field(default_factory=IncomingSettings)
    handoff: HandoffSettings = Field(default_factory=HandoffSettings)
    adaptation: AdaptationSettings = Field(default_factory=AdaptationSettings)
    templates: tuple[Template, ...] = Field(default=(), max_length=30)

    @model_validator(mode="after")
    def _references_and_guardrails(self) -> "ConsultativeSalesSettings":
        problems = check_references(self)
        if problems:
            raise ValueError("\n".join(problems))
        return self
