"""The model catalog: models by stable key, their provider, and upstream ids from deployment data."""

import inspect
from pathlib import Path
from typing import Any

import app_test_brains as brains
import pytest
import yaml
from app_support import client_yaml, env_for, make_settings
from baseline_support import install_recording_vendors
from factories import NOW

from sales_app.brain_registry import BrainRegistry, InstalledBrain
from sales_app.config.normalize import normalize
from sales_app.config.runtime_config import legacy_file_from_spec, resolve_canonical_client_config
from sales_app.config.spec_resolution import provider_problems, resolve_client_spec
from sales_app.model_catalog import (
    ModelCatalog,
    ModelCatalogError,
    ModelDescriptor,
    ModelNotConfiguredError,
    UnknownModelError,
)
from sales_app.settings import Settings
from sales_app.wiring import build_runtime, default_builders
from sales_app.wiring.providers import (
    ProviderDescriptor,
    ProviderKind,
    ProviderRegistry,
    default_registry,
)
from sales_core.approval import ApprovalMode
from sales_integrations.fakes import FakeClock

DRY = ApprovalMode.DRY_RUN
BRAIN = BrainRegistry([InstalledBrain("test_brain", brains.good_brain, None, "tests:good")])


def catalog(**upstream: str) -> ModelCatalog:
    return ModelCatalog.from_registry(default_registry(), upstream)


class TestWhatTheProvidersDeclare:
    def test_openai_serves_gpt_lune_and_gpt_sol(self):
        models = {m.key: m for m in catalog().models_of("openai")}
        assert set(models) == {"gpt-lune", "gpt-sol"}
        assert catalog().provider_of("gpt-lune") == catalog().provider_of("gpt-sol") == "openai"

    def test_meta_serves_muse_spark_and_it_is_not_an_openai_model(self):
        assert catalog().provider_of("muse-spark") == "meta"
        assert "muse-spark" not in {m.key for m in catalog().models_of("openai")}

    def test_openai_and_meta_stay_two_providers_despite_one_adapter_family(self):
        registry = default_registry()
        openai, meta = registry.require("openai"), registry.require("meta")
        assert openai.family == meta.family == "openai_compatible"
        assert openai.key != meta.key and openai.models and meta.models
        assert not {m.key for m in openai.models} & {m.key for m in meta.models}

    def test_no_descriptor_declares_an_upstream_id(self):
        for descriptor in default_registry():
            assert all(model.upstream_id is None for model in descriptor.models)
        assert all(not m.configured for m in catalog().models_of("openai"))

    def test_a_provider_may_only_declare_its_own_models_and_only_if_it_is_a_model_provider(self):
        with pytest.raises(ValueError, match="another provider"):
            ProviderDescriptor(
                key="a_llm",
                kind=ProviderKind.LLM,
                label="A",
                models=(ModelDescriptor("some-model", "b_llm", "Some"),),
            )
        with pytest.raises(ValueError, match="only a model provider"):
            ProviderDescriptor(
                key="a_source",
                kind=ProviderKind.SOURCE,
                label="A",
                models=(ModelDescriptor("some-model", "a_source", "Some"),),
            )


