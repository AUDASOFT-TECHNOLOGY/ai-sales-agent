"""Document schema 2 with the ConfigStore: Brain settings validated by the Brain's own spec, the
inbound policy, and schema 1 documents (and plain client.yaml files) that keep working."""

import json

import pytest
import yaml
from app_spec_brains import CREATED
from app_support import client_yaml, env_for, fake_builders, make_settings, write_client
from kit_support import full, minimal
from pydantic import ValidationError

from sales_app import cli
from sales_app.bootstrap import build_application
from sales_app.config import (
    ClientFile,
    import_document,
    resolve_client_config,
    validate_settings_document,
)
from sales_app.config.brain_settings import find_settings_spec, prepare_brain_settings
from sales_app.config.resolution import LEGACY_INBOUND, strip_settings, upgrade_document
from sales_app.config.schema import InboundSection
from sales_app.config_store import FileConfigStore, InMemoryConfigStore
from sales_app.errors import ConfigurationError
from sales_core.approval import ApprovalMode
from sales_core.ports import BrainSettingsSpec

SPEC_FACTORY = "app_spec_brains:create_brain"


def wiring(cid="alpha", factory=SPEC_FACTORY):
    """client.yaml with only what the operator owns, for a Brain that declares settings rules."""
    data = strip_settings(client_yaml(cid))
    data["brain"] = {"factory": factory}
    return data


def document(brain_settings=None, *, version=2, **overrides):
    doc = {
        "schema_version": version,
        "display_name": "Alpha",
        "timezone": "Africa/Tunis",
        "sheets": {"spreadsheet_id": "sheet-alpha"},
        "llm": {"model": "test-model"},
        "brain": {"settings": minimal() if brain_settings is None else brain_settings},
    }
    doc.update(overrides)
    return doc


def check(doc, *, factory=SPEC_FACTORY, cid="alpha"):
    return validate_settings_document(cid, wiring(cid, factory), doc)


def write_wiring(root, cid="alpha", factory=SPEC_FACTORY):
    (root / cid).mkdir(parents=True, exist_ok=True)
    (root / cid / "client.yaml").write_text(yaml.safe_dump(wiring(cid, factory)), encoding="utf-8")


def resolve(root, store, cid="alpha", ceiling=ApprovalMode.AUTONOMOUS):
    return resolve_client_config(root / cid, env=env_for(cid), mode_ceiling=ceiling, store=store)


class TestFindingASpec:
    def test_a_brain_module_may_declare_one(self):
        lookup = find_settings_spec(SPEC_FACTORY)
        assert isinstance(lookup.spec, BrainSettingsSpec) and lookup.error is None
        assert lookup.spec.family == "consultative_sales"

    def test_a_brain_without_one_keeps_opaque_settings(self):
        lookup = find_settings_spec("app_test_brains:good_brain")
        assert (lookup.spec, lookup.error, lookup.import_failed) == (None, None, False)

    def test_an_unimportable_module_is_reported_as_such(self):
        lookup = find_settings_spec("no_such_brain_module:create_brain")
        assert lookup.spec is None and lookup.import_failed and "cannot import" in lookup.error

    def test_something_that_is_not_a_spec_is_an_error(self):
        lookup = find_settings_spec("app_bad_spec_brains:create_brain")
        assert lookup.spec is None and not lookup.import_failed
        assert "does not implement BrainSettingsSpec" in lookup.error

    def test_the_generic_engine_declares_the_consultative_settings_rules(self):
        engine = find_settings_spec("sales_brain_kit.generic:BRAIN")
        assert engine.error is None and engine.spec is None  # a descriptor is not a spec module
        from sales_brain_kit.generic import BRAIN

        assert BRAIN.settings_spec.family == "consultative_sales"


