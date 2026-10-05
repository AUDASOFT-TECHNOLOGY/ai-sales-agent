"""The runtime fed by a canonical spec: the same behaviour as a legacy client, by every measure.

A client is described three ways and run: as the legacy files are read today (`resolve_client_config`),
as those same files read through the canonical spec, and as a canonical (v3) client.yaml of its own.
The three must give the same configuration, the same builder arguments, the same credentials, the
same channel, the same Brain and the same Engine. Then the new rules: what the runtime refuses to run
yet, and exactly one enabled channel.
"""

import json
import re
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

import app_spec_brains
import app_test_brains as brains
import pytest
import yaml
from app_support import VERIFY_TOKEN, client_yaml, env_for, fake_builders, make_settings, phone_id
from baseline_support import (
    engine_snapshot,
    fixture_json,
    fixture_text,
    install_recording_vendors,
    resolve,
    settings_secret_store,
    snapshot_config,
    store_with_document,
)
from factories import NOW

from sales_app.brain_registry import BrainRegistry, InstalledBrain
from sales_app.config import LoadedConfig, build_llm_registry, load_client_configs
from sales_app.config.normalize import normalize
from sales_app.config.runtime_config import (
    NO_CHANNEL,
    NO_CHANNEL_REASON,
    CanonicalClientConfig,
    MultipleChannelsError,
    NotRunnableError,
    channel_provider_key,
    client_config_from_spec,
    legacy_file_from_spec,
)
from sales_app.config.runtime_config import (
    resolve_canonical_client_config as resolve_with_brains,
)
from sales_app.config.spec_resolution import resolve_client_spec
from sales_app.errors import RuntimeBuildError
from sales_app.parsers import build_parsers
from sales_app.registry import ClientRegistry
from sales_app.wiring import ChannelProvider, IntegrationBuilders, default_builders
from sales_app.wiring import build_runtime as build_runtime_with_brains
from sales_app.wiring.providers import ChannelSupport, ProviderKind, ProviderRegistry
from sales_brain_kit import ConsultativeSalesSpec
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel
from sales_core.ports import InvalidSignatureError
from sales_integrations.fakes import FakeClock
from sales_integrations.whatsapp import compute_signature

APPROVAL, DRY, AUTO = ApprovalMode.APPROVAL, ApprovalMode.DRY_RUN, ApprovalMode.AUTONOMOUS
SRC = Path(__file__).resolve().parents[2] / "src"
FACTORY = "app_test_brains:good_brain"


@pytest.fixture(autouse=True)
def _reset_created():
    brains.CREATED.clear()


TEST_BRAINS = {FACTORY: "test_brain", "app_spec_brains:create_brain": "spec_brain"}


def installed_test_brain() -> BrainRegistry:
    """The suite's Brains by key: a plain one, and one that declares settings rules."""
    return BrainRegistry(
        [
            InstalledBrain("test_brain", brains.good_brain, None, "tests:good"),
            InstalledBrain(
                "spec_brain", app_spec_brains.create_brain, ConsultativeSalesSpec(), "tests:spec"
            ),
        ]
    )


def yaml_fixture(name: str) -> dict[str, Any]:
    return yaml.safe_load(fixture_text(name))


def minimal(**changes: Any) -> dict[str, Any]:
    data = yaml.safe_load(yaml.safe_dump(client_yaml("alpha")))
    data.update(changes)
    return data


SCENARIOS = {
    "complete_yaml": (yaml_fixture("legacy_complete.yaml"), None),
    "minimal_yaml": (minimal(), None),
    "yaml_without_whatsapp_references": (minimal(whatsapp={}), None),
    "wiring_plus_v1_document": (yaml_fixture("wiring_only.yaml"), fixture_json("doc_v1.json")),
    "wiring_plus_v2_document": (yaml_fixture("wiring_only.yaml"), fixture_json("doc_v2.json")),
    "wiring_plus_v2_upstream_id": (
        yaml_fixture("wiring_only.yaml"),
        fixture_json("doc_v2_upstream_id.json"),
    ),
}


def write(root: Path, raw: dict[str, Any], name: str = "alpha") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "client.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return directory


def canonical_yaml(root: Path, spec: dict[str, Any], name: str = "alpha") -> Path:
    return write(root / "canonical", spec, name)


def resolve_canonical_client_config(*args, **kwargs):
    """The canonical resolution with `test_brain` installed, as the suite's Brain."""
    return resolve_with_brains(*args, **{"brains": installed_test_brain(), **kwargs})


def build_runtime(*args, **kwargs):
    kwargs["brains"] = kwargs.get("brains") or installed_test_brain()
    return build_runtime_with_brains(*args, **kwargs)