class TestTheCatalog:
    def test_an_unknown_model_is_refused_and_the_known_ones_are_listed(self):
        with pytest.raises(UnknownModelError, match=r"unknown model 'nobody'.*gpt-lune"):
            catalog().get("nobody")
        assert "nobody" not in catalog() and catalog().find("nobody") is None

    def test_a_model_without_an_upstream_id_exists_but_cannot_run(self):
        assert catalog().get("gpt-lune").configured is False
        with pytest.raises(ModelNotConfiguredError, match="no upstream id configured"):
            catalog().runnable("gpt-lune")

    def test_an_upstream_id_given_by_deployment_data_makes_it_runnable(self):
        found = catalog(**{"gpt-lune": "up-lune-1"}).runnable("gpt-lune")
        assert (found.provider, found.upstream_id) == ("openai", "up-lune-1")
        with pytest.raises(ModelNotConfiguredError):  # its sibling has none: nothing is deduced
            catalog(**{"gpt-lune": "up-lune-1"}).runnable("gpt-sol")

    def test_an_upstream_id_for_a_model_nobody_declares_is_an_error(self):
        with pytest.raises(ModelCatalogError, match="unknown models"):
            catalog(**{"gpt-luna": "x"})

    @pytest.mark.parametrize("bad", ["", " x", "x y", "-x", "a" * 200])
    def test_an_upstream_id_must_be_a_plain_identifier(self, bad):
        with pytest.raises(ModelCatalogError):
            catalog(**{"gpt-lune": bad})

    def test_a_model_declared_twice_is_refused(self):
        one = ModelDescriptor("some-model", "a_llm", "Some")
        with pytest.raises(ModelCatalogError, match="declared twice"):
            ModelCatalog([one, ModelDescriptor("some-model", "b_llm", "Other")])

    def test_a_catalog_is_injectable_from_a_fake_registry(self):
        registry = ProviderRegistry()
        registry.register_fake(
            "fake_llm",
            ProviderKind.LLM,
            models=(ModelDescriptor("fake-model", "fake_llm", "Fake"),),
        )
        fake = ModelCatalog.from_registry(registry, {"fake-model": "up-1"})
        assert fake.keys() == ("fake-model",) and fake.provider_of("fake-model") == "fake_llm"

    def test_the_deployment_setting_carries_the_upstream_ids(self):
        settings = Settings(model_upstream_ids={"muse-spark": "up-muse"})
        built = ModelCatalog.from_registry(default_registry(), settings.model_upstream_ids)
        assert built.runnable("muse-spark").upstream_id == "up-muse"
        assert Settings().model_upstream_ids == {}


def canonical(llm: dict[str, Any]) -> dict[str, Any]:
    spec = yaml.safe_load(yaml.safe_dump(normalize(client_yaml("alpha"), None).spec))
    spec["brain"] = {"agent": "test_brain", "settings": {}}
    spec["llm"] = llm
    return spec


def write(tmp_path: Path, spec: dict[str, Any]) -> Path:
    directory = tmp_path / "alpha"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "client.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return directory


def resolve(tmp_path, llm, **kwargs):
    return resolve_canonical_client_config(
        write(tmp_path, canonical(llm)),
        env=env_for("alpha"),
        mode_ceiling=DRY,
        brains=BRAIN,
        **kwargs,
    )


class TestValidatingTheCanonicalModel:
    def spec_problems(self, tmp_path, llm, **kwargs):
        result = resolve_client_spec(write(tmp_path, canonical(llm)), **kwargs)
        assert result.errors == ()
        return result.provider_problems

    def test_a_known_key_is_fine_without_any_upstream_id(self, tmp_path):
        assert self.spec_problems(tmp_path, {"model": "gpt-lune"}) == ()
        assert self.spec_problems(tmp_path / "m", {"model": "muse-spark"}) == ()

    def test_an_unknown_key_is_reported_by_the_resolution(self, tmp_path):
        assert self.spec_problems(tmp_path, {"model": "gpt-nothing"}) == (
            "alpha: llm.model: unknown model 'gpt-nothing'",
        )

    def test_a_legacy_reference_of_another_provider_contradicts_the_key(self, tmp_path):
        llm = {"model": "muse-spark", "legacy": {"provider": "openai", "model_id": "x"}}
        (problem,) = self.spec_problems(tmp_path, llm)
        assert "llm.legacy.provider: 'openai' is not 'meta'" in problem

    def test_a_model_whose_provider_is_not_a_model_provider_is_refused(self, tmp_path):
        registry = default_registry()
        rogue = ModelCatalog([ModelDescriptor("rogue-model", "google_sheets", "Rogue")])
        (problem,) = self.spec_problems(
            tmp_path, {"model": "rogue-model"}, registry=registry, catalog=rogue
        )
        assert "provider 'google_sheets' of 'rogue-model' is not a model provider" in problem
        unknown = ModelCatalog([ModelDescriptor("rogue-model", "no_such_one", "Rogue")])
        (problem,) = self.spec_problems(
            tmp_path / "u", {"model": "rogue-model"}, registry=registry, catalog=unknown
        )
        assert "'no_such_one' of 'rogue-model' is not a model provider" in problem

    def test_the_check_takes_no_secret_and_no_environment(self):
        assert set(inspect.signature(provider_problems).parameters) == {
            "spec",
            "registry",
            "catalog",
        }