class TestPublishingValidatesTheBrainSettings:
    def test_valid_settings_are_stored_normalised_with_their_version(self):
        result = check(document())
        assert result.errors == ()
        brain = result.document["brain"]
        assert brain["settings_version"] == 1
        settings = brain["settings"]
        assert settings["business"] == {"name": "Acme Academy"}
        assert (
            settings["discovery"]["budget"]["max_questions_per_message"] == 1
        )  # defaults explicit
        assert settings["follow_up"]["limits"] == {"max_touches": 4, "min_gap": "P1D"}

    def test_the_prepared_spec_carries_the_normalised_settings(self):
        result = check(document())
        assert result.spec.brain.settings == result.document["brain"]["settings"]
        assert result.spec.brain.settings_version == 1

    def test_the_full_example_is_accepted_and_stable(self):
        once = check(document(full())).document
        assert once is not None
        assert check(once).document == once  # publishing what was stored changes nothing

    def test_invalid_settings_are_rejected_with_every_structural_problem_and_their_path(self):
        bad = full()
        bad["catalog"]["products"][0]["pricing"]["amount"] = -1
        bad["catalog"]["products"][0]["typo"] = 1
        result = check(document(bad))
        assert result.document is None and result.spec is None
        assert all(e.startswith("brain.settings.") for e in result.errors)
        text = " | ".join(result.errors)
        assert "pricing.amount" in text and "typo" in text

    def test_references_between_sections_are_reported_together_once_the_structure_is_valid(self):
        bad = full()
        bad["objectives"]["primary"] = "nope"
        bad["handoff"]["triggers"][0]["intent"] = "ghost"
        result = check(document(bad))
        assert result.document is None
        text = " | ".join(result.errors)
        assert "brain.settings.objectives.primary" in text and "ghost" in text

    def test_a_settings_version_the_brain_does_not_know_is_rejected(self):
        doc = document()
        doc["brain"]["settings_version"] = 2
        result = check(doc)
        assert result.document is None
        assert any("settings_version 2 is not supported" in e for e in result.errors)

    def test_the_client_settings_are_still_validated_alongside(self):
        result = check(document(timezone="Mars/Olympus"))
        assert any(e.startswith("timezone:") for e in result.errors)

    def test_a_brain_module_that_cannot_be_imported_cannot_have_settings_published(self):
        result = check(document({"anything": 1}), factory="no_such_brain_module:create_brain")
        assert result.document is None and any("cannot import" in e for e in result.errors)

    def test_a_spec_that_is_not_a_spec_is_an_error_not_a_crash(self):
        result = check(document(), factory="app_bad_spec_brains:create_brain")
        assert result.document is None
        assert any("does not implement BrainSettingsSpec" in e for e in result.errors)

    def test_a_spec_that_raises_is_an_error_not_a_crash(self):
        result = check(document({"any": "thing"}), factory="app_exploding_spec_brains:create_brain")
        assert result.document is None and any("spec failed: boom" in e for e in result.errors)

    def test_a_brain_without_a_spec_is_unchanged_from_before(self):
        result = check(
            document({"tone": "warm", "offers": [1, 2]}), factory="app_test_brains:good_brain"
        )
        assert result.errors == ()
        assert result.document["brain"] == {
            "settings_version": 1, "settings": {"tone": "warm", "offers": [1, 2]},
        }  # fmt: skip

    def test_secret_looking_keys_are_still_refused_inside_brain_settings(self):
        bad = minimal()
        bad["business"]["api_key"] = "sk-123"
        result = check(document(bad))
        assert any("looks like a secret" in e for e in result.errors)

    def test_prepare_brain_settings_reports_problems_without_a_module_when_skipping(self):
        spec = ClientFile.model_validate(
            client_yaml("alpha", brain={"factory": "no_such_brain_module:f"})
        )
        assert prepare_brain_settings(spec, require_module=False) == (spec, ())
        assert prepare_brain_settings(spec, require_module=True)[1]


