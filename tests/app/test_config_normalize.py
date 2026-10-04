"""The pure normaliser: every configuration readable today becomes a valid canonical (v3) spec.

The strongest test here compares, for every legacy shape, what the real configuration pipeline
produces today (`resolve_client_config`, from the baseline helpers) with what can be read back out
of the normalised result: if any value consumers use today could not be recovered from the
canonical output (and the separate environment references), that test fails.
"""

import builtins
import io
import json
import os
import re
import socket
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from app_support import client_yaml, write_client
from baseline_support import fixture_json, fixture_text, resolve, store_with_document

from sales_app.config import normalize as normalize_module
from sales_app.config.canonical import ClientSpec
from sales_app.config.normalize import (
    KNOWN_TABS,
    NormalizationError,
    Normalized,
    normalize,
)
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    SHEETS_CREDENTIALS_FILE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
from sales_integrations.google_sheets.layout import TAB_SPECS

NORMALIZE_FIXTURES = Path(__file__).resolve().parent / "normalize_fixtures"


def canonical_fixture(name: str) -> dict[str, Any]:
    return json.loads((NORMALIZE_FIXTURES / name).read_text(encoding="utf-8"))


def yaml_fixture(name: str) -> dict[str, Any]:
    return yaml.safe_load(fixture_text(name))


def minimal() -> dict[str, Any]:
    """The smallest legacy client.yaml the application accepts (what the app tests write)."""
    return yaml.safe_load(yaml.safe_dump(client_yaml("alpha")))


def valid(raw: dict[str, Any], document=None) -> Normalized:
    result = normalize(raw, document)
    ClientSpec.model_validate(result.spec)  # the canonical result is always a valid ClientSpec
    return result


def problems_of(raw, document=None, **kwargs) -> tuple[str, ...]:
    with pytest.raises(NormalizationError) as raised:
        normalize(raw, document, **kwargs)
    return raised.value.problems


def today(tmp_path, raw: dict[str, Any], document=None) -> dict[str, Any]:
    """What the real pipeline makes of the same input: the legacy spec, as plain JSON."""
    disabled = raw.get("enabled") is False  # a disabled client is never validated by the loader
    write_client(
        tmp_path, "alpha", raw=yaml.safe_dump({**raw, "enabled": True} if disabled else raw)
    )
    store = store_with_document("alpha", document) if document is not None else None
    config = resolve(tmp_path, "alpha", store=store).config
    assert config is not None
    expected = config.spec.model_dump(mode="json")
    return {**expected, "enabled": False} if disabled else expected


def legacy_view(result: Normalized) -> dict[str, Any]:
    """The legacy spec as consumers read it today, rebuilt from the canonical result alone."""
    spec, env = result.spec, result.env_fallbacks
    source, store, channels = spec["source"], spec["store"], spec["channels"]
    mapping = {**source["settings"]["column_mapping"], **store["settings"]["column_mapping"]}
    channel = channels[0]
    legacy = spec["llm"]["legacy"]
    return {
        "client_id": spec["client_id"],
        "display_name": spec["display_name"],
        "enabled": spec["enabled"],
        "mode": spec["mode"],
        "timezone": spec["timezone"],
        "daily_action_limit": spec["daily_action_limit"],
        "max_attempts": spec["max_attempts"],
        "opt_out_keywords": spec["opt_out_keywords"],
        "brain": {
            "factory": spec["brain"]["legacy_factory"],
            "settings": spec["brain"]["settings"],
            "settings_version": spec["brain"]["settings_version"],
        },
        "sheets": {
            "provider": source["provider"],
            "spreadsheet_id": source["settings"]["spreadsheet_id"],
            "prospects_tab": source["settings"]["prospects_tab"],
            "actions_tab": store["settings"]["actions_tab"],
            "messages_tab": store["settings"]["messages_tab"],
            "log_tab": store["settings"]["log_tab"],
            "column_mapping": mapping,
            "credentials_env": env[SHEETS_CREDENTIALS_FILE],
        },
        "llm": {
            "provider": legacy["provider"],
            "model": legacy["model_id"],
            "api_key_env": env[OPENAI_API_KEY],
        },
        "whatsapp": {
            "provider": channel["provider"],
            "api_version": channel["settings"]["api_version"],
            "token_env": env.get(WHATSAPP_ACCESS_TOKEN),
            "phone_number_id_env": env.get(WHATSAPP_PHONE_NUMBER_ID),
            "app_secret_env": env.get(WHATSAPP_APP_SECRET),
        },
        "schedule": {**spec["schedule"], **spec["polling"]},
        "inbound": spec["inbound"],
    }