def canonical_spec(raw: dict[str, Any], document=None) -> dict[str, Any]:
    """The canonical spec of a configuration, as a canonical file would write it: the suite's
    Brain is named by key (a canonical file may not carry a Python path)."""
    spec = deepcopy(normalize(raw, document).spec)
    agent = TEST_BRAINS.get(spec["brain"].get("legacy_factory"))
    if agent is not None:
        spec["brain"] = {**spec["brain"], "agent": agent, "legacy_factory": None}
    return spec


def three_ways(root: Path, raw, document, *, ceiling=AUTO, env=None, secret_store=None, **kwargs):
    """The legacy resolution, the canonical one over the same files, and the canonical one over a
    canonical client.yaml written from the same configuration."""
    env = env if env is not None else env_for("alpha")
    store = store_with_document("alpha", document) if document is not None else None
    legacy_dir = write(root / "legacy", raw)
    legacy = resolve_legacy(legacy_dir, env, ceiling, store, secret_store, **kwargs)
    through = resolve_canonical_client_config(
        legacy_dir, env=env, mode_ceiling=ceiling, store=store, secret_store=secret_store, **kwargs
    )
    canonical_dir = canonical_yaml(root, canonical_spec(raw, document))
    own = resolve_canonical_client_config(
        canonical_dir, env=env, mode_ceiling=ceiling, secret_store=secret_store, **kwargs
    )
    return legacy, through, own


def resolve_legacy(directory, env, ceiling, store, secret_store, **kwargs):
    from sales_app.config import resolve_client_config

    return resolve_client_config(
        directory,
        env=env,
        mode_ceiling=ceiling,
        store=store,
        secret_store=secret_store,
        **{k: v for k, v in kwargs.items() if k == "llm_registry"},
    )


ENV_FIELDS = {
    "sheets": ("credentials_env",),
    "llm": ("api_key_env",),
    "whatsapp": ("token_env", "phone_number_id_env", "app_secret_env"),
}


def without_env_names(spec) -> dict[str, Any]:
    """A legacy spec dump without its `*_env` names: a canonical file carries none."""
    data = spec.model_dump(mode="json")
    for section, names in ENV_FIELDS.items():
        for name in names:
            data[section].pop(name, None)
    return data


def full_store(*client_ids: str):
    """Every credential of the clients in the credential store, so no environment name is needed."""
    from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore, client_scope

    store = InMemorySecretStore()
    store.set(GLOBAL_SCOPE, "openai.api_key", "sk-test", by="t")
    store.set(GLOBAL_SCOPE, "whatsapp.app_secret", "secret-alpha", by="t")  # one Meta app, shared
    for client_id in client_ids or ("alpha",):
        store.set(client_scope(client_id), "whatsapp.access_token", f"token-{client_id}", by="t")
        store.set(client_scope(client_id), "whatsapp.phone_number_id", phone_id(client_id), by="t")
    return store


def run(config, monkeypatch, tmp_path, registry=None) -> dict[str, Any]:
    """Build the runtime with the real builders over recording vendors; what was passed where."""
    brains.CREATED.clear()
    recording = install_recording_vendors(monkeypatch)
    runtime = build_runtime(
        config, default_builders(make_settings(tmp_path)), FakeClock(NOW), brains=registry
    )
    return {
        "vendor_calls": recording.calls,
        "engine": engine_snapshot(runtime),
        "brain": [dict(settings) for _, settings in brains.CREATED],
        "brain_llm_is_the_built_provider": [llm in recording.llms for llm, _ in brains.CREATED],
    }