class TestLoadingValidatesToo:
    def test_a_stored_version_reaches_the_client_config_normalised(self, tmp_path):
        write_wiring(tmp_path)
        store = InMemoryConfigStore()
        store.publish("alpha", check(document(full())).document, author="t", expected_parent=None)
        config = resolve(tmp_path, store).config
        assert (config.config_source, config.config_version) == ("store", 1)
        assert config.spec.brain.settings["catalog"]["products"][0]["id"] == "leadership-essentials"
        assert config.spec.brain.settings["discovery"]["hypotheses"]["never_state_as_fact"] is True
        assert config.spec.brain.settings_version == 1

    def test_the_brain_is_built_with_the_normalised_settings(self, tmp_path):
        write_wiring(tmp_path)
        store = InMemoryConfigStore()
        store.publish("alpha", check(document()).document, author="t", expected_parent=None)
        CREATED.clear()
        builders, _ = fake_builders()
        application = build_application(
            make_settings(tmp_path),
            env=env_for("alpha"),
            builders=builders,
            config_store=store,
        )
        (settings,) = CREATED
        assert settings["business"] == {"name": "Acme Academy"}
        assert settings["discovery"]["budget"]["give_before_ask"] is True  # a default, filled in
        assert application.registry.get("alpha").config.spec.brain.settings_version == 1

    def test_an_invalid_stored_version_is_reported_and_leaves_the_running_config_alone(
        self, tmp_path
    ):
        write_wiring(tmp_path)
        store = InMemoryConfigStore()
        store.publish("alpha", check(document()).document, author="t", expected_parent=None)
        running = resolve(tmp_path, store).config
        snapshot = running.spec.model_dump_json()

        broken = check(document()).document
        broken["brain"]["settings"]["catalog"] = {"products": [{"id": "Bad Id"}]}
        store.publish(
            "alpha", broken, author="t", expected_parent=1
        )  # bypasses publish-time checks
        result = resolve(tmp_path, store)

        assert result.config is None
        assert any(e.startswith("alpha: brain.settings.catalog.products") for e in result.errors)
        assert any("configuration version 2" in e for e in result.errors)
        assert running.spec.model_dump_json() == snapshot and running.config_version == 1

    def test_startup_fails_fast_on_invalid_brain_settings(self, tmp_path):
        write_wiring(tmp_path)
        store = InMemoryConfigStore()
        bad = check(document()).document
        bad["brain"]["settings"] = {"business": {"name": "x"}}
        store.publish("alpha", bad, author="t", expected_parent=None)
        builders, _ = fake_builders()
        with pytest.raises(ConfigurationError) as raised:
            build_application(
                make_settings(tmp_path), env=env_for("alpha"), builders=builders, config_store=store
            )
        assert any("brain.settings.communication" in e for e in raised.value.errors)

    def test_a_client_yaml_alone_is_validated_by_the_brains_spec_too(self, tmp_path):
        data = client_yaml("alpha")
        data["brain"] = {"factory": SPEC_FACTORY, "settings": {"tone": "warm"}}  # not this schema
        write_client(tmp_path, "alpha", raw=yaml.safe_dump(data))
        result = resolve(tmp_path, None)
        assert result.config is None
        assert any(e.startswith("alpha: brain.settings.") for e in result.errors)

    def test_a_valid_client_yaml_is_normalised_like_a_stored_document(self, tmp_path):
        data = client_yaml("alpha")
        data["brain"] = {"factory": SPEC_FACTORY, "settings": minimal()}
        write_client(tmp_path, "alpha", raw=yaml.safe_dump(data))
        config = resolve(tmp_path, None).config
        assert config.config_source == "file"
        assert config.spec.brain.settings["follow_up"]["limits"]["max_touches"] == 4

    def test_an_unimportable_brain_is_left_for_the_runtime_build_to_report(self, tmp_path):
        write_wiring(tmp_path, factory="no_such_brain_module:create_brain")
        store = InMemoryConfigStore()
        store.publish(
            "alpha",
            {**check(document(), factory="app_spec_brains:create_brain").document},
            author="t", expected_parent=None,
        )  # fmt: skip
        result = resolve(tmp_path, store)
        assert result.config is not None and result.errors == ()

    def test_a_brain_without_a_spec_loads_exactly_as_before(self, tmp_path):
        write_client(tmp_path, "alpha")  # good_brain, settings {"tone": "alpha"}
        config = resolve(tmp_path, None).config
        assert config.spec.brain.settings == {"tone": "alpha"}