def scenarios() -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
    """(client.yaml, stored document) for every legacy shape the application can read today."""
    bare_whatsapp = minimal()
    bare_whatsapp["whatsapp"] = {}
    disabled = minimal()
    disabled["enabled"] = False
    custom_provider = minimal()
    custom_provider["sheets"]["provider"] = "another_source"
    return {
        "complete_yaml": (yaml_fixture("legacy_complete.yaml"), None),
        "minimal_yaml": (minimal(), None),
        "yaml_without_any_whatsapp_reference": (bare_whatsapp, None),
        "disabled_yaml": (disabled, None),
        "yaml_naming_another_source": (custom_provider, None),
        "wiring_plus_document_v1": (yaml_fixture("wiring_only.yaml"), fixture_json("doc_v1.json")),
        "wiring_plus_document_v2": (yaml_fixture("wiring_only.yaml"), fixture_json("doc_v2.json")),
        "wiring_plus_document_v2_upstream_id": (
            yaml_fixture("wiring_only.yaml"),
            fixture_json("doc_v2_upstream_id.json"),
        ),
        "complete_yaml_overridden_by_a_document": (
            yaml_fixture("legacy_complete.yaml"),
            fixture_json("doc_v2.json"),
        ),
    }


SCENARIOS = scenarios()


class TestEveryReadableLegacyConfigurationNormalises:
    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_the_result_is_a_valid_client_spec(self, name):
        raw, document = SCENARIOS[name]
        spec = ClientSpec.model_validate(normalize(raw, document).spec)
        assert spec.schema_version == 3 and spec.channels and spec.store is not None

    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_every_value_consumers_use_today_can_be_read_back(self, name, tmp_path):
        raw, document = SCENARIOS[name]
        assert legacy_view(valid(raw, document)) == today(tmp_path, raw, document)

    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_normalising_twice_changes_nothing(self, name):
        raw, document = SCENARIOS[name]
        once = valid(raw, document)
        again = normalize(once.spec)
        assert again.spec == once.spec and again.origin == "canonical"

    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_the_same_input_gives_the_same_output(self, name):
        raw, document = SCENARIOS[name]
        assert normalize(raw, document) == normalize(deepcopy(raw), deepcopy(document))