class TestACanonicalClientRunsLikeALegacyOne:
    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_the_configuration_is_the_same(self, name, tmp_path):
        raw, document = SCENARIOS[name]
        legacy, through, own = three_ways(tmp_path, raw, document)
        assert legacy.config is not None and through.config is not None and own.config is not None
        # read through the canonical spec: identical, down to the environment names and the secrets
        assert snapshot_config(through.config) == snapshot_config(legacy.config)
        # from a canonical file: the same, except the environment names it never carried
        own_spec, legacy_spec = (
            without_env_names(own.config.spec),
            without_env_names(legacy.config.spec),
        )
        # ...and the Brain, which a canonical file names by key where the legacy file has a path
        legacy_brain = legacy_spec.pop("brain")
        assert own_spec.pop("brain") == {
            **{k: v for k, v in legacy_brain.items() if k != "factory"},
            "agent": "test_brain",
        }
        assert own_spec == legacy_spec
        assert own.config.effective_mode == legacy.config.effective_mode

    @pytest.mark.parametrize("ceiling", [APPROVAL, DRY], ids=["live", "dry_run"])
    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_the_runtime_is_the_same(self, name, ceiling, tmp_path, monkeypatch):
        raw, document = SCENARIOS[name]
        legacy, through, own = three_ways(
            tmp_path, raw, document, ceiling=ceiling, secret_store=full_store()
        )
        expected = run(legacy.config, monkeypatch, tmp_path)
        assert run(through.config, monkeypatch, tmp_path) == expected
        assert run(own.config, monkeypatch, tmp_path) == expected

    def test_the_builders_receive_what_they_always_did(self, tmp_path, monkeypatch):
        raw, document = SCENARIOS["complete_yaml"]
        _, _, own = three_ways(tmp_path, raw, document, ceiling=APPROVAL, secret_store=full_store())
        calls = dict(run(own.config, monkeypatch, tmp_path)["vendor_calls"])
        assert calls["GoogleSheetsRepository"]["spreadsheet_id"] == "sheet-alpha"
        assert calls["GoogleSheetsRepository"]["tabs"]["prospects"] == "Leads"
        assert calls["GoogleSheetsRepository"]["column_mapping"] == {
            "prospects.phone": "Mobile",
            "prospects.display_name": "Name",
            "actions.draft_text": "Draft",
        }
        assert calls["OpenAIProvider"]["model"] == "gpt-upstream-1"
        assert calls["OpenAIProvider"]["api_key"] == "sk-test"
        assert calls["WhatsAppChannel"] == {
            "token": "token-alpha",
            "phone_number_id": "phone-alpha",
            "api_version": "v22.0",
        }

    def test_credentials_resolve_the_same_when_some_are_missing(self, tmp_path):
        for removed in (
            "OPENAI_API_KEY",
            "ALPHA_TOKEN",
            "ALPHA_PHONE_ID",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ):
            env = env_for("alpha")
            env.pop(removed)
            raw, document = SCENARIOS["complete_yaml"]
            legacy, through, _own = three_ways(
                tmp_path / removed, raw, document, ceiling=APPROVAL, env=env
            )
            assert not legacy.config.ready, removed
            assert through.config.missing_credentials == legacy.config.missing_credentials

    def test_credentials_from_the_store_resolve_the_same(self, tmp_path):
        from sales_app.secret_store import GLOBAL_SCOPE, client_scope

        secrets = settings_secret_store(openai__api_key="sk-store", openai__organization="org-1")
        secrets.set(client_scope("alpha"), "whatsapp.access_token", "tok-store", by="t")
        secrets.set(GLOBAL_SCOPE, "whatsapp.api_version", "v23.0", by="t")
        raw, document = SCENARIOS["complete_yaml"]
        legacy, through, own = three_ways(tmp_path, raw, document, secret_store=secrets)
        assert snapshot_config(through.config) == snapshot_config(legacy.config)
        assert snapshot_config(own.config)["secrets"]["openai.api_key"] == "sk-store"

    def test_the_deployments_model_allow_list_applies_the_same(self, tmp_path):
        registry = build_llm_registry({"openai": ["something-else"]})
        raw, document = SCENARIOS["complete_yaml"]
        legacy, through, own = three_ways(tmp_path, raw, document, llm_registry=registry)
        assert legacy.config is None and legacy.errors
        assert through.errors == legacy.errors and own.errors == legacy.errors

    def test_the_mode_ceiling_clamps_the_same(self, tmp_path):
        raw, document = SCENARIOS["complete_yaml"]
        legacy, through, own = three_ways(tmp_path, raw, document, ceiling=DRY)
        assert through.config.mode_clamped and own.config.mode_clamped == legacy.config.mode_clamped

    def test_it_is_a_client_config_that_remembers_its_spec(self, tmp_path):
        raw, document = SCENARIOS["complete_yaml"]
        _, through, own = three_ways(tmp_path, raw, document, secret_store=full_store())
        for resolution in (through, own):
            config = resolution.config
            assert isinstance(config, CanonicalClientConfig) and config.canonical is not None
            assert config.client_id == "alpha" and config.ready
        assert through.config.env_fallbacks["whatsapp.access_token"] == "ALPHA_TOKEN"
        assert own.config.env_fallbacks == {}  # a canonical file carries no environment names

    def test_a_legacy_load_still_gives_plain_client_configs(self, tmp_path):
        write(tmp_path, yaml_fixture("legacy_complete.yaml"))
        loaded = load_client_configs(tmp_path, env=env_for("alpha"), mode_ceiling=AUTO)
        assert type(loaded.clients[0]).__name__ == "ClientConfig"

    def test_the_brain_is_found_by_catalogue_key_and_is_the_same_brain(self, tmp_path, monkeypatch):
        raw, document = SCENARIOS["complete_yaml"]
        legacy, _, _ = three_ways(tmp_path, raw, document, ceiling=APPROVAL)
        by_key = resolve_canonical_client_config(
            tmp_path / "legacy" / "alpha",
            env=env_for("alpha"),
            mode_ceiling=APPROVAL,
            known_brains={FACTORY: "test_brain"},
            brains=installed_test_brain(),
        )
        assert by_key.config.canonical.brain.agent == "test_brain"
        # The key is resolved by the registry: it never became a Python path.
        assert by_key.config.spec.brain.agent == "test_brain"
        assert by_key.config.spec.brain.factory is None
        assert run(by_key.config, monkeypatch, tmp_path, installed_test_brain()) == run(
            legacy.config, monkeypatch, tmp_path
        )

    def test_a_brain_key_nobody_can_resolve_is_refused(self, tmp_path):
        spec = canonical_spec(minimal())
        spec["brain"] = {"agent": "unknown_brain", "settings": {}}
        result = resolve_canonical_client_config(
            canonical_yaml(tmp_path, spec), env=env_for("alpha"), mode_ceiling=AUTO
        )
        assert result.config is None
        assert result.errors == (
            "alpha: brain.agent: 'unknown_brain' is not a Brain this runtime can find",
        )

    def test_a_brains_own_settings_rules_now_apply_to_a_canonical_client_too(self, tmp_path):
        raw = minimal(brain={"factory": "app_spec_brains:create_brain", "settings": {"tone": "x"}})
        legacy, through, own = three_ways(tmp_path, raw, None)
        assert legacy.config is None and any("brain.settings" in e for e in legacy.errors)
        assert set(through.errors) == set(legacy.errors) == set(own.errors)

    def test_a_disabled_client_is_disabled_and_a_bad_one_has_the_same_errors(self, tmp_path):
        disabled = write(tmp_path / "d", {**minimal(), "enabled": False})
        result = resolve_canonical_client_config(disabled, env={}, mode_ceiling=AUTO)
        assert result.disabled and result.config is None and result.errors == ()
        bad = write(tmp_path / "b", minimal(timezone="Mars/Base"))
        legacy = resolve_legacy(bad, {}, AUTO, None, None)
        assert (
            resolve_canonical_client_config(bad, env={}, mode_ceiling=AUTO).errors == legacy.errors
        )


