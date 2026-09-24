"""The Naga package is wiring and nothing else. Its business content is Naga's own data, none of
which is known yet, so the package must load, validate and run without any of it, and must never
grow business logic of its own: every capability it shows is the generic kit's.

The settings used here are the generic tests' invented documents; nothing in these tests is a
statement about Naga's business."""

import ast
import json
import re
from pathlib import Path

import naga_brain
import pytest
import yaml
from app_support import (
    SHARED_ENV,
    InlineExecutor,
    fake_builders,
    make_settings,
    write_client,
)
from brain_support import answer, request, settings_doc
from commerce_support import ONGOING, at_payment, commerce_doc
from factories import NOW
from kit_support import minimal
from naga_brain.brain import SETTINGS_SPEC, NagaBrain, create_brain

from sales_app.bootstrap import build_application
from sales_app.config import load_client_configs
from sales_app.config.brain_settings import find_settings_spec
from sales_app.wiring.brains import load_brain
from sales_brain_kit import (
    ConsultativeSalesBrain,
    ConsultativeSalesSpec,
    SettingsInvalidError,
    check_readiness,
    parse_settings,
)
from sales_core.approval import ApprovalMode
from sales_core.contracts import DecisionType
from sales_core.ports import Brain
from sales_integrations.fakes import FakeClock, FakeLLMProvider

REPO = Path(__file__).resolve().parents[3]
NAGA_CLIENT = REPO / "clients" / "naga" / "client.yaml"
FACTORY = "naga_brain.brain:create_brain"
ENV = {
    **SHARED_ENV,
    "NAGA_WHATSAPP_TOKEN": "token",
    "NAGA_WHATSAPP_PHONE_NUMBER_ID": "phone-naga",
    "NAGA_WHATSAPP_APP_SECRET": "secret",
}


def shipped() -> dict:
    return yaml.safe_load(NAGA_CLIENT.read_text(encoding="utf-8"))


def naga_with(settings: dict, root: Path, *, enabled: bool = True) -> Path:
    """The real client file, with the two operator placeholders filled in and `settings` as the
    Brain settings (Naga's own are not known yet)."""
    data = shipped()
    data["enabled"] = enabled
    data["sheets"]["spreadsheet_id"] = "sheet-naga"
    data["llm"]["model"] = "test-model"
    data["brain"]["settings"] = settings
    return write_client(root, "naga", raw=yaml.safe_dump(data))


class TestTheFactory:
    def test_the_client_file_names_the_naga_factory(self):
        assert shipped()["brain"]["factory"] == FACTORY

    def test_the_application_loader_builds_a_naga_brain(self):
        brain = load_brain(FACTORY, FakeLLMProvider(default="unused"), settings_doc())
        assert isinstance(brain, NagaBrain) and isinstance(brain, Brain)

    def test_it_is_the_generic_kit_brain_not_a_second_brain_interface(self):
        brain = create_brain(FakeLLMProvider(default="x"), settings_doc())
        assert isinstance(brain, ConsultativeSalesBrain)
        assert type(brain).decide is ConsultativeSalesBrain.decide  # nothing is re-implemented
        assert type(brain).__mro__[1] is ConsultativeSalesBrain  # and nothing sits in between

    def test_it_is_built_with_the_settings_it_was_given(self):
        brain = create_brain(FakeLLMProvider(default="x"), commerce_doc())
        assert brain.settings.business.name == "Acme Academy"
        assert [m.id for m in brain.settings.payment.methods][:1] == ["online-card"]

    def test_incomplete_settings_stop_the_brain_from_being_built(self):
        with pytest.raises(SettingsInvalidError, match="communication"):
            create_brain(FakeLLMProvider(default="x"), {"business": {"name": "Naga"}})

    def test_the_generic_capabilities_work_through_naga_s_wiring(self):
        """Payment, market and workflow are the kit's: Naga gets them by being configured."""
        llm = FakeLLMProvider(
            [json.dumps(answer(message="[[payment:bank-transfer]]", language="fr"))]
        )
        brain = create_brain(llm, commerce_doc())
        decision = brain.decide(request(facts=at_payment("Country A"), **ONGOING))
        assert decision.decision_type is DecisionType.SEND_MESSAGE
        assert "Virement vers :" in decision.message.text


