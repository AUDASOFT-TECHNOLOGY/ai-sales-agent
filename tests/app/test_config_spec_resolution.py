"""The canonical spec resolved beside the legacy pipeline (`resolve_client_spec`).

Two things are tested. First, that on every configuration the legacy pipeline reads, the new one
reaches the same verdict (enabled, disabled, or the same errors) and a spec from which every value
the legacy `ClientFile` carries can be read back. Second, what it does not do: it reads no secret and
no environment variable, calls no provider, imports no Brain, and nothing in the runtime uses it.
"""

import dataclasses
import inspect
import json
import os
import re
import socket
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from app_support import client_yaml, write_client
from baseline_support import (
    fixture_json,
    fixture_text,
    install_recording_vendors,
    resolve,
    store_with_document,
)

from sales_app.config import ClientConfig, resolve_client_config
from sales_app.config.canonical import ClientSpec
from sales_app.config.normalize import normalize
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    SHEETS_CREDENTIALS_FILE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
from sales_app.config.spec_resolution import SpecResolution, provider_problems, resolve_client_spec
from sales_app.config_store import ConfigStoreError, InMemoryConfigStore
from sales_app.wiring.providers import (
    ChannelSupport,
    ProviderKind,
    ProviderRegistry,
    default_registry,
)
from sales_core.approval import ApprovalMode

NORMALIZE_FIXTURES = Path(__file__).resolve().parent / "normalize_fixtures"
SRC = Path(__file__).resolve().parents[2] / "src"


def yaml_fixture(name: str) -> dict[str, Any]:
    return yaml.safe_load(fixture_text(name))


def canonical_fixture(name: str) -> dict[str, Any]:
    return json.loads((NORMALIZE_FIXTURES / name).read_text(encoding="utf-8"))


def minimal(**changes: Any) -> dict[str, Any]:
    data = yaml.safe_load(yaml.safe_dump(client_yaml("alpha")))
    for key, value in changes.items():
        data[key] = value
    return data


def put(root: Path, raw: dict[str, Any] | str, client_id: str = "alpha") -> Path:
    text = raw if isinstance(raw, str) else yaml.safe_dump(raw)
    write_client(root, client_id, raw=text)
    return root / client_id


def new_pipeline(root, raw, document=None, **kwargs) -> SpecResolution:
    directory = put(root, raw)
    store = store_with_document("alpha", document) if document is not None else None
    return resolve_client_spec(directory, store=store, **kwargs)


def both(root, raw, document=None):
    """The legacy resolution and the new one, over the same files and the same stored version."""
    directory = put(root, raw)
    store = store_with_document("alpha", document) if document is not None else None
    legacy = resolve_client_config(
        directory,
        env={},
        mode_ceiling=ApprovalMode.AUTONOMOUS,
        store=store,
    )
    return legacy, resolve_client_spec(directory, store=store)


