"""The canonical (v3) configuration schema: what it accepts, what it refuses, that it is immutable.

The schema is provider-neutral: provider keys here are only examples of well-formed keys, and a
test checks the module itself names none. Settings of a provider are free at this level; their
validation belongs to the provider descriptors (a later increment).
"""

import re
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import ValidationError

from sales_app.config import canonical
from sales_app.config.canonical import (
    ChannelBinding,
    ClientSpec,
    LLMBinding,
    ProviderBinding,
    StoreBinding,
)
from sales_core.approval import ApprovalMode

NAGA: dict[str, Any] = {
    "schema_version": 3,
    "client_id": "naga",
    "display_name": "Naga",
    "enabled": False,
    "mode": "approval",
    "timezone": "Africa/Tunis",
    "daily_action_limit": 20,
    "max_attempts": 3,
    "opt_out_keywords": ["stop"],
    "brain": {"agent": "naga", "settings_version": 1, "settings": {}},
    "source": {
        "provider": "google_sheets",
        "settings": {
            "spreadsheet_id": "sheet-naga",
            "prospects_tab": "Prospects",
            "column_mapping": {},
        },
    },
    "store": {
        "provider": "google_sheets",
        "connection": "same_as_source",
        "settings": {
            "actions_tab": "Actions",
            "messages_tab": "Messages",
            "log_tab": "Log",
            "column_mapping": {},
        },
    },
    "channels": [
        {
            "id": "whatsapp",
            "provider": "whatsapp",
            "enabled": True,
            "priority": 1,
            "settings": {"api_version": "v21.0"},
        }
    ],
    "llm": {"model": "gpt-lune"},
    "schedule": {"daily_at": "09:00", "days": ["mon", "tue", "wed", "thu", "fri", "sat"]},
    "polling": {
        "prepare_every_minutes": 60,
        "dispatch_every_minutes": 5,
        "recover_every_minutes": 15,
    },
    "inbound": {
        "spontaneous_conversations": {"enabled": False},
        "unknown_contacts": {"policy": "ignore", "max_new_per_day": 30},
        "limits": {"max_replies_per_prospect_per_day": 20},
    },
}

# A different source, a store kept elsewhere, two channels and another provider's model.
CONCEPTUAL: dict[str, Any] = {
    "schema_version": 3,
    "client_id": "acme",
    "display_name": "Acme",
    "timezone": "Europe/Paris",
    "brain": {"agent": "consultative_sales", "settings": {"tone": "warm"}},
    "source": {
        "provider": "airtable",
        "settings": {"base_id": "appXXXX", "table": "Leads", "view": "To contact"},
    },
    "store": {
        "provider": "google_sheets",
        "connection": {"settings": {"spreadsheet_id": "sheet-acme"}},
        "settings": {"actions_tab": "Actions", "messages_tab": "Messages", "log_tab": "Log"},
    },
    "channels": [
        {"id": "whatsapp", "provider": "whatsapp", "priority": 1},
        {
            "id": "email",
            "provider": "email",
            "priority": 2,
            "settings": {"from_address": "sales@acme.example", "reply_to": "sales@acme.example"},
        },
    ],
    "llm": {"model": "muse-spark"},
}


def spec(**overrides: Any) -> ClientSpec:
    data = deepcopy(NAGA)
    for key, value in overrides.items():
        data[key] = value
    return ClientSpec.model_validate(data)


def refused(message: str, **overrides: Any) -> None:
    with pytest.raises(ValidationError, match=message):
        spec(**overrides)