class TestWhatTheRuntimeCannotRunYet:
    def refuse(self, tmp_path, mutate, ceiling=AUTO):
        spec = canonical_spec(minimal())
        mutate(spec)
        result = resolve_canonical_client_config(
            canonical_yaml(tmp_path, spec), env=env_for("alpha"), mode_ceiling=ceiling
        )
        assert result.config is None
        return result.errors

    def test_a_store_kept_apart_from_the_source(self, tmp_path):
        def mutate(spec):
            spec["store"]["connection"] = {"settings": {"spreadsheet_id": "other"}}

        assert self.refuse(tmp_path, mutate) == (
            "alpha: store: a store kept apart from the source cannot run yet",
        )

    def test_a_store_of_another_provider(self, tmp_path):
        def mutate(spec):
            spec["store"]["provider"] = "another_store"

        (error,) = self.refuse(tmp_path, mutate)
        assert "cannot be kept with the source of 'google_sheets' yet" in error

    def test_a_model_named_only_by_a_catalogue_key(self, tmp_path):
        def mutate(spec):
            spec["llm"] = {"model": "muse-spark"}

        (error,) = self.refuse(tmp_path, mutate)
        assert "'muse-spark' (meta) has no upstream id configured" in error

    def test_a_key_beside_the_older_reference_runs_on_the_older_reference(self, tmp_path):
        spec = canonical_spec(minimal())
        spec["llm"]["model"] = "gpt-lune"
        result = resolve_canonical_client_config(
            canonical_yaml(tmp_path, spec), env=env_for("alpha"), mode_ceiling=AUTO
        )
        assert result.config.spec.llm.model == "test-model"

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            (("source", "settings", "base_id"), "source.settings.base_id: not understood"),
            (("store", "settings", "table"), "store.settings.table: not understood"),
            (("channels", 0, "settings", "from_address"), "settings.from_address: not understood"),
        ],
    )
    def test_settings_the_runtime_does_not_understand(self, tmp_path, path, message):
        def mutate(spec):
            target = spec
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = "x"

        (error,) = self.refuse(tmp_path, mutate)
        assert message in error

    def test_a_source_without_a_spreadsheet(self, tmp_path):
        def mutate(spec):
            del spec["source"]["settings"]["spreadsheet_id"]

        assert (
            "source.settings.spreadsheet_id: required by the runtime"
            in self.refuse(tmp_path, mutate)[0]
        )

    def test_a_column_mapped_by_both_roles(self, tmp_path):
        def mutate(spec):
            spec["source"]["settings"]["column_mapping"] = {"prospects.phone": "A"}
            spec["store"]["settings"]["column_mapping"] = {"prospects.phone": "B"}

        assert "mapped by both the source and the store" in self.refuse(tmp_path, mutate)[0]

    def test_every_reason_is_reported_at_once(self, tmp_path):
        def mutate(spec):
            spec["llm"] = {"model": "muse-spark"}
            spec["source"]["settings"]["base_id"] = "x"
            spec["brain"] = {"agent": "nobody"}

        assert len(self.refuse(tmp_path, mutate)) == 3

    def test_the_mapping_itself_is_a_pure_function(self):
        spec = resolve_client_spec_of(minimal())
        file = legacy_file_from_spec(spec, {"openai.api_key": "MY_KEY"}, allow_legacy_factory=True)
        assert file.llm.api_key_env == "MY_KEY" and file.sheets.provider == "google_sheets"
        with pytest.raises(NotRunnableError):
            legacy_file_from_spec(
                spec.model_copy(
                    update={
                        "brain": spec.brain.model_copy(
                            update={"legacy_factory": None, "agent": "x"}
                        )
                    }
                )
            )