class TestSchemaOneStillWorks:
    def test_a_version_1_document_is_accepted_and_written_back_as_version_2(self):
        result = check(document({"tone": "warm"}, version=1), factory="app_test_brains:good_brain")
        assert result.errors == ()
        stored = result.document
        assert stored["schema_version"] == 2
        assert stored["brain"] == {"settings_version": 1, "settings": {"tone": "warm"}}
        # it predates the inbound policy, so it keeps today's behaviour, explicitly
        assert stored["inbound"]["spontaneous_conversations"]["enabled"] is True
        assert stored["inbound"]["unknown_contacts"]["policy"] == "ignore"

    def test_a_version_2_document_that_says_nothing_gets_the_safe_defaults(self):
        stored = check(document(), factory="app_test_brains:good_brain").document
        assert stored["inbound"]["spontaneous_conversations"]["enabled"] is False

    def test_a_version_1_document_cannot_use_version_2_keys(self):
        with_inbound = document({"tone": "x"}, version=1, inbound={"limits": {}})
        result = check(with_inbound, factory="app_test_brains:good_brain")
        assert any(e.startswith("inbound:") for e in result.errors) or any(
            "inbound" in e for e in result.errors
        )
        with_version = document({"tone": "x"}, version=1)
        with_version["brain"]["settings_version"] = 1
        result = check(with_version, factory="app_test_brains:good_brain")
        assert any("brain.settings_version" in e for e in result.errors)

    def test_upgrading_is_pure_and_only_touches_version_1(self):
        old = document({"tone": "x"}, version=1)
        before = json.dumps(old, sort_keys=True)
        upgraded = upgrade_document(old)
        assert json.dumps(old, sort_keys=True) == before and upgraded["schema_version"] == 2
        already = document({"tone": "x"}, version=2)
        assert upgrade_document(already) == already

    def test_versions_of_both_schemas_live_in_one_history(self, tmp_path):
        write_wiring(tmp_path, factory="app_test_brains:good_brain")
        store = InMemoryConfigStore()
        v1 = document({"tone": "old"}, version=1)
        store.publish("alpha", v1, author="t", expected_parent=None)  # written by the earlier code
        first = resolve(tmp_path, store).config
        assert first.spec.inbound.spontaneous_conversations.enabled is True
        v2 = check(document({"tone": "new"}), factory="app_test_brains:good_brain").document
        store.publish("alpha", v2, author="t", expected_parent=1)
        second = resolve(tmp_path, store).config
        assert second.spec.inbound.spontaneous_conversations.enabled is False
        assert (first.config_version, second.config_version) == (1, 2)
        assert first.spec.brain.settings == {"tone": "old"}

    def test_a_schema_this_code_does_not_know_is_reported(self, tmp_path):
        write_wiring(tmp_path, factory="app_test_brains:good_brain")
        store = InMemoryConfigStore()
        store.publish("alpha", document({"tone": "x"}, version=3), author="t", expected_parent=None)
        result = resolve(tmp_path, store)
        assert result.config is None
        assert any("configuration version 1: schema_version" in e for e in result.errors)

    def test_a_client_yaml_from_before_the_inbound_policy_keeps_todays_behaviour(self, tmp_path):
        write_client(tmp_path, "alpha")
        config = resolve(tmp_path, None).config
        assert config.spec.inbound.spontaneous_conversations.enabled is True
        assert config.spec.inbound.unknown_contacts.policy == "ignore"

    def test_a_client_yaml_that_states_its_policy_is_believed(self, tmp_path):
        data = client_yaml("alpha")
        data["inbound"] = {"spontaneous_conversations": {"enabled": False}}
        write_client(tmp_path, "alpha", raw=yaml.safe_dump(data))
        assert (
            resolve(tmp_path, None).config.spec.inbound.spontaneous_conversations.enabled is False
        )

    def test_importing_a_client_yaml_writes_the_legacy_behaviour_explicitly(self):
        result = import_document("alpha", client_yaml("alpha"))
        assert result.errors == ()
        assert result.document["schema_version"] == 2
        assert result.document["inbound"]["spontaneous_conversations"]["enabled"] is True
        assert result.document["brain"]["settings_version"] == 1
        assert LEGACY_INBOUND["spontaneous_conversations"] == {"enabled": True}

    def test_importing_validates_the_brain_settings_too(self):
        raw = client_yaml("alpha")
        raw["brain"] = {"factory": SPEC_FACTORY, "settings": {"tone": "warm"}}
        result = import_document("alpha", raw)
        assert result.document is None and any(
            e.startswith("brain.settings.") for e in result.errors
        )