class TestSheetsBecomeASourceAndAStore:
    def test_the_values_of_each_role(self):
        result = valid(yaml_fixture("legacy_complete.yaml"))
        assert result.spec["source"] == {
            "provider": "google_sheets",
            "settings": {
                "spreadsheet_id": "sheet-alpha",
                "prospects_tab": "Leads",
                "column_mapping": {"prospects.phone": "Mobile", "prospects.display_name": "Name"},
            },
        }
        assert result.spec["store"] == {
            "provider": "google_sheets",
            "connection": "same_as_source",
            "settings": {
                "actions_tab": "ActionsX",
                "messages_tab": "MessagesX",
                "log_tab": "LogX",
                "column_mapping": {"actions.draft_text": "Draft"},
            },
        }

    def test_the_defaults_of_the_legacy_schema_are_written_out(self):
        result = valid(minimal())
        assert result.spec["source"]["settings"] == {
            "spreadsheet_id": "sheet-alpha",
            "prospects_tab": "Prospects",
            "column_mapping": {},
        }
        assert result.spec["store"]["settings"] == {
            "actions_tab": "Actions",
            "messages_tab": "Messages",
            "log_tab": "Log",
            "column_mapping": {},
        }

    def test_each_mapping_prefix_goes_to_its_role_and_nothing_is_lost(self):
        raw = minimal()
        mapping = {
            "prospects.phone": "Tel",
            "prospects.email": "Mail",
            "actions.status": "State",
            "messages.text": "Body",
            "log.details": "Info",
            "facts.key": "K",
        }
        raw["sheets"]["column_mapping"] = mapping
        spec = valid(raw).spec
        source_map = spec["source"]["settings"]["column_mapping"]
        store_map = spec["store"]["settings"]["column_mapping"]
        assert source_map == {"prospects.phone": "Tel", "prospects.email": "Mail"}
        assert store_map == {
            "actions.status": "State",
            "messages.text": "Body",
            "log.details": "Info",
            "facts.key": "K",
        }
        assert {**source_map, **store_map} == mapping

    def test_the_provider_is_carried_to_both_roles_whatever_it_is(self):
        spec = valid(SCENARIOS["yaml_naming_another_source"][0]).spec
        assert spec["source"]["provider"] == spec["store"]["provider"] == "another_source"

    def test_the_same_spreadsheet_plays_both_roles(self):
        spec = valid(minimal()).spec
        assert spec["store"]["connection"] == "same_as_source"
        assert "spreadsheet_id" not in spec["store"]["settings"]

    def test_the_known_tab_prefixes_are_the_sheets_layout_tabs(self):
        assert set(KNOWN_TABS) == set(TAB_SPECS)

    @pytest.mark.parametrize(
        "key", ["leads.phone", "phone", "prospects.", ".phone", "Prospects.phone", "actions", ""]
    )
    def test_an_unknown_prefix_is_an_explicit_error(self, key):
        raw = minimal()
        raw["sheets"]["column_mapping"] = {key: "Header"}
        problems = problems_of(raw)
        assert len(problems) == 1 and repr(key) in problems[0]
        assert "not a known '<tab>.<field>'" in problems[0]

    def test_every_unknown_prefix_is_reported_at_once_and_good_keys_are_not(self):
        raw = minimal()
        raw["sheets"]["column_mapping"] = {
            "leads.phone": "A",
            "prospects.phone": "B",
            "nope": "C",
        }
        problems = problems_of(raw)
        assert len(problems) == 2
        assert any("'leads.phone'" in p for p in problems) and any("'nope'" in p for p in problems)


class TestWhatsappBecomesAChannel:
    def test_the_channel(self):
        result = valid(yaml_fixture("legacy_complete.yaml"))
        assert result.spec["channels"] == [
            {
                "id": "whatsapp",
                "provider": "whatsapp",
                "enabled": True,
                "priority": 1,
                "settings": {"api_version": "v22.0"},
            }
        ]

    def test_the_default_api_version_is_written_out(self):
        assert valid(minimal()).spec["channels"][0]["settings"] == {"api_version": "v21.0"}

    def test_the_channel_takes_the_providers_name_as_its_id(self):
        raw = minimal()
        raw["whatsapp"]["provider"] = "another_channel"
        channel = valid(raw).spec["channels"][0]
        assert (channel["id"], channel["provider"]) == ("another_channel", "another_channel")

    def test_the_channel_holds_no_account_or_credential(self):
        channel = valid(yaml_fixture("legacy_complete.yaml")).spec["channels"][0]
        assert set(channel) == {"id", "provider", "enabled", "priority", "settings"}
        assert set(channel["settings"]) == {"api_version"}