def resolve_client_spec_of(raw: dict[str, Any]):
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        return resolve_client_spec(write(Path(folder), raw)).spec


class TestOneChannelRuns:
    def with_channels(self, tmp_path, channels, ceiling=APPROVAL, env=None):
        spec = canonical_spec(minimal())
        spec["channels"] = channels
        return resolve_canonical_client_config(
            canonical_yaml(tmp_path, spec),
            env=env if env is not None else env_for("alpha"),
            mode_ceiling=ceiling,
            secret_store=full_store(),
        )

    def test_no_channel_at_all_leaves_the_client_not_ready(self, tmp_path):
        result = self.with_channels(tmp_path, [])
        assert result.errors == () and not result.config.ready
        assert result.config.missing_credentials == (NO_CHANNEL_REASON,)
        assert result.config.spec.whatsapp.provider == NO_CHANNEL
        assert channel_provider_key(result.config) is None

    def test_only_disabled_channels_leave_the_client_not_ready(self, tmp_path):
        result = self.with_channels(
            tmp_path, [{"provider": "whatsapp", "enabled": False, "priority": 1}]
        )
        assert not result.config.ready and NO_CHANNEL_REASON in result.config.missing_credentials

    def test_a_dry_run_without_a_channel_is_not_ready_either(self, tmp_path):
        assert not self.with_channels(tmp_path, [], ceiling=DRY).config.ready

    def test_no_channel_means_the_runtime_cannot_be_built(self, tmp_path, monkeypatch):
        install_recording_vendors(monkeypatch)
        config = self.with_channels(tmp_path, []).config
        with pytest.raises(RuntimeBuildError) as raised:
            build_runtime(config, default_builders(make_settings(tmp_path)), FakeClock(NOW))
        assert str(raised.value) == f"alpha: {NO_CHANNEL_REASON}"

    def test_exactly_one_enabled_channel_is_fine(self, tmp_path):
        result = self.with_channels(tmp_path, [{"provider": "whatsapp", "priority": 1}])
        assert result.config.ready and channel_provider_key(result.config) == "whatsapp"

    def test_a_disabled_second_channel_is_ignored(self, tmp_path):
        channels = [
            {"provider": "whatsapp", "priority": 1},
            {"provider": "email", "enabled": False, "priority": 2},
        ]
        result = self.with_channels(tmp_path, channels)
        assert result.config.ready and channel_provider_key(result.config) == "whatsapp"

    def test_several_enabled_channels_are_refused_in_words(self, tmp_path):
        channels = [{"provider": "whatsapp", "priority": 1}, {"provider": "email", "priority": 2}]
        result = self.with_channels(tmp_path, channels)
        assert result.config is None
        assert result.errors == (
            "alpha: more than one channel is enabled (whatsapp, email); this version runs exactly one",
        )

    def test_the_provider_is_read_from_the_spec_not_from_a_legacy_section(self, tmp_path):
        result = self.with_channels(tmp_path, [{"provider": "another_channel"}])
        assert channel_provider_key(result.config) == "another_channel"
        assert result.config.spec.whatsapp.provider == "another_channel"

    def test_asking_a_config_with_several_channels_never_guesses(self, tmp_path):
        spec = canonical_spec(minimal())
        spec["channels"] = [{"provider": "whatsapp"}, {"provider": "email"}]
        config = CanonicalClientConfig(
            *_parts(self.with_channels(tmp_path / "x", [{"provider": "whatsapp"}]).config),
            canonical=resolve_client_spec_of_dict(spec),
        )
        with pytest.raises(MultipleChannelsError, match="whatsapp, email"):
            channel_provider_key(config)

    def test_an_unknown_channel_provider_is_refused_at_build_time(self, tmp_path, monkeypatch):
        install_recording_vendors(monkeypatch)
        config = self.with_channels(tmp_path, [{"provider": "email"}]).config
        with pytest.raises(RuntimeBuildError) as raised:
            build_runtime(config, default_builders(make_settings(tmp_path)), FakeClock(NOW))
        assert str(raised.value) == "alpha: unknown channel provider 'email'"


