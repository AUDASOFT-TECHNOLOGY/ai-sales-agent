"""The generic `consultative` engine is wiring over the kit and nothing else.

Every client runs on it; a client's business content is its own published settings. The engine must
load, validate and run on any valid settings, and must never grow business logic of its own: every
capability it shows is the kit's. The settings used here are the generic tests' invented documents.
"""

import ast
import json
import re
from pathlib import Path

import pytest
from brain_support import answer, request
from commerce_support import ONGOING, at_payment, commerce_doc
from kit_support import minimal

from sales_app.brain_registry import installed_brains
from sales_brain_kit import (
    ConsultativeSalesBrain,
    ConsultativeSalesSpec,
    check_readiness,
    create_brain,
    generic,
    parse_settings,
)
from sales_core.contracts import DecisionType
from sales_core.ports import Brain
from sales_integrations.fakes import FakeLLMProvider

BRAIN = generic.BRAIN
SOURCE = Path(generic.__file__)


class TestTheDescriptor:
    def test_it_is_the_kits_own_factory_and_spec(self):
        assert BRAIN.key == "consultative"
        assert BRAIN.factory is create_brain
        assert type(BRAIN.settings_spec) is ConsultativeSalesSpec  # not a subclass or extension

    def test_the_application_finds_it_by_key_and_builds_a_brain(self):
        installed = installed_brains().get("consultative")
        brain = installed.factory(FakeLLMProvider(default="x"), commerce_doc())
        assert isinstance(brain, ConsultativeSalesBrain) and isinstance(brain, Brain)

    def test_the_generic_capabilities_work_through_it(self):
        """Payment, market and workflow are the kit's: a client gets them by being configured."""
        llm = FakeLLMProvider(
            [json.dumps(answer(message="[[payment:bank-transfer]]", language="fr"))]
        )
        decision = create_brain(llm, commerce_doc()).decide(
            request(facts=at_payment("Country A"), **ONGOING)
        )
        assert decision.decision_type is DecisionType.SEND_MESSAGE
        assert "Virement vers :" in decision.message.text


class TestTheSettingsRules:
    def test_the_admin_renders_forms_from_the_generic_schema(self):
        schema = BRAIN.settings_spec.json_schema()
        assert schema["x-family"] == "consultative_sales"
        assert {"catalog", "markets", "payment", "conversion"} <= set(schema["properties"])

    def test_validation_and_readiness_apply_to_any_client(self):
        assert BRAIN.settings_spec.validate(commerce_doc(), 1) == []
        assert BRAIN.settings_spec.validate({}, 1)  # empty settings: the spec says what is missing
        assert any(
            "no active product" in m for m in check_readiness(parse_settings(minimal())).missing
        )


class TestTheDescriptorHoldsNoBusinessLogic:
    def test_no_link_currency_language_delay_or_amount_is_written_in_it(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        strings = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        code = "\n".join(s for s in strings if s != ast.get_docstring(tree))
        assert "http" not in code
        assert not re.search(r"\b[A-Z]{3}\b", code)
        assert not re.search(r"\b(?:fr|ar|en|arabic|french|english)\b", code, re.IGNORECASE)

    @pytest.mark.parametrize("forbidden", ["MessagingChannel", "SalesRepository", "Engine"])
    def test_it_adds_no_second_brain_interface_and_no_effects(self, forbidden):
        assert forbidden not in SOURCE.read_text(encoding="utf-8")