class TestValidConfigurations:
    def test_the_naga_example(self):
        naga = spec()
        assert naga.client_id == "naga" and naga.mode is ApprovalMode.APPROVAL
        assert naga.enabled is False and naga.brain.agent == "naga"
        assert naga.source.provider == "google_sheets"
        assert naga.source.settings["spreadsheet_id"] == "sheet-naga"
        assert isinstance(naga.store, StoreBinding) and naga.store.connection == "same_as_source"
        assert [(c.id, c.provider, c.priority) for c in naga.channels] == [
            ("whatsapp", "whatsapp", 1)
        ]
        assert naga.llm.model == "gpt-lune" and naga.llm.legacy is None

    def test_the_conceptual_example(self):
        acme = ClientSpec.model_validate(CONCEPTUAL)
        assert acme.source.provider == "airtable"
        assert acme.store is not None and acme.store.provider == "google_sheets"
        assert acme.store.connection.settings["spreadsheet_id"] == "sheet-acme"
        assert [(c.id, c.provider, c.priority) for c in acme.channels] == [
            ("whatsapp", "whatsapp", 1),
            ("email", "email", 2),
        ]
        assert acme.llm.model == "muse-spark"

    def test_the_three_required_models_are_valid_keys(self):
        for key in ("gpt-lune", "gpt-sol", "muse-spark"):
            assert spec(llm={"model": key}).llm.model == key

    def test_defaults_are_the_safe_ones(self):
        minimal = {
            "schema_version": 3,
            "client_id": "min",
            "display_name": "Min",
            "timezone": "UTC",
            "brain": {"agent": "naga"},
            "source": {"provider": "some_source"},
            "llm": {"model": "some-model"},
        }
        result = ClientSpec.model_validate(minimal)
        assert result.enabled is True and result.mode is ApprovalMode.DRY_RUN
        assert (result.daily_action_limit, result.max_attempts) == (20, 3)
        assert result.opt_out_keywords == ("stop",)
        assert result.store is None and result.channels == ()
        assert result.source.settings == {} and result.brain.settings == {}
        assert result.schedule.daily_at == "09:00" and result.schedule.days[0] == "mon"
        assert result.polling.dispatch_every_minutes == 5
        assert result.inbound.spontaneous_conversations.enabled is False

    def test_a_channel_id_defaults_to_its_provider(self):
        channel = spec(channels=[{"provider": "some_channel"}]).channels[0]
        assert (channel.id, channel.enabled, channel.priority) == ("some_channel", True, 0)

    def test_a_client_may_have_no_channel(self):
        assert spec(channels=[]).channels == ()

    def test_a_disabled_channel_still_counts_as_configured(self):
        result = spec(channels=[{"provider": "a_channel", "enabled": False}])
        assert result.channels[0].enabled is False

    def test_the_older_model_reference_is_accepted_beside_or_instead_of_a_key(self):
        legacy = {"provider": "some_provider", "model_id": "upstream-id-1"}
        assert spec(llm={"legacy": legacy}).llm.model is None
        both = spec(llm={"model": "some-key", "legacy": legacy}).llm
        assert both.model == "some-key" and both.legacy.model_id == "upstream-id-1"

    def test_the_older_brain_factory_form_is_accepted_alone(self):
        brain = {"legacy_factory": "naga_brain.brain:create_brain", "settings": {}}
        assert spec(brain=brain).brain.legacy_factory == "naga_brain.brain:create_brain"

    def test_provider_settings_are_free_json(self):
        free = {"anything": [1, 2.5, None, True, {"nested": ["x"]}], "other": "y"}
        assert (
            spec(source={"provider": "some_source", "settings": free}).source.settings["other"]
            == "y"
        )

    def test_validating_what_a_spec_dumps_gives_the_same_spec(self):
        for data in (NAGA, CONCEPTUAL):
            original = ClientSpec.model_validate(data)
            dumped = original.model_dump(mode="json")
            assert ClientSpec.model_validate(dumped) == original
            assert dumped["source"]["settings"] == data["source"]["settings"]

    @pytest.mark.parametrize("provider", ["ab", "some_provider", "x" * 32, "a1_b2"])
    def test_any_well_formed_provider_key_is_accepted(self, provider):
        assert spec(source={"provider": provider}).source.provider == provider