class TestTheSettingsRules:
    def test_the_application_finds_the_kit_spec_next_to_the_factory(self):
        lookup = find_settings_spec(FACTORY)
        assert lookup.error is None and lookup.spec is SETTINGS_SPEC

    def test_naga_adds_no_rules_of_its_own(self):
        assert type(SETTINGS_SPEC) is ConsultativeSalesSpec  # not a subclass, not an extension

    def test_the_admin_renders_forms_from_the_generic_schema(self):
        schema = SETTINGS_SPEC.json_schema()
        assert schema["x-family"] == "consultative_sales"
        assert {"catalog", "markets", "payment", "conversion"} <= set(schema["properties"])

    def test_generic_validation_and_readiness_apply_to_naga_like_any_client(self):
        assert SETTINGS_SPEC.validate(commerce_doc(), 1) == []
        assert any(
            "no active product" in m for m in check_readiness(parse_settings(minimal())).missing
        )


class TestNoBusinessDataIsShipped:
    def test_the_seed_settings_are_empty(self):
        assert shipped()["brain"]["settings"] == {}

    def test_no_provisional_assumption_remains_in_the_client_file(self):
        text = NAGA_CLIENT.read_text(encoding="utf-8")
        for word in ("PROVISIONAL", "supported:", "default: fr", "formality", "products:"):
            assert word not in text, word

    def test_the_seed_says_exactly_what_is_missing_and_nothing_more(self):
        problems = SETTINGS_SPEC.validate({}, 1)
        assert problems and all(p.startswith(("business", "communication")) for p in problems)

    def test_naga_cannot_be_enabled_until_its_settings_are_entered(self, tmp_path):
        naga_with({}, tmp_path)
        loaded = load_client_configs(tmp_path, env=ENV, mode_ceiling=ApprovalMode.APPROVAL)
        assert loaded.clients == ()
        assert any(e.startswith("naga: brain.settings.business") for e in loaded.errors)
        assert any("brain.settings.communication" in e for e in loaded.errors)

    def test_it_ships_disabled(self):
        assert shipped()["enabled"] is False

    def test_it_loads_and_runs_with_no_catalog_at_all(self):
        llm = FakeLLMProvider([json.dumps(answer())])
        brain = create_brain(llm, minimal())
        assert brain.decide(request()).decision_type is DecisionType.SEND_MESSAGE


class TestThroughTheApplication:
    def test_naga_loads_once_its_settings_are_valid(self, tmp_path):
        naga_with(commerce_doc(), tmp_path)
        loaded = load_client_configs(tmp_path, env=ENV, mode_ceiling=ApprovalMode.APPROVAL)
        assert loaded.errors == ()
        (config,) = loaded.clients
        assert config.spec.brain.settings == SETTINGS_SPEC.prepare(commerce_doc(), 1)

    def test_invalid_settings_are_reported_at_startup_with_their_path(self, tmp_path):
        broken = commerce_doc()
        broken["payment"]["methods"][0]["instructions"] = []
        naga_with(broken, tmp_path)
        loaded = load_client_configs(tmp_path, env=ENV, mode_ceiling=ApprovalMode.APPROVAL)
        assert loaded.clients == ()
        assert any("brain.settings.payment.methods.0.instructions" in e for e in loaded.errors)

    def test_the_runtime_builds_a_naga_engine(self, tmp_path):
        naga_with(settings_doc(), tmp_path)
        clock = FakeClock(NOW)
        builders, made = fake_builders(clock)
        application = build_application(
            make_settings(tmp_path),
            env=ENV,
            builders=builders,
            clock=clock,
            executor=InlineExecutor(),
        )
        runtime = application.registry.get("naga")
        assert runtime is not None and isinstance(runtime.engine._brain, NagaBrain)
        assert made.llms["naga"] is runtime.engine._brain._llm


PACKAGE = Path(naga_brain.__file__).parent
PYTHON_FILES = sorted(PACKAGE.rglob("*.py"))


def strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(getattr(node.body[0], "value", None), ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class TestThePackageHoldsNoBusinessLogic:
    def test_it_has_only_its_wiring(self):
        assert {p.name for p in PYTHON_FILES} == {"__init__.py", "brain.py"}

    @pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda p: p.name)
    def test_no_link_currency_language_delay_or_amount_is_written_in_code(self, path):
        code = "\n".join(strings(path))
        assert "http" not in code
        assert not re.search(r"\b(?:[A-Z]{3})\b", code)
        assert not re.search(r"\bP\d+[DWH]\b|\bPT\d+[HM]\b", code)
        assert not re.search(r"\b(?:fr|ar|en|arabic|french|english)\b", code, re.IGNORECASE)
        numbers = {
            node.value
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, int | float)
            and not isinstance(node.value, bool)
        }
        assert numbers <= {0, 1}

    def test_no_commercial_concept_is_named_in_the_code(self):
        tree = ast.parse((PACKAGE / "brain.py").read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.ClassDef)}
        pattern = r"product|price|pay|market|country|language|follow|workflow|handoff|tone"
        assert not [n for n in names if re.search(pattern, n, re.IGNORECASE)]