class TestTheLlmKeepsItsUpstreamIdentity:
    def test_the_provider_and_the_upstream_id_are_kept_under_legacy(self):
        llm = valid(yaml_fixture("legacy_complete.yaml")).spec["llm"]
        assert llm == {"legacy": {"provider": "openai", "model_id": "gpt-upstream-1"}}

    def test_no_catalogue_key_is_invented(self):
        for raw, document in SCENARIOS.values():
            assert "model" not in normalize(raw, document).spec["llm"]

    @pytest.mark.parametrize(
        "model_id", ["ft:gpt-4o:acme:custom:123", "gpt-4.1-2025-04-14", "GPT_X"]
    )
    def test_an_id_that_could_never_be_a_key_is_kept_whole(self, model_id):
        raw = minimal()
        raw["llm"]["model"] = model_id
        assert valid(raw).spec["llm"]["legacy"]["model_id"] == model_id

    def test_an_id_that_looks_like_a_key_is_not_taken_for_one(self):
        raw = minimal()
        raw["llm"]["model"] = "muse-spark"  # under the legacy provider, which is not Meta's
        llm = valid(raw).spec["llm"]
        assert llm == {"legacy": {"provider": "openai", "model_id": "muse-spark"}}

    def test_a_stored_document_with_an_upstream_id_keeps_it(self):
        raw, document = SCENARIOS["wiring_plus_document_v2_upstream_id"]
        assert valid(raw, document).spec["llm"] == {
            "legacy": {"provider": "openai", "model_id": "gpt-upstream-1"}
        }

    def test_a_version_1_document_takes_the_provider_from_the_wiring(self):
        raw, document = SCENARIOS["wiring_plus_document_v1"]
        assert "provider" not in document["llm"]
        assert valid(raw, document).spec["llm"]["legacy"]["provider"] == "openai"


class TestTheBrainFactory:
    FACTORY = "app_test_brains:good_brain"

    def test_an_unknown_factory_is_kept_as_legacy_factory(self):
        brain = valid(minimal()).spec["brain"]
        assert brain["legacy_factory"] == self.FACTORY and "agent" not in brain

    def test_a_known_factory_becomes_an_agent_key_and_the_path_is_dropped_only_then(self):
        brain = valid_with_brains(minimal(), {self.FACTORY: "test_brain"}).spec["brain"]
        assert brain["agent"] == "test_brain" and "legacy_factory" not in brain

    def test_other_known_brains_do_not_affect_an_unknown_factory(self):
        brain = valid_with_brains(minimal(), {"other_pkg:make": "other"}).spec["brain"]
        assert brain["legacy_factory"] == self.FACTORY and "agent" not in brain

    def test_the_brain_settings_and_their_version_are_kept(self):
        brain = valid(yaml_fixture("legacy_complete.yaml")).spec["brain"]
        assert brain["settings"] == {"tone": "warm"} and brain["settings_version"] == 1

    def test_nested_brain_settings_are_copied_not_shared(self):
        raw = minimal()
        raw["brain"]["settings"] = {"deep": {"list": [1, 2]}}
        result = normalize(raw)
        result.spec["brain"]["settings"]["deep"]["list"].append(3)
        assert raw["brain"]["settings"] == {"deep": {"list": [1, 2]}}


def valid_with_brains(raw, brains) -> Normalized:
    result = normalize(raw, None, known_brains=brains)
    ClientSpec.model_validate(result.spec)
    return result


