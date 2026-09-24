"""Payment: how a client gets paid, by product, market, country and currency.

Everything here is *exact data*. A payment URL, an account number, a bank name or the wording of an
instruction is written once by the client and handed to the prospect exactly as written: the model
never receives these values and never types them. It points at a method by id and deterministic
code inserts the configured text (see `consultative/exact.py`).
"""

from typing import Literal

from pydantic import Field, model_validator

from sales_brain_kit.settings.common import (
    HttpsUrl,
    LocalizedExact,
    LocalizedExactShort,
    LocalizedShort,
    LocalizedText,
    Model,
    ShortText,
    Slug,
)
from sales_brain_kit.settings.scope import Applicability

MethodKind = Literal["online", "offline", "other"]
ConfirmationMode = Literal["provider", "manual", "none"]


class PaymentField(Model):
    """One line of payment detail: a label and the value, kept exactly."""

    label: LocalizedShort
    value: LocalizedExactShort


class PaymentInstruction(Model):
    """What to tell the prospect for the scope it applies to. The most specific match is used."""

    id: Slug
    applies_to: Applicability = Field(default_factory=Applicability)
    text: LocalizedExact | None = None  # exact wording, in one or several languages
    fields: tuple[PaymentField, ...] = Field(default=(), max_length=20)
    url: HttpsUrl | None = None  # a payment page, for online methods

    @model_validator(mode="after")
    def _says_something(self) -> "PaymentInstruction":
        if self.text is None and not self.fields and self.url is None:
            raise ValueError("give at least one of 'text', 'fields' or 'url'")
        return self


class Confirmation(Model):
    """How a payment becomes confirmed: by the provider, by a person, or not tracked."""

    mode: ConfirmationMode = "manual"
    note: LocalizedText | None = None  # what the prospect is told to expect or do


class PaymentMethod(Model):
    id: Slug
    status: Literal["active", "inactive"] = "active"
    label: LocalizedShort
    kind: MethodKind = "offline"
    provider: ShortText | None = None  # reference metadata, never shown to the prospect
    applies_to: Applicability = Field(default_factory=Applicability)
    instructions: tuple[PaymentInstruction, ...] = Field(min_length=1, max_length=40)
    confirmation: Confirmation = Field(default_factory=Confirmation)
    metadata: dict[Slug, ShortText] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _kind_needs_its_data(self) -> "PaymentMethod":
        if self.kind == "online" and not any(i.url for i in self.instructions):
            raise ValueError("an 'online' method needs an instruction with a 'url'")
        if self.kind == "offline" and not any(i.text or i.fields for i in self.instructions):
            raise ValueError("an 'offline' method needs an instruction with 'text' or 'fields'")
        if len(self.metadata) > 10:
            raise ValueError("at most 10 metadata entries")
        return self


class PaymentSettings(Model):
    methods: tuple[PaymentMethod, ...] = Field(default=(), max_length=30)