def _parts(config):
    return (config.spec, config.directory, config.effective_mode, config.secrets)


def resolve_client_spec_of_dict(spec: dict[str, Any]):

    from sales_app.config.canonical import ClientSpec

    return ClientSpec.model_validate(spec)


class TestRegistryAndParsersFollowTheChannel:
    def world(self, tmp_path, monkeypatch, *, canonical: bool, extra_clients=()):
        install_recording_vendors(monkeypatch)
        configs = []
        for client_id in ("alpha", "beta", *extra_clients):
            raw = client_yaml(client_id)
            raw = yaml.safe_load(yaml.safe_dump(raw))
            directory = write(tmp_path / ("canonical" if canonical else "legacy"), raw, client_id)
            if canonical:
                directory = canonical_yaml(tmp_path / "specs", canonical_spec_of(raw), client_id)
                result = resolve_canonical_client_config(
                    directory,
                    env=env_for(client_id),
                    mode_ceiling=APPROVAL,
                    secret_store=full_store("alpha", "beta", *extra_clients),
                )
            else:
                result = resolve_legacy(
                    directory,
                    env_for(client_id),
                    APPROVAL,
                    None,
                    full_store("alpha", "beta", *extra_clients),
                )
            assert result.config is not None, result.errors
            configs.append(result.config)
        builders = default_builders(make_settings(tmp_path))
        registry = ClientRegistry.build(
            LoadedConfig(tuple(configs), (), ()),
            builders,
            FakeClock(NOW),
            brains=installed_test_brain(),
        )
        return configs, builders, registry

    def test_inbound_routing_is_the_same(self, tmp_path, monkeypatch):
        answers = []
        for canonical in (False, True):
            _, _, registry = self.world(tmp_path / str(canonical), monkeypatch, canonical=canonical)
            answers.append(
                {
                    "alpha": registry.by_routing_key("whatsapp", phone_id("alpha")).client_id,
                    "beta": registry.by_routing_key("whatsapp", phone_id("beta")).client_id,
                    "unknown": registry.by_routing_key("whatsapp", "unknown"),
                    "other_provider": registry.by_routing_key("sms", phone_id("beta")),
                    "health": registry.health(),
                }
            )
        assert answers[0] == answers[1]
        assert answers[1]["alpha"] == "alpha" and answers[1]["beta"] == "beta"

    def test_a_client_without_a_channel_receives_nothing_and_is_listed_as_waiting(
        self, tmp_path, monkeypatch
    ):
        install_recording_vendors(monkeypatch)
        spec = canonical_spec(minimal())
        spec["channels"] = []
        config = resolve_canonical_client_config(
            canonical_yaml(tmp_path, spec),
            env=env_for("alpha"),
            mode_ceiling=APPROVAL,
            secret_store=full_store(),
        ).config
        builders = default_builders(make_settings(tmp_path))
        registry = ClientRegistry.build(
            LoadedConfig((config,), (), ()), builders, FakeClock(NOW), brains=installed_test_brain()
        )
        assert registry.by_routing_key("whatsapp", phone_id("alpha")) is None
        assert registry.not_ready_client_for("whatsapp", phone_id("alpha")) is None
        (entry,) = registry.health()
        assert entry["status"] == "not_ready"
        assert entry["missing_credentials"] == [NO_CHANNEL_REASON]
        assert build_parsers([config], builders) == {}

    def test_the_parsers_authenticate_each_account_the_same(self, tmp_path, monkeypatch):
        results = []
        for canonical in (False, True):
            configs, builders, _ = self.world(
                tmp_path / str(canonical), monkeypatch, canonical=canonical
            )
            parser = build_parsers(configs, builders)["whatsapp"]
            results.append(
                {
                    "alpha": attempt(parser, phone_id("alpha"), "secret-alpha"),
                    "alpha_with_another_secret": attempt(
                        parser, phone_id("alpha"), "another-secret"
                    ),
                    "beta": attempt(
                        parser, phone_id("beta"), "secret-alpha"
                    ),  # one Meta app signs for both
                    "unsigned": attempt(parser, phone_id("alpha"), None),
                    "handshake": parser.handshake(
                        {
                            "hub.mode": "subscribe",
                            "hub.verify_token": VERIFY_TOKEN,
                            "hub.challenge": "42",
                        }
                    ),
                }
            )
        assert results[0] == results[1]
        assert results[1]["alpha"] == {
            "events": [(phone_id("alpha"), f"wamid.{phone_id('alpha')}")]
        }

    def test_routing_key_and_signing_secret_come_from_the_descriptors_channel(
        self, tmp_path, monkeypatch
    ):
        configs, builders, _ = self.world(tmp_path, monkeypatch, canonical=True)
        provider = builders.channels["whatsapp"]
        assert isinstance(provider, ChannelProvider) and provider.kind is Channel.WHATSAPP
        assert provider.routing_key(configs[0]) == phone_id("alpha")
        assert provider.webhook_secret(configs[0]) == "secret-alpha"

    def test_a_fake_channel_provider_runs_a_canonical_client(self, tmp_path):
        base, made = fake_builders()
        builders = IntegrationBuilders(
            repositories=base.repositories,
            llms=base.llms,
            channels={"fake_channel": base.channels["whatsapp"]},
        )
        spec = canonical_spec(minimal())
        spec["channels"] = [{"provider": "fake_channel", "priority": 1}]
        config = resolve_canonical_client_config(
            canonical_yaml(tmp_path, spec),
            env=env_for("alpha"),
            mode_ceiling=APPROVAL,
            secret_store=full_store(),
        ).config
        runtime = build_runtime(config, builders, FakeClock(NOW))
        assert runtime.client_id == "alpha" and "alpha" in made.channels
        registry = ClientRegistry.build(
            LoadedConfig((config,), (), ()), builders, FakeClock(NOW), brains=installed_test_brain()
        )
        assert registry.by_routing_key("whatsapp", phone_id("alpha")) is None
        assert registry.by_routing_key("fake_channel", phone_id("alpha")).client_id == "alpha"