class TestEnvironmentReferencesStayOutOfTheCanonicalSpec:
    def test_they_are_returned_apart_by_logical_credential_key(self):
        result = valid(yaml_fixture("legacy_complete.yaml"))
        assert dict(result.env_fallbacks) == {
            SHEETS_CREDENTIALS_FILE: "GOOGLE_APPLICATION_CREDENTIALS",
            OPENAI_API_KEY: "OPENAI_API_KEY",
            WHATSAPP_ACCESS_TOKEN: "ALPHA_TOKEN",
            WHATSAPP_PHONE_NUMBER_ID: "ALPHA_PHONE_ID",
            WHATSAPP_APP_SECRET: "ALPHA_APP_SECRET",
        }

    def test_the_spec_contains_no_env_key_and_no_env_name(self):
        for raw, document in SCENARIOS.values():
            text = json.dumps(normalize(raw, document).spec)
            assert "_env" not in text
            for name in ("ALPHA_TOKEN", "ALPHA_PHONE_ID", "ALPHA_APP_SECRET", "OPENAI_API_KEY"):
                assert name not in text

    def test_names_the_legacy_schema_defaults_are_applied_and_absent_ones_are_absent(self):
        env = valid(SCENARIOS["yaml_without_any_whatsapp_reference"][0]).env_fallbacks
        assert dict(env) == {
            SHEETS_CREDENTIALS_FILE: "GOOGLE_APPLICATION_CREDENTIALS",
            OPENAI_API_KEY: "OPENAI_API_KEY",
        }

    def test_a_custom_name_is_kept_exactly(self):
        raw = minimal()
        raw["llm"]["api_key_env"] = "MY_KEY"
        raw["sheets"]["credentials_env"] = "MY_GOOGLE"
        env = valid(raw).env_fallbacks
        assert env[OPENAI_API_KEY] == "MY_KEY" and env[SHEETS_CREDENTIALS_FILE] == "MY_GOOGLE"

    def test_a_value_is_never_read(self, monkeypatch):
        for name in ("ALPHA_TOKEN", "OPENAI_API_KEY", "ALPHA_PHONE_ID"):
            monkeypatch.setenv(name, "SECRET-VALUE-THAT-MUST-NOT-APPEAR")
        result = valid(yaml_fixture("legacy_complete.yaml"))
        assert "SECRET-VALUE" not in json.dumps(result.spec) + json.dumps(
            dict(result.env_fallbacks)
        )

    def test_the_references_come_from_the_wiring_even_when_a_document_owns_the_settings(self):
        raw, document = SCENARIOS["wiring_plus_document_v2"]
        assert valid(raw, document).env_fallbacks[WHATSAPP_ACCESS_TOKEN] == "ALPHA_TOKEN"


class TestDocumentsV1AndV2ReachV3:
    def test_a_version_1_document(self):
        raw, document = SCENARIOS["wiring_plus_document_v1"]
        result = valid(raw, document)
        spec = ClientSpec.model_validate(result.spec)
        assert result.document_version == 1 and result.origin == "legacy"
        assert spec.display_name == "Alpha V1" and spec.daily_action_limit == 9
        assert spec.source.settings["spreadsheet_id"] == "sheet-alpha-v1"
        assert spec.source.settings["column_mapping"] == {"prospects.phone": "Tel"}
        assert spec.schedule.daily_at == "08:00" and spec.schedule.days == ("mon", "tue")
        assert spec.brain.settings == {"tone": "v1"}
        # a version 1 document predates the inbound policy: replies keep being handled
        assert spec.inbound.spontaneous_conversations.enabled is True
        # the wiring is the operator's: it supplies the Brain, the account and the polling
        assert spec.brain.legacy_factory == "app_test_brains:good_brain"
        assert spec.polling.prepare_every_minutes == 60

    def test_a_version_2_document(self):
        raw, document = SCENARIOS["wiring_plus_document_v2"]
        result = valid(raw, document)
        spec = ClientSpec.model_validate(result.spec)
        assert result.document_version == 2
        assert spec.display_name == "Alpha V2" and spec.opt_out_keywords == ("stop", "halt")
        assert spec.source.settings["column_mapping"] == {"prospects.email": "Mail"}
        assert spec.store.settings["column_mapping"] == {"messages.text": "Body"}
        assert spec.inbound.spontaneous_conversations.allowed_decisions == ("send_message", "close")
        assert spec.inbound.limits.max_replies_per_prospect_per_day == 7
        assert spec.inbound.unknown_contacts.max_new_per_day == 5

    def test_a_version_2_document_that_says_nothing_about_inbound_gets_the_safe_defaults(self):
        raw, document = SCENARIOS["wiring_plus_document_v2_upstream_id"]
        spec = ClientSpec.model_validate(valid(raw, document).spec)
        assert spec.inbound.spontaneous_conversations.enabled is False

    def test_the_document_wins_over_settings_left_in_the_yaml(self):
        raw, document = SCENARIOS["complete_yaml_overridden_by_a_document"]
        spec = ClientSpec.model_validate(valid(raw, document).spec)
        assert spec.display_name == "Alpha V2" and spec.daily_action_limit == 11
        assert spec.source.settings["prospects_tab"] == "Prospects"  # the yaml's "Leads" is ignored

    def test_the_stored_document_is_not_modified(self):
        raw, document = SCENARIOS["wiring_plus_document_v1"]
        before = deepcopy(document)
        normalize(raw, document)
        assert document == before