class TestRefusals:
    def test_an_unknown_top_level_key(self):
        refused("surprise", surprise=1)

    @pytest.mark.parametrize(
        "section",
        ["brain", "source", "llm", "schedule", "polling"],
    )
    def test_an_unknown_key_in_any_section(self, section):
        data = deepcopy(NAGA)
        data[section]["surprise"] = 1
        with pytest.raises(ValidationError, match="surprise"):
            ClientSpec.model_validate(data)

    def test_an_unknown_key_in_the_store_a_channel_and_a_connection(self):
        for mutate in (
            lambda d: d["store"].update(surprise=1),
            lambda d: d["channels"][0].update(surprise=1),
            lambda d: d["store"].update(connection={"settings": {"k": "v"}, "surprise": 1}),
        ):
            data = deepcopy(NAGA)
            mutate(data)
            with pytest.raises(ValidationError, match="surprise"):
                ClientSpec.model_validate(data)

    def test_the_schema_version_must_be_three(self):
        for version in (1, 2, 4, "3"):
            refused("schema_version", schema_version=version)
        data = deepcopy(NAGA)
        del data["schema_version"]
        with pytest.raises(ValidationError, match="schema_version"):
            ClientSpec.model_validate(data)

    def test_two_channels_of_the_same_provider(self):
        channels = [
            {"id": "first", "provider": "a_channel"},
            {"id": "second", "provider": "a_channel"},
        ]
        refused("more than one channel for provider 'a_channel'", channels=channels)

    def test_the_same_provider_twice_with_default_ids(self):
        refused(
            "more than one channel", channels=[{"provider": "a_channel"}, {"provider": "a_channel"}]
        )

    def test_a_disabled_channel_still_counts_towards_one_per_provider(self):
        channels = [
            {"provider": "a_channel"},
            {"id": "other", "provider": "a_channel", "enabled": False},
        ]
        refused("more than one channel", channels=channels)

    def test_two_channels_with_the_same_id(self):
        channels = [
            {"id": "main", "provider": "one_channel"},
            {"id": "main", "provider": "two_channel"},
        ]
        refused("channel id 'main' is used more than once", channels=channels)

    @pytest.mark.parametrize(
        "provider",
        [
            "",
            "a",
            "Upper",
            "1abc",
            "has-hyphen",
            "has.dot",
            "has space",
            "pkg.mod:fn",
            "x" * 33,
            "é",
        ],
    )
    def test_a_malformed_provider_key(self, provider):
        refused("provider", source={"provider": provider})
        refused("provider", channels=[{"id": "ok", "provider": provider}])
        refused("provider", store={"provider": provider})

    @pytest.mark.parametrize(
        "channel_id", ["", "Upper", "1abc", "has.dot", "has space", "x" * 33, "a/b"]
    )
    def test_a_malformed_channel_id(self, channel_id):
        refused("id", channels=[{"id": channel_id, "provider": "a_channel"}])

    @pytest.mark.parametrize(
        "agent",
        [
            "naga_brain.brain:create_brain",
            "naga_brain.brain",
            "pkg:fn",
            "a.b",
            "../x",
            "x/y",
            "Naga",
            "a",
            "",
            "x" * 65,
        ],
    )
    def test_an_agent_that_looks_like_a_python_path_or_is_malformed(self, agent):
        refused("agent", brain={"agent": agent})

    def test_a_brain_needs_exactly_one_way_to_be_named(self):
        refused("exactly one of", brain={"settings": {}})
        refused(
            "exactly one of",
            brain={"agent": "naga", "legacy_factory": "naga_brain.brain:create_brain"},
        )

    @pytest.mark.parametrize("factory", ["not-a-path", "pkg.mod", "pkg:", ":fn", "pkg:a:b", ""])
    def test_a_malformed_legacy_factory(self, factory):
        refused("legacy_factory", brain={"legacy_factory": factory})

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "a",
            "GPT-Lune",
            "1abc",
            "muse_spark",
            "gpt.lune",
            "gpt lune",
            "-gpt",
            "x" * 65,
            "gpt/sol",
        ],
    )
    def test_an_invalid_model_key(self, key):
        refused("model", llm={"model": key})

    def test_an_llm_needs_a_model_or_the_older_reference(self):
        refused("needs a model key", llm={})

    def test_an_incomplete_older_model_reference(self):
        refused("model_id", llm={"legacy": {"provider": "some_provider"}})
        refused("provider", llm={"legacy": {"model_id": "x"}})
        refused("model_id", llm={"legacy": {"provider": "some_provider", "model_id": " "}})

    @pytest.mark.parametrize("priority", [-1, -100, 1.5, "1", True, None])
    def test_an_invalid_priority(self, priority):
        refused("priority", channels=[{"provider": "a_channel", "priority": priority}])

    @pytest.mark.parametrize(
        "connection",
        [
            "elsewhere",
            "SAME_AS_SOURCE",
            {},
            {"settings": {}},
            {"spreadsheet_id": "x"},
            {"settings": "x"},
            ["same_as_source"],
            None,
            3,
        ],
    )
    def test_an_invalid_connection(self, connection):
        store = {**NAGA["store"], "connection": connection}
        with pytest.raises(ValidationError, match="connection"):
            spec(store=store)

    def test_settings_must_be_json_compatible(self):
        refused("settings", source={"provider": "some_source", "settings": {"x": object()}})
        refused("settings", source={"provider": "some_source", "settings": {"x": float("nan")}})
        refused("settings", source={"provider": "some_source", "settings": {1: "x"}})

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("client_id", "Bad Id"),
            ("client_id", ""),
            ("display_name", " "),
            ("timezone", "Mars/Base"),
            ("mode", "yolo"),
            ("daily_action_limit", 0),
            ("max_attempts", 21),
        ],
    )
    def test_the_general_settings_are_validated(self, name, value):
        refused(name, **{name: value})

    def test_the_schedule_and_polling_are_validated(self):
        refused("daily_at", schedule={"daily_at": "9:00"})
        refused("daily_at", schedule={"daily_at": 630})
        refused("days", schedule={"days": []})
        refused("days", schedule={"days": ["mon", "mon"]})
        refused("days", schedule={"days": ["funday"]})
        refused("prepare_every_minutes", polling={"prepare_every_minutes": 4})
        refused("dispatch_every_minutes", polling={"dispatch_every_minutes": 0})

    def test_the_inbound_policy_keeps_its_rules(self):
        refused(
            "requires spontaneous_conversations",
            inbound={
                "unknown_contacts": {"policy": "engage"},
                "spontaneous_conversations": {"enabled": False},
            },
        )