def legacy_view(resolution: SpecResolution) -> dict[str, Any]:
    """The legacy `ClientFile` as a dump, rebuilt only from the canonical spec and the `*_env` names."""
    spec, env = resolution.spec.model_dump(mode="json"), resolution.env_fallbacks
    source, store = spec["source"]["settings"], spec["store"]["settings"]
    channel, legacy_llm = spec["channels"][0], spec["llm"]["legacy"]
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
            "provider": spec["source"]["provider"],
            "spreadsheet_id": source["spreadsheet_id"],
            "prospects_tab": source["prospects_tab"],
            "actions_tab": store["actions_tab"],
            "messages_tab": store["messages_tab"],
            "log_tab": store["log_tab"],
            "column_mapping": {**source["column_mapping"], **store["column_mapping"]},
            "credentials_env": env[SHEETS_CREDENTIALS_FILE],
        },
        "llm": {
            "provider": legacy_llm["provider"],
            "model": legacy_llm["model_id"],
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


def bare_whatsapp() -> dict[str, Any]:
    return minimal(whatsapp={})


# Every legacy configuration the application accepts: (client.yaml, stored document).
ACCEPTED = {
    "complete_yaml": (yaml_fixture("legacy_complete.yaml"), None),
    "minimal_yaml": (minimal(), None),
    "yaml_without_whatsapp_references": (bare_whatsapp(), None),
    "wiring_plus_v1_document": (yaml_fixture("wiring_only.yaml"), fixture_json("doc_v1.json")),
    "wiring_plus_v2_document": (yaml_fixture("wiring_only.yaml"), fixture_json("doc_v2.json")),
    "wiring_plus_v2_upstream_id": (
        yaml_fixture("wiring_only.yaml"),
        fixture_json("doc_v2_upstream_id.json"),
    ),
    "yaml_overridden_by_a_document": (
        yaml_fixture("legacy_complete.yaml"),
        fixture_json("doc_v2.json"),
    ),
}

# Configurations the legacy pipeline refuses: (client.yaml, stored document).
REFUSED = {
    "placeholders": (yaml_fixture("legacy_with_placeholders.yaml"), None),
    "unknown_key": ({**minimal(), "surprise": 1}, None),
    "bad_timezone": (minimal(timezone="Mars/Base"), None),
    "bad_mode": (minimal(mode="yolo"), None),
    "bad_factory": (minimal(brain={"factory": "not-a-path"}), None),
    "limit_out_of_range": (minimal(daily_action_limit=0), None),
    "missing_settings_without_a_version": (yaml_fixture("wiring_only.yaml"), None),
    "document_with_an_operator_key": (
        yaml_fixture("wiring_only.yaml"),
        {"schema_version": 2, "brain": {"factory": "a:b"}},
    ),
    "document_with_a_secret": (
        yaml_fixture("wiring_only.yaml"),
        {"schema_version": 2, "llm": {"model": "m", "api_key": "k"}},
    ),
    "document_with_a_bad_value": (
        yaml_fixture("wiring_only.yaml"),
        {**fixture_json("doc_v2.json"), "timezone": "Mars/Base"},
    ),
    "document_that_leaves_settings_out": (
        yaml_fixture("wiring_only.yaml"),
        {"schema_version": 2, "display_name": "X"},
    ),
}


class TestSameVerdictAsTheLegacyPipeline:
    @pytest.mark.parametrize("name", list(ACCEPTED))
    def test_an_accepted_configuration_gives_a_spec(self, name, tmp_path):
        raw, document = ACCEPTED[name]
        legacy, new = both(tmp_path, raw, document)
        assert legacy.config is not None and legacy.errors == ()
        assert new.errors == () and not new.disabled and isinstance(new.spec, ClientSpec)
        assert (new.config_source, new.config_version) == (
            legacy.config.config_source,
            legacy.config.config_version,
        )

    @pytest.mark.parametrize("name", list(ACCEPTED))
    def test_every_legacy_value_can_be_read_back_from_the_spec(self, name, tmp_path):
        raw, document = ACCEPTED[name]
        legacy, new = both(tmp_path, raw, document)
        assert legacy_view(new) == legacy.config.spec.model_dump(mode="json")

    @pytest.mark.parametrize("name", list(REFUSED))
    def test_a_refused_configuration_is_refused_with_the_same_messages(self, name, tmp_path):
        raw, document = REFUSED[name]
        legacy, new = both(tmp_path, raw, document)
        assert legacy.config is None and legacy.errors
        assert new.spec is None and new.errors
        assert set(new.errors) == set(legacy.errors)

    def test_the_client_id_must_equal_the_directory_name(self, tmp_path):
        legacy, new = both(tmp_path, {**yaml_fixture("legacy_complete.yaml"), "client_id": "other"})
        assert (
            new.errors
            == legacy.errors
            == ("alpha: client_id 'other' must equal the directory name",)
        )

    def test_an_unreadable_client_yaml_gives_the_same_errors(self, tmp_path):
        (tmp_path / "alpha").mkdir()
        for text in (None, "[1, 2", "- just\n- a list\n"):
            if text is not None:
                put(tmp_path, text)
            legacy = resolve_client_config(
                tmp_path / "alpha", env={}, mode_ceiling=ApprovalMode.AUTONOMOUS
            )
            assert resolve_client_spec(tmp_path / "alpha").errors == legacy.errors

    def test_a_failing_store_gives_the_same_error(self, tmp_path):
        class BrokenStore(InMemoryConfigStore):
            def current(self, client_id):
                raise ConfigStoreError("the store is down")

        directory = put(tmp_path, minimal())
        legacy = resolve_client_config(
            directory, env={}, mode_ceiling=ApprovalMode.AUTONOMOUS, store=BrokenStore()
        )
        new = resolve_client_spec(directory, store=BrokenStore())
        assert new.errors == legacy.errors == ("alpha: configuration store: the store is down",)

    def test_an_empty_store_changes_nothing(self, tmp_path):
        directory = put(tmp_path, minimal())
        legacy = resolve_client_config(
            directory, env={}, mode_ceiling=ApprovalMode.AUTONOMOUS, store=InMemoryConfigStore()
        )
        new = resolve_client_spec(directory, store=InMemoryConfigStore())
        assert (new.config_source, new.config_version) == ("file", None)
        assert legacy.config.config_source == "file"

    def test_a_stored_version_note_follows_validation_errors(self, tmp_path):
        raw, document = REFUSED["document_with_a_bad_value"]
        _, new = both(tmp_path, raw, document)
        assert "alpha: settings in force come from configuration version 1" in new.errors

    def test_the_hint_to_publish_a_version_is_given_when_settings_are_missing(self, tmp_path):
        raw, document = REFUSED["missing_settings_without_a_version"]
        _, new = both(tmp_path, raw, document)
        assert any("no configuration version is published" in e for e in new.errors)

    def test_a_schema_version_only_the_new_pipeline_supports_is_accepted_there(self, tmp_path):
        """The legacy pipeline reads versions 1 and 2; this one also reads 3 (and says 1, 2, 3)."""
        raw = yaml_fixture("wiring_only.yaml")
        legacy, new = both(tmp_path, raw, {"schema_version": 9})
        assert "must be one of [1, 2], got 9" in " ".join(legacy.errors)
        assert "must be one of [1, 2, 3], got 9" in " ".join(new.errors)


class TestDisabledIsDecidedFirst:
    """`enabled: false` excludes a client before anything is validated, as in the legacy pipeline."""

    def test_a_disabled_client_full_of_placeholders_is_accepted(self, tmp_path):
        raw = yaml_fixture("legacy_disabled_placeholders.yaml")
        directory = put(tmp_path, raw, "pholder")
        legacy = resolve_client_config(directory, env={}, mode_ceiling=ApprovalMode.AUTONOMOUS)
        new = resolve_client_spec(directory)
        assert legacy.disabled and new.disabled
        assert new.spec is None and new.errors == () == legacy.errors

    def test_a_disabled_client_is_not_validated_at_all(self, tmp_path):
        garbage = {"client_id": "alpha", "enabled": False, "sheets": "nonsense", "llm": 3}
        legacy, new = both(tmp_path, garbage)
        assert legacy.disabled and new.disabled and new.errors == ()

    def test_a_disabled_client_never_asks_the_store(self, tmp_path):
        class CountingStore(InMemoryConfigStore):
            calls = 0

            def current(self, client_id):
                CountingStore.calls += 1
                return super().current(client_id)

        directory = put(tmp_path, {**minimal(), "enabled": False})
        assert resolve_client_spec(directory, store=CountingStore()).disabled
        assert CountingStore.calls == 0
        put(tmp_path, minimal())
        assert resolve_client_spec(directory, store=CountingStore()).spec is not None
        assert CountingStore.calls == 1

    def test_a_disabled_canonical_client_is_excluded_too(self, tmp_path):
        data = {**canonical_fixture("canonical_naga.json"), "enabled": False}
        directory = put(tmp_path, data, "naga")
        assert resolve_client_spec(directory).disabled

    def test_enabled_is_read_with_the_legacy_default(self, tmp_path):
        raw = minimal()
        del raw["enabled"]
        assert new_pipeline(tmp_path, raw).spec.enabled is True

    def test_an_explicit_enabled_true_is_kept(self, tmp_path):
        assert new_pipeline(tmp_path, minimal(enabled=True)).spec.enabled is True

    def test_a_read_error_comes_before_the_enabled_check(self, tmp_path):
        directory = put(tmp_path, "enabled: [", "alpha")
        assert resolve_client_spec(directory).errors and not resolve_client_spec(directory).disabled


class TestCanonicalInputs:
    def canonical_yaml(self, **changes) -> dict[str, Any]:
        return {**canonical_fixture("canonical_naga.json"), "enabled": True, **changes}

    def test_a_canonical_client_yaml(self, tmp_path):
        directory = put(tmp_path, self.canonical_yaml(), "naga")
        result = resolve_client_spec(directory)
        assert result.errors == () and result.origin == "canonical"
        assert result.spec.client_id == "naga" and result.spec.llm.model == "gpt-lune"
        assert result.env_fallbacks == {} and result.config_source == "file"

    def test_it_preserves_client_id_and_enabled(self, tmp_path):
        result = resolve_client_spec(put(tmp_path, self.canonical_yaml(), "naga"))
        assert (result.spec.client_id, result.spec.enabled) == ("naga", True)

    def test_a_canonical_client_yaml_with_a_problem(self, tmp_path):
        data = self.canonical_yaml(channels=[{"provider": "a_channel"}, {"provider": "a_channel"}])
        result = resolve_client_spec(put(tmp_path, data, "naga"))
        assert result.spec is None
        assert any("naga: file: Value error, more than one channel" in e for e in result.errors)

    def test_a_canonical_client_yaml_with_an_unknown_key(self, tmp_path):
        result = resolve_client_spec(put(tmp_path, self.canonical_yaml(surprise=1), "naga"))
        assert any("surprise" in e and e.startswith("naga: ") for e in result.errors)

    def test_the_client_id_must_equal_the_directory_name(self, tmp_path):
        result = resolve_client_spec(put(tmp_path, self.canonical_yaml(), "other"))
        assert result.errors == ("other: client_id 'naga' must equal the directory name",)

    def test_a_v3_document_over_legacy_wiring(self, tmp_path):
        result = new_pipeline(
            tmp_path, yaml_fixture("wiring_only.yaml"), canonical_fixture("doc_v3.json")
        )
        assert result.errors == () and result.origin == "legacy"
        assert (result.config_source, result.config_version) == ("store", 1)
        assert result.spec.display_name == "Alpha V3" and result.spec.llm.model == "muse-spark"
        assert result.spec.brain.legacy_factory == "app_test_brains:good_brain"
        assert result.env_fallbacks[WHATSAPP_PHONE_NUMBER_ID] == "ALPHA_PHONE_ID"

    def test_a_v3_document_over_canonical_wiring(self, tmp_path):
        wiring = canonical_fixture("wiring_canonical.json")
        result = new_pipeline(tmp_path, wiring, canonical_fixture("doc_v3.json"))
        assert result.errors == () and result.origin == "canonical"
        assert result.spec.brain.agent == "consultative_sales"
        assert (result.config_source, result.config_version) == ("store", 1)

    def test_a_v3_document_may_not_set_wiring(self, tmp_path):
        document = {**canonical_fixture("doc_v3.json"), "max_attempts": 9}
        result = new_pipeline(tmp_path, canonical_fixture("wiring_canonical.json"), document)
        assert result.spec is None
        assert (
            "alpha: max_attempts: is not a client setting (operator-owned or unknown)"
            in result.errors
        )
        assert "alpha: settings in force come from configuration version 1" in result.errors

    def test_a_v3_document_with_a_secret_is_refused(self, tmp_path):
        document = canonical_fixture("doc_v3.json")
        document["source"]["settings"]["api_key"] = "k"
        result = new_pipeline(tmp_path, canonical_fixture("wiring_canonical.json"), document)
        assert any("looks like a secret" in e for e in result.errors)

    def test_a_v3_document_that_leaves_required_settings_out(self, tmp_path):
        document = {"schema_version": 3, "display_name": "X"}
        result = new_pipeline(tmp_path, canonical_fixture("wiring_canonical.json"), document)
        assert result.spec is None and any("timezone" in e for e in result.errors)


class TestTheResultIsTheNormalizersResult:
    @pytest.mark.parametrize("name", list(ACCEPTED))
    def test_the_spec_and_the_env_fallbacks_are_what_normalize_gives(self, name, tmp_path):
        raw, document = ACCEPTED[name]
        result = new_pipeline(tmp_path, raw, document)
        direct = normalize(raw, document)
        assert result.spec == ClientSpec.model_validate(direct.spec)
        assert dict(result.env_fallbacks) == dict(direct.env_fallbacks)
        assert result.origin == direct.origin

    def test_the_spec_resolves_to_the_same_spec_when_resolved_again(self, tmp_path):
        first = new_pipeline(tmp_path, ACCEPTED["complete_yaml"][0])
        again = new_pipeline(tmp_path, ACCEPTED["complete_yaml"][0])
        assert first == again


class TestEnvironmentReferences:
    def test_the_names_are_returned_by_logical_credential_key(self, tmp_path):
        result = new_pipeline(tmp_path, yaml_fixture("legacy_complete.yaml"))
        assert dict(result.env_fallbacks) == {
            SHEETS_CREDENTIALS_FILE: "GOOGLE_APPLICATION_CREDENTIALS",
            OPENAI_API_KEY: "OPENAI_API_KEY",
            WHATSAPP_ACCESS_TOKEN: "ALPHA_TOKEN",
            WHATSAPP_PHONE_NUMBER_ID: "ALPHA_PHONE_ID",
            WHATSAPP_APP_SECRET: "ALPHA_APP_SECRET",
        }

    def test_the_spec_holds_no_environment_name(self, tmp_path):
        result = new_pipeline(tmp_path, yaml_fixture("legacy_complete.yaml"))
        text = json.dumps(result.spec.model_dump(mode="json"))
        assert "_env" not in text and "ALPHA_TOKEN" not in text and "OPENAI_API_KEY" not in text

    def test_a_value_is_never_read(self, tmp_path, monkeypatch):
        for name in ("ALPHA_TOKEN", "OPENAI_API_KEY", "ALPHA_PHONE_ID", "ALPHA_APP_SECRET"):
            monkeypatch.setenv(name, "SECRET-VALUE-THAT-MUST-NOT-APPEAR")
        result = new_pipeline(tmp_path, yaml_fixture("legacy_complete.yaml"))
        everything = repr(result)
        assert "SECRET-VALUE" not in everything

    def test_a_canonical_client_has_no_environment_references(self, tmp_path):
        data = {**canonical_fixture("canonical_naga.json"), "enabled": True}
        assert resolve_client_spec(put(tmp_path, data, "naga")).env_fallbacks == {}


class TestProviders:
    def test_the_providers_of_the_legacy_fixtures_are_all_known_and_runnable(self, tmp_path):
        for name, (raw, document) in ACCEPTED.items():
            assert new_pipeline(tmp_path, raw, document).provider_problems == (), name

    def test_the_registry_is_the_one_source_of_providers(self, tmp_path):
        spec = new_pipeline(tmp_path, minimal()).spec
        assert provider_problems(spec) == provider_problems(spec, default_registry()) == []
        empty = provider_problems(spec, ProviderRegistry())
        assert {p.split(":")[0] for p in empty} == {
            "source.provider",
            "store.provider",
            "channels[0].provider",
            "llm.legacy.provider",
        }
        assert all("unknown provider" in p for p in empty)

    def test_an_unknown_provider_is_reported_and_the_spec_is_still_returned(self, tmp_path):
        raw = minimal(sheets={"spreadsheet_id": "s", "provider": "airtable"})
        result = new_pipeline(tmp_path, raw)
        assert result.errors == () and result.spec.source.provider == "airtable"
        assert "alpha: source.provider: unknown provider 'airtable'" in result.provider_problems
        assert "alpha: store.provider: unknown provider 'airtable'" in result.provider_problems
        assert result.problems == result.provider_problems

    def test_a_provider_that_cannot_play_the_role_is_reported(self, tmp_path):
        raw = minimal(whatsapp={"provider": "openai"}, llm={"provider": "whatsapp", "model": "m"})
        problems = new_pipeline(tmp_path, raw).provider_problems
        assert "alpha: channels[0].provider: 'openai' cannot be used as a channel" in problems
        assert "alpha: llm.legacy.provider: 'whatsapp' cannot be used as a llm" in problems

    def test_a_provider_known_but_not_runnable_is_reported(self, tmp_path):
        result = new_pipeline(
            tmp_path, minimal(llm={"provider": "meta", "model": "muse-spark-1.3"})
        )
        assert result.errors == ()
        assert (
            "alpha: llm.legacy.provider: 'meta' is known but cannot be run yet"
            in result.provider_problems
        )

    def test_an_unknown_model_provider_is_reported(self, tmp_path):
        result = new_pipeline(tmp_path, minimal(llm={"provider": "anthropic", "model": "claude-x"}))
        assert result.provider_problems == (
            "alpha: llm.legacy.provider: unknown provider 'anthropic'",
        )

    def test_a_store_the_source_cannot_host_is_reported(self, tmp_path):
        registry = ProviderRegistry()
        registry.register_fake("fake_source", ProviderKind.SOURCE, build=lambda c: None)
        registry.register_fake("fake_llm", ProviderKind.LLM, build=lambda c: None)
        raw = minimal(
            sheets={"spreadsheet_id": "s", "provider": "fake_source"},
            llm={"provider": "fake_llm", "model": "m"},
            whatsapp={},
        )
        problems = new_pipeline(tmp_path, raw, registry=registry).provider_problems
        assert any("store.provider: 'fake_source' cannot be used as a store" in p for p in problems)

    def test_the_settings_a_known_provider_refuses_are_reported(self, tmp_path):
        document = canonical_fixture("doc_v3.json")
        document["source"]["settings"] = {"prospects_tab": "Leads"}  # no spreadsheet_id
        document["channels"][0]["settings"] = {"api_version": ""}
        result = new_pipeline(tmp_path, canonical_fixture("wiring_canonical.json"), document)
        assert result.errors == () and result.spec is not None
        assert "alpha: source.settings.spreadsheet_id: Field required" in result.provider_problems
        assert any(
            p.startswith("alpha: channels[0].settings.api_version")
            for p in result.provider_problems
        )

    def test_a_disabled_channel_need_not_be_runnable(self, tmp_path):
        document = canonical_fixture("doc_v3.json")  # its email channel is disabled and unknown
        result = new_pipeline(tmp_path, canonical_fixture("wiring_canonical.json"), document)
        assert any(
            "channels[1].provider: unknown provider 'email'" in p for p in result.provider_problems
        )
        assert not any("cannot be run yet" in p for p in result.provider_problems)

    def test_fake_providers_are_accepted_through_a_registry_and_never_called(self, tmp_path):
        def explode(config):
            raise AssertionError("a provider must never be built while a configuration is read")

        registry = ProviderRegistry()
        registry.register_fake(
            "fake_source",
            ProviderKind.SOURCE,
            build=explode,
            roles=frozenset({ProviderKind.SOURCE, ProviderKind.STORE}),
            can_host_store=True,
        )
        registry.register_fake("fake_llm", ProviderKind.LLM, build=explode)
        registry.register_fake(
            "fake_channel",
            ProviderKind.CHANNEL,
            build=explode,
            channel=ChannelSupport("sms", explode, explode, explode),
        )
        raw = minimal(
            sheets={"spreadsheet_id": "s", "provider": "fake_source"},
            llm={"provider": "fake_llm", "model": "m"},
            whatsapp={"provider": "fake_channel"},
        )
        result = new_pipeline(tmp_path, raw, registry=registry)
        assert result.errors == () and result.provider_problems == ()


class TestBrains:
    FACTORY = "app_test_brains:good_brain"

    def test_an_unknown_brain_stays_a_legacy_factory(self, tmp_path):
        brain = new_pipeline(tmp_path, minimal()).spec.brain
        assert brain.legacy_factory == self.FACTORY and brain.agent is None

    def test_a_known_brain_becomes_a_catalogue_key(self, tmp_path):
        result = new_pipeline(tmp_path, minimal(), known_brains={self.FACTORY: "test_brain"})
        assert result.spec.brain.agent == "test_brain" and result.spec.brain.legacy_factory is None

    def test_other_known_brains_change_nothing(self, tmp_path):
        result = new_pipeline(tmp_path, minimal(), known_brains={"other:make": "other"})
        assert result.spec.brain.legacy_factory == self.FACTORY

    def test_no_brain_is_ever_imported_or_discovered(self, tmp_path, monkeypatch):
        import importlib
        import importlib.metadata

        def forbidden(*args, **kwargs):
            raise AssertionError("a Brain must not be imported while a configuration is read")

        monkeypatch.setattr(importlib, "import_module", forbidden)
        monkeypatch.setattr(importlib.metadata, "entry_points", forbidden)
        assert (
            new_pipeline(tmp_path, minimal(), known_brains={self.FACTORY: "known_brain"}).errors
            == ()
        )
        assert new_pipeline(tmp_path, minimal()).errors == ()

    def test_a_brains_own_settings_rules_stay_in_the_legacy_pipeline(self, tmp_path):
        """Known gap, on purpose: the legacy pipeline refuses settings a Brain's `SETTINGS_SPEC`
        rejects; the canonical spec carries brain settings as written (rules arrive with Brains
        resolved by catalogue key)."""
        raw = minimal(brain={"factory": "app_spec_brains:create_brain", "settings": {"tone": "x"}})
        legacy, new = both(tmp_path, raw)
        assert legacy.config is None and any("brain.settings" in e for e in legacy.errors)
        assert new.errors == () and dict(new.spec.brain.settings) == {"tone": "x"}


class TestNothingExternalHappens:
    def test_the_resolution_takes_neither_an_environment_nor_a_secret_store(self):
        parameters = set(inspect.signature(resolve_client_spec).parameters)
        assert parameters == {"directory", "store", "registry", "known_brains"}

    def test_no_environment_variable_no_socket_and_no_provider_is_touched(
        self, tmp_path, monkeypatch
    ):
        recording = install_recording_vendors(monkeypatch)

        def forbidden(*args, **kwargs):
            raise AssertionError("resolving a configuration must be pure")

        class ForbiddenEnvironment(dict):
            __getitem__ = get = __contains__ = keys = items = forbidden

        directories = []
        for name, (raw, document) in ACCEPTED.items():
            directories.append((put(tmp_path / name, raw), document))
        registry = default_registry()
        monkeypatch.setattr(os, "environ", ForbiddenEnvironment())
        monkeypatch.setattr(socket, "socket", forbidden)
        for directory, document in directories:
            store = store_with_document("alpha", document) if document is not None else None
            assert resolve_client_spec(directory, store=store, registry=registry).errors == ()
        assert recording.calls == []

    def test_resolving_does_not_change_what_the_legacy_pipeline_returns(self, tmp_path):
        directory = put(tmp_path, yaml_fixture("legacy_complete.yaml"))
        before = resolve(tmp_path)
        resolve_client_spec(directory)
        after = resolve(tmp_path)
        assert before.config.spec == after.config.spec
        assert before.config.missing_credentials == after.config.missing_credentials


class TestTheNewPipelineIsParallel:
    CONSUMERS: ClassVar[list[str]] = [
        "sales_app/config/loader.py",
        "sales_app/config/resolution.py",
        "sales_app/bootstrap.py",
        "sales_app/registry.py",
        "sales_app/runtime_manager.py",
        "sales_app/parsers.py",
        "sales_app/scheduler.py",
        "sales_app/cli.py",
        "sales_app/config_cli.py",
    ]

    def test_no_consumer_reads_the_canonical_pipeline(self):
        files = [SRC / c for c in self.CONSUMERS]
        files += list((SRC / "sales_app" / "wiring").glob("*.py"))
        files += list((SRC / "sales_app" / "api").glob("*.py"))
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"spec_resolution|config\.canonical|config\.normalize", text), path

    def test_only_the_new_pipeline_imports_the_canonical_modules(self):
        for module in ("canonical", "normalize"):
            importers = [
                p.relative_to(SRC).as_posix()
                for p in (SRC / "sales_app").rglob("*.py")
                if re.search(rf"config\.{module}\b", p.read_text(encoding="utf-8"))
                and p.name != f"{module}.py"
            ]
            assert importers == ["sales_app/config/spec_resolution.py"], module

    def test_the_legacy_resolution_is_unchanged_in_shape(self):
        assert [f.name for f in dataclasses.fields(ClientConfig)] == [
            "spec",
            "directory",
            "effective_mode",
            "secrets",
            "config_version",
            "config_source",
            "missing_credentials",
        ]
        assert list(inspect.signature(resolve_client_config).parameters) == [
            "directory",
            "env",
            "mode_ceiling",
            "store",
            "llm_registry",
            "secret_store",
        ]

    def test_the_new_pipeline_reaches_providers_only_through_their_registry(self):
        text = (SRC / "sales_app" / "config" / "spec_resolution.py").read_text(encoding="utf-8")
        for forbidden in (
            "sales_core",
            "sales_integrations",
            "sales_app.wiring.runtime",
            "sales_app.wiring.integrations",
            "sales_app.registry",
            "importlib",
            "os.environ",
            "secret_store",
        ):
            assert forbidden not in text, forbidden
        assert "sales_app.wiring.providers" in text
