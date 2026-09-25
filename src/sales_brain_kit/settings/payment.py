"""Payment: how a client gets paid, by product, market, country and currency.

Everything here is *exact data*. A payment URL, an account number, a bank name or the wording of an
instruction is written once by the client and handed to the prospect exactly as written: the model
never receives these values and never types them. It points at a method by id and deterministic
code inserts the configured text (see `consultative/exact.py`).
"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

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
ConfirmationMode = Literal["manual", "none"]


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
    """How a payment becomes confirmed. It is always a person at the client who confirms it, after
    checking (`manual`); `none` means the client does not track confirmation here. No provider, no
    message and no answer of the prospect or of the model confirms a payment."""

    mode: ConfirmationMode = "manual"
    note: LocalizedText | None = None  # what the prospect is told to expect or do

    @field_validator("mode", mode="before")
    @classmethod
    def _no_automatic_confirmation(cls, value: object) -> object:
        if value == "provider":
            raise ValueError(
                "a payment is confirmed only by a person at the client, never by a provider: "
                "use 'manual'"
            )
        return value


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


class OnlinePayment(Model):
    """Whether a product can be paid online, through which methods (kind `online`, whose
    instruction carries the configured payment link). Off unless the product says so."""

    enabled: bool = False
    methods: tuple[Slug, ...] = Field(default=(), max_length=30)  # empty: every online method


class OfflinePayment(Model):
    """Whether a product can be paid offline (a transfer, a deposit, ...), through which methods,
    and whether the prospect must send a receipt after paying so that a person can verify it."""

    enabled: bool = False
    methods: tuple[Slug, ...] = Field(default=(), max_length=30)  # empty: every offline method
    receipt_required: bool = False

    @model_validator(mode="after")
    def _receipt_needs_offline(self) -> "OfflinePayment":
        if self.receipt_required and not self.enabled:
            raise ValueError("'receipt_required' is for offline payment, which is not enabled")
        return self


class ProductPayment(Model):
    """The payment options a product declares, explicitly. Without this block the product makes no
    declaration and the methods' own scopes decide; with it, an option that is not enabled is never
    offered and none of its methods (or their links) can be given to the prospect."""

    online: OnlinePayment = Field(default_factory=OnlinePayment)
    offline: OfflinePayment = Field(default_factory=OfflinePayment)

    def allows(self, method: PaymentMethod) -> bool:
        option = self.online if method.kind == "online" else self.offline
        return option.enabled and (not option.methods or method.id in option.methods)