class TestAlreadyCanonical:
    def test_a_canonical_mapping_is_accepted_as_it_is(self):
        data = canonical_fixture("canonical_naga.json")
        result = normalize(data)
        assert result.spec == data and result.origin == "canonical"
        assert result.env_fallbacks == {} and result.document_version is None
        ClientSpec.model_validate(result.spec)

    def test_it_is_idempotent(self):
        once = normalize(canonical_fixture("canonical_naga.json"))
        assert normalize(once.spec).spec == once.spec
        assert normalize(normalize(once.spec).spec).spec == once.spec

    def test_the_result_is_a_copy(self):
        data = canonical_fixture("canonical_naga.json")
        result = normalize(data)
        result.spec["source"]["settings"]["spreadsheet_id"] = "changed"
        assert data["source"]["settings"]["spreadsheet_id"] == "sheet-naga"
        assert normalize(MappingProxyType(data)).spec == data

    def test_a_canonical_mapping_with_a_version_2_document_is_refused(self):
        problems = problems_of(
            canonical_fixture("canonical_naga.json"), fixture_json("doc_v2.json")
        )
        assert "only be combined with a v3 document" in problems[0]


class TestDocumentsV3:
    def overlay(self, raw):
        return valid(raw, canonical_fixture("doc_v3.json"))

    def check(self, result):
        spec = ClientSpec.model_validate(result.spec)
        assert spec.display_name == "Alpha V3" and spec.llm.model == "muse-spark"
        assert [(c.id, c.enabled, c.priority) for c in spec.channels] == [
            ("whatsapp", True, 1),
            ("email", False, 2),
        ]
        assert spec.source.settings["prospects_tab"] == "Leads"
        assert spec.brain.settings == {"tone": "v3"}
        return spec

    def test_over_canonical_wiring(self):
        result = self.overlay(canonical_fixture("wiring_canonical.json"))
        spec = self.check(result)
        assert result.origin == "canonical" and result.document_version == 3
        assert spec.client_id == "alpha" and spec.max_attempts == 4
        assert spec.brain.agent == "consultative_sales" and spec.polling.dispatch_every_minutes == 3
        assert spec.brain.settings_version == 1

    def test_over_legacy_wiring(self):
        result = self.overlay(yaml_fixture("wiring_only.yaml"))
        spec = self.check(result)
        assert result.origin == "legacy" and result.document_version == 3
        assert (
            spec.client_id == "alpha" and spec.brain.legacy_factory == "app_test_brains:good_brain"
        )
        assert result.env_fallbacks[WHATSAPP_PHONE_NUMBER_ID] == "ALPHA_PHONE_ID"

    def test_legacy_wiring_gives_its_polling_to_the_canonical_spec(self):
        raw = yaml_fixture("wiring_only.yaml")
        raw["schedule"] = {"prepare_every_minutes": 45, "dispatch_every_minutes": 2}
        spec = ClientSpec.model_validate(self.overlay(raw).spec)
        assert (spec.polling.prepare_every_minutes, spec.polling.dispatch_every_minutes) == (45, 2)
        assert spec.polling.recover_every_minutes == 15

    def test_settings_left_in_the_yaml_are_ignored_as_with_older_documents(self):
        raw = yaml_fixture("legacy_complete.yaml")
        spec = ClientSpec.model_validate(self.overlay(raw).spec)
        assert spec.display_name == "Alpha V3" and spec.source.settings["prospects_tab"] == "Leads"

    def test_the_result_is_idempotent(self):
        result = self.overlay(canonical_fixture("wiring_canonical.json"))
        assert normalize(result.spec).spec == result.spec

    @pytest.mark.parametrize(
        ("name", "extra", "message"),
        [
            ("operator_key", {"max_attempts": 9}, "max_attempts: is not a client setting"),
            ("unknown_key", {"color": "red"}, "color: is not a client setting"),
            ("polling", {"polling": {}}, "polling: is not a client setting"),
            (
                "brain_agent",
                {"brain": {"agent": "naga", "settings": {}}},
                "brain.agent: is not a client setting",
            ),
            (
                "secret_looking_key",
                {"source": {"provider": "x_source", "settings": {"api_key": "k"}}},
                "looks like a secret",
            ),
        ],
    )
    def test_a_document_may_not_set_wiring_or_carry_a_secret(self, name, extra, message):
        document = {**canonical_fixture("doc_v3.json"), **extra}
        problems = problems_of(canonical_fixture("wiring_canonical.json"), document)
        assert any(message in p for p in problems), problems

    def test_an_unsupported_or_malformed_document(self):
        wiring = canonical_fixture("wiring_canonical.json")
        assert "schema_version" in problems_of(wiring, {"schema_version": 9})[0]
        assert "schema_version" in problems_of(wiring, {})[0]
        assert "schema_version" in problems_of(wiring, {"schema_version": True})[0]
        assert "JSON object" in problems_of(wiring, ["x"])[0]  # type: ignore[arg-type]

    def test_a_version_1_or_2_document_keeps_the_old_structure_rules(self):
        raw = yaml_fixture("wiring_only.yaml")
        problems = problems_of(raw, {"schema_version": 2, "whatsapp": {"token_env": "X"}})
        assert any("whatsapp" in p and "operator-owned" in p for p in problems)
        problems = problems_of(raw, {"schema_version": 2, "llm": {"model": "m", "api_key": "k"}})
        assert any("looks like a secret" in p for p in problems)