class TestImmutability:
    def test_models_cannot_be_assigned_to(self):
        naga = spec()
        for target, attribute, value in (
            (naga, "display_name", "Other"),
            (naga.brain, "agent", "other"),
            (naga.source, "provider", "other_source"),
            (naga.store, "connection", "same_as_source"),
            (naga.channels[0], "priority", 9),
            (naga.llm, "model", "other-model"),
            (naga.schedule, "daily_at", "10:00"),
            (naga.polling, "dispatch_every_minutes", 9),
            (naga.inbound, "unknown_contacts", None),
        ):
            with pytest.raises(ValidationError, match="frozen"):
                setattr(target, attribute, value)

    def test_collections_are_tuples(self):
        naga = spec()
        assert isinstance(naga.channels, tuple) and isinstance(naga.opt_out_keywords, tuple)
        assert isinstance(naga.schedule.days, tuple)

    def test_settings_are_read_only_all_the_way_down(self):
        naga = spec(
            source={"provider": "some_source", "settings": {"outer": {"inner": [1, 2]}, "top": 1}}
        )
        settings = naga.source.settings
        assert isinstance(settings, MappingProxyType)
        for mutate in (
            lambda: settings.__setitem__("top", 2),
            lambda: settings["outer"].__setitem__("inner", ()),
            lambda: settings.update(x=1),
        ):
            with pytest.raises((TypeError, AttributeError)):
                mutate()
        assert settings["outer"]["inner"] == (1, 2)

    def test_the_input_is_copied_not_shared(self):
        data = deepcopy(NAGA)
        result = ClientSpec.model_validate(data)
        data["source"]["settings"]["spreadsheet_id"] = "changed"
        data["channels"].append({"provider": "late_channel"})
        assert result.source.settings["spreadsheet_id"] == "sheet-naga"
        assert len(result.channels) == 1

    def test_equal_specs_are_equal_and_hashable_parts_are_stable(self):
        assert spec() == spec()
        assert spec() != spec(display_name="Other")
        assert spec().channels[0] == ChannelBinding.model_validate(NAGA["channels"][0])
        assert spec().llm == LLMBinding(model="gpt-lune")
        assert spec().source == ProviderBinding.model_validate(NAGA["source"])


class TestTheModuleIsProviderNeutralAndInert:
    SOURCE = Path(canonical.__file__).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "name",
        [
            "google_sheets",
            "sheets",
            "whatsapp",
            "openai",
            "meta",
            "airtable",
            "email",
            "telegram",
            "gpt",
            "muse",
        ],
    )
    def test_no_provider_or_model_is_named_in_the_module(self, name):
        assert not re.search(rf"\b{name}\b", self.SOURCE, flags=re.IGNORECASE), name

    def test_only_the_read_only_parallel_pipeline_imports_it(self):
        """Increment 5 added `resolve_client_spec`; increment 7 added the one boundary that turns a
        spec into what the runtime runs on (`runtime_config`). Nothing else imports it."""
        src = Path(canonical.__file__).resolve().parents[2]
        importers = [
            path.relative_to(src).as_posix()
            for path in src.rglob("*.py")
            if path.name != "canonical.py"
            and re.search(
                r"config\.canonical|from sales_app\.config import .*canonical",
                path.read_text(encoding="utf-8"),
            )
        ]
        assert importers == [
            "sales_app/config/runtime_config.py",
            "sales_app/config/spec_resolution.py",
        ]