def canonical_spec_of(raw: dict[str, Any]) -> dict[str, Any]:
    return canonical_spec(raw)


def attempt(parser, phone, secret):
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": phone},
                                "messages": [
                                    {
                                        "from": "21612345678",
                                        "id": f"wamid.{phone}",
                                        "timestamp": "1768467600",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
    ).encode()
    headers = {"X-Hub-Signature-256": compute_signature(secret, body)} if secret else {}
    try:
        events = parser.parse(body, headers)
    except InvalidSignatureError as error:
        return {"rejected": str(error)}
    return {"events": [(e.routing_key, e.message.external_id) for e in events]}


class TestProvidersComeFromTheRegistry:
    def canonical_config(self, tmp_path, mutate=None):
        spec = canonical_spec(minimal())
        if mutate:
            mutate(spec)
        return resolve_canonical_client_config(
            canonical_yaml(tmp_path, spec),
            env=env_for("alpha"),
            mode_ceiling=APPROVAL,
            secret_store=full_store(),
        ).config

    def test_the_known_runnable_providers(self, tmp_path):
        builders = default_builders(make_settings(tmp_path))
        assert set(builders.repositories) == {"google_sheets"}
        assert set(builders.llms) == {"openai"}
        assert set(builders.channels) == {"whatsapp"}

    def test_a_declared_provider_that_cannot_run_is_refused_like_an_unknown_one(
        self, tmp_path, monkeypatch
    ):
        install_recording_vendors(monkeypatch)

        def mutate(spec):
            spec["llm"] = {"legacy": {"provider": "meta", "model_id": "muse-spark-1.3"}}

        config = self.canonical_config(tmp_path, mutate)
        with pytest.raises(
            RuntimeBuildError, match="alpha: llm: alpha: unknown llm provider 'meta'"
        ):
            build_runtime(config, default_builders(make_settings(tmp_path)), FakeClock(NOW))
        assert "meta" not in default_builders(make_settings(tmp_path)).llms

    def test_an_unknown_source_provider(self, tmp_path, monkeypatch):
        install_recording_vendors(monkeypatch)

        def mutate(spec):
            spec["source"]["provider"] = "airtable"
            spec["store"]["provider"] = "airtable"

        config = self.canonical_config(tmp_path, mutate)
        with pytest.raises(RuntimeBuildError, match="unknown sheets provider 'airtable'"):
            build_runtime(config, default_builders(make_settings(tmp_path)), FakeClock(NOW))

    def test_fake_providers_from_a_registry_run_a_canonical_client(self, tmp_path):
        made = {}

        def build(name):
            def factory(config):
                made[name] = config.client_id
                base, _ = fake_builders()
                return {
                    "repository": base.repositories["google_sheets"],
                    "llm": base.llms["openai"],
                    "channel": base.channels["whatsapp"].build,
                }[name](config)

            return factory

        registry = ProviderRegistry()
        registry.register_fake("fake_source", ProviderKind.SOURCE, build=build("repository"))
        registry.register_fake("fake_llm", ProviderKind.LLM, build=build("llm"))
        registry.register_fake(
            "fake_channel",
            ProviderKind.CHANNEL,
            build=build("channel"),
            channel=ChannelSupport(
                kind="whatsapp",
                routing_key=lambda config: config.phone_number_id,
                webhook_secret=lambda config: None,
                build_parser=lambda secrets, token: None,
            ),
        )

        def mutate(spec):
            spec["source"]["provider"] = "fake_source"
            spec["store"]["provider"] = "fake_source"
            spec["llm"] = {"legacy": {"provider": "fake_llm", "model_id": "m"}}
            spec["channels"] = [{"provider": "fake_channel", "priority": 1}]

        config = self.canonical_config(tmp_path, mutate)
        builders = default_builders(make_settings(tmp_path), registry=registry)
        runtime = build_runtime(config, builders, FakeClock(NOW))
        assert runtime.client_id == "alpha"
        assert made == {"repository": "alpha", "llm": "alpha", "channel": "alpha"}

    def test_the_wiring_never_compares_a_provider_to_a_name(self):
        files = [
            SRC / "sales_app" / "wiring" / "runtime.py",
            SRC / "sales_app" / "wiring" / "integrations.py",
            SRC / "sales_app" / "registry.py",
            SRC / "sales_app" / "parsers.py",
            SRC / "sales_app" / "config" / "runtime_config.py",
        ]
        pattern = re.compile(r"(provider|channel_key|\.key)\s*[!=]=\s*[\"']")
        for path in files:
            assert not pattern.search(path.read_text(encoding="utf-8")), path.name


class TestTheLegacyLoadPathIsUntouched:
    def test_nothing_that_loads_configuration_reads_the_canonical_boundary(self):
        for name in (
            "config/loader.py",
            "config/resolution.py",
            "bootstrap.py",
            "runtime_manager.py",
            "scheduler.py",
            "cli.py",
            "config_cli.py",
        ):
            text = (SRC / "sales_app" / name).read_text(encoding="utf-8")
            assert "runtime_config" not in text, name
        for path in (SRC / "sales_app" / "api").glob("*.py"):
            assert "runtime_config" not in path.read_text(encoding="utf-8"), path.name

    def test_the_runtime_reads_the_channel_only_through_the_one_accessor(self):
        for name in ("wiring/runtime.py", "registry.py", "parsers.py"):
            text = (SRC / "sales_app" / name).read_text(encoding="utf-8")
            assert "whatsapp.provider" not in text, name
            assert "channel_provider_key" in text, name

    def test_a_legacy_config_answers_with_its_whatsapp_section(self, tmp_path):
        write(tmp_path, minimal())
        config = load_client_configs(tmp_path, env=env_for("alpha"), mode_ceiling=AUTO).clients[0]
        assert channel_provider_key(config) == "whatsapp"
        assert channel_provider_key(resolve(tmp_path).config) == "whatsapp"

    def test_the_stagger_is_still_what_the_registry_gives(self, tmp_path, monkeypatch):
        install_recording_vendors(monkeypatch)
        write(tmp_path, minimal())
        config = resolve(tmp_path, ceiling=APPROVAL).config
        runtime = build_runtime(
            config,
            default_builders(make_settings(tmp_path)),
            FakeClock(NOW),
            stagger=timedelta(seconds=7),
        )
        assert runtime.stagger.total_seconds() == 7


def test_client_config_from_spec_passes_a_disabled_or_failed_resolution_through(tmp_path):
    disabled = write(tmp_path / "d", {**minimal(), "enabled": False})
    resolved = resolve_client_spec(disabled)
    assert client_config_from_spec(resolved, disabled, env={}, mode_ceiling=AUTO).disabled
    broken = write(tmp_path / "b", minimal(timezone="Mars/Base"))
    result = client_config_from_spec(resolve_client_spec(broken), broken, env={}, mode_ceiling=AUTO)
    assert result.config is None and result.errors