class TestIncompleteOrInvalidInput:
    def test_a_configuration_without_sheets(self):
        raw = minimal()
        del raw["sheets"]
        problems = problems_of(raw)
        assert problems[0].startswith("sheets: section missing")
        assert "spreadsheet_id" in problems[0]

    def test_a_document_without_sheets_over_wiring_that_has_none_either(self):
        raw = yaml_fixture("wiring_only.yaml")
        del raw["sheets"]
        document = {k: v for k, v in fixture_json("doc_v2.json").items() if k != "sheets"}
        assert any(p.startswith("sheets: section missing") for p in problems_of(raw, document))

    def test_a_document_that_does_not_complete_the_wiring(self):
        problems = problems_of(
            yaml_fixture("wiring_only.yaml"), {"schema_version": 2, "display_name": "X"}
        )
        assert any("spreadsheet_id: Field required" in p for p in problems)
        assert any("timezone: Field required" in p for p in problems)

    def test_missing_llm_and_brain_sections(self):
        raw = minimal()
        del raw["llm"], raw["brain"]
        problems = problems_of(raw)
        assert "llm: section missing; a legacy configuration needs `llm`" in problems
        assert "brain: section missing; a legacy configuration needs `brain`" in problems

    def test_an_unknown_key_is_never_dropped(self):
        raw = minimal()
        raw["surprise"] = 1
        raw["sheets"]["extra"] = 2
        problems = problems_of(raw)
        assert any("surprise" in p for p in problems) and any("sheets.extra" in p for p in problems)

    def test_unknown_wiring_keys_are_reported_beside_a_v3_document(self):
        raw = yaml_fixture("wiring_only.yaml")
        raw["surprise"] = 1
        raw["whatsapp"]["extra"] = 2
        problems = problems_of(raw, canonical_fixture("doc_v3.json"))
        assert "surprise: is not a known configuration key" in problems
        assert "whatsapp.extra: is not a known configuration key" in problems

    def test_every_problem_is_collected_in_one_error(self):
        raw = minimal()
        raw["timezone"] = "Mars/Base"
        raw["sheets"]["column_mapping"] = {"nope": "x"}
        raw["daily_action_limit"] = 0
        assert len(problems_of(raw)) >= 3

    def test_placeholders_are_refused_as_the_legacy_schema_refuses_them(self):
        for name in ("legacy_with_placeholders.yaml", "legacy_disabled_placeholders.yaml"):
            problems = problems_of(yaml_fixture(name))
            assert any("is still a placeholder" in p for p in problems)

    def test_the_error_message_joins_the_problems(self):
        raw = minimal()
        raw["sheets"]["column_mapping"] = {"a": "x", "b": "y"}
        with pytest.raises(NormalizationError, match="'a'.*'b'"):
            normalize(raw)

    @pytest.mark.parametrize("raw", [None, [], "client", 3])
    def test_the_input_must_be_a_mapping(self, raw):
        assert problems_of(raw) == ("configuration must be a mapping",)

    def test_a_non_json_value_is_an_error_not_a_crash(self):
        data = canonical_fixture("canonical_naga.json")
        data["source"]["settings"]["bad"] = object()
        assert "configuration" in problems_of(data)[0]


