"""The kit's BrainSettingsSpec: what the application and a future admin interface rely on."""

import json
from types import MappingProxyType

import pytest
from kit_support import SPEC, edited, full, minimal

import sales_brain_kit.spec as spec_module
from sales_brain_kit import (
    FAMILY,
    SETTINGS_VERSION,
    ConsultativeSalesSpec,
    SettingsInvalidError,
    parse_settings,
)
from sales_core.ports import BrainSettingsSpec


class TestTheContract:
    def test_it_implements_the_core_port(self):
        assert isinstance(SPEC, BrainSettingsSpec)
        assert isinstance(ConsultativeSalesSpec(), BrainSettingsSpec)

    def test_it_names_its_family_and_version(self):
        assert SPEC.family == FAMILY == "consultative_sales"
        assert SPEC.schema_version == SETTINGS_VERSION == 1

    def test_validate_lists_problems_and_never_raises(self):
        assert SPEC.validate(minimal(), 1) == []
        assert SPEC.validate({}, 1)
        assert SPEC.validate({"business": 5}, 1)
        assert SPEC.validate({"business": {"name": "x"}, "communication": "fr"}, 1)

    def test_prepare_returns_the_normalised_settings(self):
        settings = SPEC.prepare(minimal(), 1)
        assert settings["business"] == {"name": "Acme Academy"}
        assert settings["discovery"]["budget"]["max_questions_per_message"] == 1

    def test_prepare_raises_a_value_error_for_invalid_settings(self):
        with pytest.raises(ValueError) as raised:
            SPEC.prepare({"business": {"name": "x"}}, 1)
        assert isinstance(raised.value, SettingsInvalidError)
        assert any("communication" in p for p in raised.value.problems)

    def test_the_default_version_is_the_current_one(self):
        assert SPEC.validate(minimal()) == []
        assert SPEC.prepare(minimal()) == SPEC.prepare(minimal(), 1)

    @pytest.mark.parametrize("version", [0, 2, 99, -1])
    def test_other_versions_are_not_understood(self, version):
        assert any("settings_version" in p for p in SPEC.validate(minimal(), version))
        with pytest.raises(SettingsInvalidError):
            SPEC.prepare(minimal(), version)


class TestInputShapes:
    def test_frozen_mappings_are_accepted(self):
        frozen = MappingProxyType(
            {
                "business": MappingProxyType({"name": "Acme"}),
                "communication": MappingProxyType(
                    {"languages": MappingProxyType({"default": "en", "supported": ("en",)})}
                ),
            }
        )
        assert SPEC.validate(frozen, 1) == []
        assert SPEC.prepare(frozen, 1)["business"]["name"] == "Acme"

    def test_the_input_is_never_modified(self):
        document = full()
        before = json.dumps(document, sort_keys=True)
        SPEC.prepare(document, 1)
        assert json.dumps(document, sort_keys=True) == before

    def test_settings_that_are_not_json_are_a_problem_not_a_crash(self):
        document = edited(minimal(), "business.name", "ok")
        document["extra"] = {1, 2}
        assert any("JSON" in p for p in SPEC.validate(document, 1))

    def test_a_size_limit_bounds_one_clients_settings(self, monkeypatch):
        monkeypatch.setattr(spec_module, "MAX_SETTINGS_BYTES", 200)
        found = SPEC.validate(full(), 1)
        assert len(found) == 1 and "bytes" in found[0]
        assert SPEC.validate(minimal(), 1) == []  # a small document is under the limit

    def test_the_real_limit_admits_a_large_catalog(self):
        document = minimal()
        document["catalog"] = {
            "products": [
                {
                    "id": f"p{i}",
                    "name": f"Product {i}",
                    "summary": "s" * 300,
                    "description": "d" * 3000,
                }
                for i in range(150)
            ]
        }
        assert SPEC.validate(document, 1) == []


class TestProblemMessages:
    def test_each_problem_starts_with_its_path(self):
        found = SPEC.validate(edited(full(), "catalog.products.0.pricing.amount", -5), 1)
        assert any(p.startswith("catalog.products.0.pricing.amount") for p in found)

    def test_cross_section_problems_carry_their_own_path(self):
        found = SPEC.validate(edited(full(), "objectives.primary", "nope"), 1)
        assert found == ["objectives.primary: 'nope' is not one of the goals"]

    def test_all_problems_come_back(self):
        document = edited(full(), "business.name", ...)
        document = edited(document, "catalog.products.0.pricing.amount", -5)
        document = edited(document, "communication.message.max_chars", 5)
        assert len(SPEC.validate(document, 1)) >= 3


class TestTheJsonSchemaForForms:
    schema = SPEC.json_schema()

    def test_it_identifies_the_family_and_version(self):
        assert self.schema["x-family"] == "consultative_sales"
        assert self.schema["x-settings-version"] == 1

    def test_it_is_plain_json(self):
        assert json.loads(json.dumps(self.schema)) == json.loads(json.dumps(dict(self.schema)))

    def test_it_describes_the_catalog_as_a_list_of_products(self):
        catalog = self.schema["$defs"]["CatalogSettings"]["properties"]["products"]
        assert catalog["type"] == "array" and catalog["maxItems"] == 200
        product = self.schema["$defs"]["Product"]
        assert set(product["required"]) == {"id", "name", "summary"}
        assert set(product["properties"]["status"]["enum"]) == {"active", "coming_soon", "inactive"}

    def test_it_lists_every_top_level_section(self):
        assert set(self.schema["properties"]) == {
            "business", "communication", "objectives", "stages", "catalog", "discovery",
            "conversation", "follow_up", "incoming", "handoff", "adaptation", "templates",
            "markets", "payment", "conversion",
        }  # fmt: skip
        assert set(self.schema["required"]) == {"business", "communication"}

    def test_the_documented_defaults_and_limits_are_visible(self):
        budget = self.schema["$defs"]["Budget"]["properties"]["max_questions_per_message"]
        assert budget["default"] == 1 and budget["maximum"] == 2

    def test_unknown_keys_are_forbidden_in_the_schema_too(self):
        assert self.schema["additionalProperties"] is False
        assert self.schema["$defs"]["Product"]["additionalProperties"] is False


class TestTypedAccess:
    def test_parse_settings_returns_the_typed_model(self):
        settings = parse_settings(full())
        assert settings.business.name == "Acme Academy"
        assert [p.id for p in settings.catalog.products][:2] == [
            "leadership-essentials", "sales-management",
        ]  # fmt: skip

    def test_parse_settings_raises_with_all_problems(self):
        with pytest.raises(SettingsInvalidError) as raised:
            parse_settings({"business": {"name": "x"}})
        assert raised.value.problems