class TestInboundPolicyDocument:
    def test_defaults_are_the_safe_ones(self):
        policy = InboundSection()
        assert policy.spontaneous_conversations.enabled is False
        assert policy.spontaneous_conversations.when_disabled == "store_and_flag"
        assert policy.unknown_contacts.policy == "ignore"
        assert policy.unknown_contacts.max_new_per_day == 30
        assert policy.limits.max_replies_per_prospect_per_day == 20
        assert len(policy.spontaneous_conversations.allowed_decisions) == 5

    def test_an_unknown_contact_is_created_only_by_the_engage_policy(self):
        assert (
            InboundSection.model_validate(
                {
                    "spontaneous_conversations": {"enabled": True},
                    "unknown_contacts": {"policy": "engage"},
                }
            ).unknown_contacts.policy
            == "engage"
        )
        for policy in ("hold_for_review", "create", "handoff"):
            with pytest.raises(ValidationError):
                InboundSection.model_validate({"unknown_contacts": {"policy": policy}})

    def test_engaging_strangers_requires_spontaneous_conversations(self):
        with pytest.raises(ValidationError, match="requires spontaneous_conversations.enabled"):
            InboundSection.model_validate({"unknown_contacts": {"policy": "engage"}})

    @pytest.mark.parametrize(
        "raw",
        [
            {"spontaneous_conversations": {"allowed_decisions": []}},
            {"spontaneous_conversations": {"allowed_decisions": ["send_message", "send_message"]}},
            {"spontaneous_conversations": {"allowed_decisions": ["sell_everything"]}},
            {"spontaneous_conversations": {"when_disabled": "reply_anyway"}},
            {"unknown_contacts": {"max_new_per_day": -1}},
            {"unknown_contacts": {"max_new_per_day": 1001}},
            {"limits": {"max_replies_per_prospect_per_day": 0}},
            {"limits": {"max_replies_per_prospect_per_day": 201}},
            {"limits": {"typo": 1}},
            {"typo": {}},
        ],
    )
    def test_bounds_and_unknown_keys(self, raw):
        with pytest.raises(ValidationError):
            InboundSection.model_validate(raw)

    def test_a_published_inbound_section_is_stored_normalised(self):
        inbound = {
            "spontaneous_conversations": {
                "enabled": True,
                "allowed_decisions": ["send_message", "no_action"],
            },
            "unknown_contacts": {"policy": "engage", "max_new_per_day": 5},
        }
        stored = check(document(inbound=inbound), factory="app_test_brains:good_brain").document
        assert stored["inbound"]["spontaneous_conversations"]["allowed_decisions"] == [
            "send_message", "no_action",
        ]  # fmt: skip
        assert stored["inbound"]["unknown_contacts"] == {"policy": "engage", "max_new_per_day": 5}
        assert stored["inbound"]["limits"] == {"max_replies_per_prospect_per_day": 20}

    def test_an_invalid_inbound_section_is_rejected_when_publishing(self):
        result = check(
            document(inbound={"unknown_contacts": {"policy": "engage"}}),
            factory="app_test_brains:good_brain",
        )
        assert result.document is None
        assert any("requires spontaneous_conversations.enabled" in e for e in result.errors)

    def test_the_operator_wiring_still_cannot_be_set_through_the_inbound_section(self):
        result = check(document(inbound={"whatsapp": {}}), factory="app_test_brains:good_brain")
        assert any(e.startswith("inbound.whatsapp:") for e in result.errors)

    def test_the_policy_reaches_the_client_config_and_nothing_enforces_it_yet(self, tmp_path):
        write_wiring(tmp_path, factory="app_test_brains:good_brain")
        store = InMemoryConfigStore()
        inbound = {
            "spontaneous_conversations": {"enabled": True},
            "unknown_contacts": {"policy": "engage"},
        }
        stored = check(
            document({"tone": "x"}, inbound=inbound), factory="app_test_brains:good_brain"
        )
        store.publish("alpha", stored.document, author="t", expected_parent=None)
        config = resolve(tmp_path, store).config
        assert config.spec.inbound.unknown_contacts.policy == "engage"