class TestTheRuntimeRunsAModelThroughTheCatalog:
    def test_a_key_resolves_to_its_provider_and_upstream_id(self, tmp_path, monkeypatch):
        recording = install_recording_vendors(monkeypatch)
        result = resolve(tmp_path, {"model": "gpt-sol"}, catalog=catalog(**{"gpt-sol": "up-sol-9"}))
        assert result.errors == ()
        assert (result.config.spec.llm.provider, result.config.spec.llm.model) == (
            "openai",
            "up-sol-9",
        )
        build_runtime(
            result.config, default_builders(make_settings(tmp_path)), FakeClock(NOW), brains=BRAIN
        )
        assert dict(recording.calls)["OpenAIProvider"]["model"] == "up-sol-9"

    def test_a_key_without_an_upstream_id_is_not_runnable_and_says_why(self, tmp_path):
        result = resolve(tmp_path, {"model": "gpt-lune"})
        assert result.config is None
        assert result.errors == (
            (
                "alpha: llm.model: model 'gpt-lune' (openai) has no upstream id configured, "
                "so it cannot run"
            ),
        )

    def test_nothing_is_deduced_from_a_sibling_or_a_name(self, tmp_path):
        result = resolve(tmp_path, {"model": "gpt-lune"}, catalog=catalog(**{"gpt-sol": "up-s"}))
        assert result.config is None and "gpt-lune" in result.errors[0]

    def test_meta_models_keep_the_provider_meta_and_the_runtime_refuses_it_for_now(
        self, tmp_path, monkeypatch
    ):
        install_recording_vendors(monkeypatch)
        result = resolve(tmp_path, {"model": "muse-spark"}, catalog=catalog(**{"muse-spark": "m1"}))
        assert result.config.spec.llm.provider == "meta"  # never rewritten to openai
        with pytest.raises(Exception, match="unknown llm provider 'meta'"):
            build_runtime(
                result.config,
                default_builders(make_settings(tmp_path)),
                FakeClock(NOW),
                brains=BRAIN,
            )

    def test_an_unknown_key_is_refused(self, tmp_path):
        result = resolve(tmp_path, {"model": "gpt-nothing"})
        assert result.errors == ("alpha: llm.model: unknown model 'gpt-nothing'",)

    def test_a_key_beside_a_legacy_reference_prefers_the_catalog_when_configured(self, tmp_path):
        llm = {"model": "gpt-lune", "legacy": {"provider": "openai", "model_id": "old-id"}}
        configured = resolve(tmp_path, llm, catalog=catalog(**{"gpt-lune": "new-id"}))
        assert configured.config.spec.llm.model == "new-id"
        fallback = resolve(tmp_path / "f", llm)
        assert fallback.config.spec.llm.model == "old-id"  # as before the catalog existed

    def test_a_legacy_reference_alone_runs_exactly_as_before(self, tmp_path):
        llm = {"legacy": {"provider": "openai", "model_id": "whatever-id"}}
        result = resolve(tmp_path, llm)
        assert (result.config.spec.llm.provider, result.config.spec.llm.model) == (
            "openai",
            "whatever-id",
        )

    def test_the_pure_mapping_takes_the_catalog_as_a_parameter(self, tmp_path):
        spec = resolve_client_spec(write(tmp_path, canonical({"model": "gpt-lune"}))).spec
        with pytest.raises(Exception, match="no upstream id"):
            legacy_file_from_spec(spec, brains=BRAIN)
        file = legacy_file_from_spec(spec, brains=BRAIN, catalog=catalog(**{"gpt-lune": "u1"}))
        assert (file.llm.provider, file.llm.model) == ("openai", "u1")