class TestPurity:
    def test_the_input_is_never_modified(self):
        for raw, document in SCENARIOS.values():
            raw_before, document_before = deepcopy(raw), deepcopy(document)
            normalize(raw, document)
            assert raw == raw_before and document == document_before

    def test_it_touches_no_file_no_environment_and_no_network(self, monkeypatch):
        def forbidden(*args, **kwargs):
            raise AssertionError("normalize must be pure")

        class ForbiddenEnvironment(dict):
            __getitem__ = get = __contains__ = keys = items = forbidden

        inputs = [(raw, document) for raw, document in SCENARIOS.values()]
        canonical = canonical_fixture("canonical_naga.json")  # read before the guard
        # The timezone database is read when a zone is first used; the validation of a timezone
        # name is the one legitimate lookup, so the zones in use are loaded before the guard.
        zones = [ZoneInfo(name) for name in ("Africa/Tunis", "UTC")]
        monkeypatch.setattr(builtins, "open", forbidden)
        monkeypatch.setattr(io, "open", forbidden)
        monkeypatch.setattr(os, "environ", ForbiddenEnvironment())
        monkeypatch.setattr(socket, "socket", forbidden)
        for raw, document in inputs:
            normalize(raw, document)
        normalize(canonical)
        assert zones

    def test_the_result_is_plain_json(self):
        for raw, document in SCENARIOS.values():
            result = normalize(raw, document)
            assert json.loads(json.dumps(result.spec)) == result.spec


class TestTheModuleIsInert:
    def test_nothing_in_the_application_imports_it_yet(self):
        """Retire this test when the normaliser is wired into configuration loading."""
        src = Path(normalize_module.__file__).resolve().parents[2]
        importers = [
            path.relative_to(src).as_posix()
            for path in src.rglob("*.py")
            if path.name != "normalize.py"
            and re.search(
                r"config\.normalize|from sales_app\.config import .*\bnormalize\b",
                path.read_text(encoding="utf-8"),
            )
        ]
        assert importers == []

    def test_the_normaliser_does_not_import_the_runtime(self):
        text = Path(normalize_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "sales_app.wiring",
            "sales_app.registry",
            "sales_app.api",
            "sales_integrations",
        ):
            assert forbidden not in text