class TestTheCommandLine:
    @pytest.fixture
    def environment(self, tmp_path, monkeypatch):
        for name in list(__import__("os").environ):
            if name.startswith("SALES_AGENT_") or name == "WHATSAPP_VERIFY_TOKEN":
                monkeypatch.delenv(name)
        clients, config = tmp_path / "clients", tmp_path / "config"
        clients.mkdir()
        monkeypatch.setenv("SALES_AGENT_CLIENTS_DIR", str(clients))
        monkeypatch.setenv("SALES_AGENT_CONFIG_DIR", str(config))
        monkeypatch.setenv("SALES_AGENT_LOCK_FILE", str(tmp_path / "agent.lock"))
        monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
        for name, value in env_for("alpha").items():
            monkeypatch.setenv(name, value)
        write_wiring(clients)
        return tmp_path

    def run(self, *argv):
        return cli.main(["config", *argv])

    def write(self, path, brain_settings, **overrides):
        path.write_text(json.dumps(document(brain_settings, **overrides)), encoding="utf-8")
        return str(path)

    def versions(self, environment):
        return [v.version for v in FileConfigStore(environment / "config").history("alpha")]

    def test_publish_stores_the_normalised_brain_settings(self, environment, capsys):
        file = self.write(environment / "s.json", full())
        assert self.run("publish", "alpha", file) == cli.EXIT_OK
        stored = FileConfigStore(environment / "config").current("alpha")
        assert stored.document["schema_version"] == 2
        assert stored.document["brain"]["settings_version"] == 1
        assert (
            stored.document["brain"]["settings"]["catalog"]["products"][2]["status"]
            == "coming_soon"
        )
        assert (
            stored.document["brain"]["settings"]["discovery"]["fields"][0]["evidence"] == "required"
        )

    def test_publish_rejects_invalid_brain_settings_and_writes_nothing(self, environment, capsys):
        structural = full()
        structural["discovery"]["budget"] = {"max_questions_per_message": 3}
        assert self.run("publish", "alpha", self.write(environment / "a.json", structural)) == 2
        assert (
            "brain.settings.discovery.budget.max_questions_per_message" in capsys.readouterr().err
        )

        references = full()
        references["adaptation"]["dimensions"]["examples"]["inputs"] = ["gender"]
        assert self.run("publish", "alpha", self.write(environment / "b.json", references)) == 2
        assert "protected attribute" in capsys.readouterr().err
        assert self.versions(environment) == []
        assert not (environment / "config").exists()

    def test_a_version_1_file_still_publishes_and_becomes_version_2(self, environment):
        write_client(
            environment / "clients", "alpha", brain={"factory": "app_test_brains:good_brain"}
        )
        file = self.write(environment / "old.json", {"tone": "warm"}, version=1)
        assert self.run("publish", "alpha", file) == cli.EXIT_OK
        assert (
            FileConfigStore(environment / "config").current("alpha").document["schema_version"] == 2
        )

    def test_rolling_back_to_a_version_1_document_publishes_it_as_version_2(self, environment):
        write_client(
            environment / "clients", "alpha", brain={"factory": "app_test_brains:good_brain"}
        )
        store = FileConfigStore(environment / "config")
        store.publish(
            "alpha", document({"tone": "old"}, version=1), author="t", expected_parent=None
        )
        store.publish(
            "alpha",
            check(document({"tone": "new"}), factory="app_test_brains:good_brain").document,
            author="t", expected_parent=1,
        )  # fmt: skip
        assert self.run("rollback", "alpha", "--to", "1") == cli.EXIT_OK
        latest = store.current("alpha")
        assert latest.version == 3 and latest.document["schema_version"] == 2
        assert latest.document["brain"]["settings"] == {"tone": "old"}
        assert store.get("alpha", 1).document["schema_version"] == 1  # history is never rewritten

    def test_validate_checks_the_current_version_against_the_brains_spec(self, environment, capsys):
        file = self.write(environment / "s.json", minimal())
        assert self.run("publish", "alpha", file) == 0
        capsys.readouterr()
        assert self.run("validate", "alpha") == 0
        assert "alpha v1 is valid" in capsys.readouterr().out

    def test_import_seeds_version_one_with_the_legacy_inbound_behaviour(self, environment, capsys):
        write_client(environment / "clients", "alpha")
        assert self.run("import", "alpha") == cli.EXIT_OK
        stored = FileConfigStore(environment / "config").current("alpha").document
        assert stored["schema_version"] == 2
        assert stored["inbound"]["spontaneous_conversations"]["enabled"] is True

    def test_validate_reports_the_version_the_agent_runs_on(self, environment, monkeypatch, capsys):
        builders, _ = fake_builders()
        monkeypatch.setattr(
            cli, "build_application", lambda s, **kw: build_application(s, builders=builders, **kw)
        )
        assert self.run("publish", "alpha", self.write(environment / "s.json", minimal())) == 0
        capsys.readouterr()
        assert cli.main(["validate"]) == cli.EXIT_OK
        assert "[config v1]" in capsys.readouterr().out


class TestCommerceSettingsThroughThePublishingPath:
    """Slice 6b: markets, prices, payment and workflows are ordinary Brain settings for the
    ConfigStore: validated by the Brain's spec, stored normalised, exact values untouched."""

    def test_a_commerce_document_is_accepted_and_stored_with_its_exact_values(self):
        from commerce_support import ACCOUNT_NUMBER, PAY_URL, commerce_doc

        result = check(document(commerce_doc()))
        assert result.errors == ()
        stored = result.document["brain"]["settings"]
        methods = {m["id"]: m for m in stored["payment"]["methods"]}
        assert methods["bank-transfer"]["instructions"][0]["fields"][1]["value"] == ACCOUNT_NUMBER
        assert methods["online-card"]["instructions"][0]["url"] == PAY_URL
        assert json.dumps(stored)  # plain JSON, as the store keeps it

    def test_no_secret_lint_is_tripped_by_payment_data(self):
        from commerce_support import commerce_doc

        assert check(document(commerce_doc())).errors == ()

    def test_a_broken_payment_configuration_is_reported_with_its_path(self):
        from commerce_support import commerce_doc

        broken = commerce_doc()
        broken["payment"]["methods"][0]["instructions"][0].pop("url")
        result = check(document(broken))
        assert result.document is None
        assert any("brain.settings.payment.methods.0" in e and "'url'" in e for e in result.errors)

    def test_a_broken_workflow_is_reported_with_its_path(self):
        from commerce_support import commerce_doc

        broken = commerce_doc()
        broken["conversion"]["workflows"][0]["steps"][2]["next"] = "register"
        result = check(document(broken))
        assert any("conversion.workflows[0].steps" in e and "loop back" in e for e in result.errors)
