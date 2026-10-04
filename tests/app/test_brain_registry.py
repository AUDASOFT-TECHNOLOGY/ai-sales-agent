"""Brains found by key through package entry points, and never by a path a client wrote."""

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import app_test_brains as brains
import pytest
import yaml
from app_support import client_yaml, env_for, make_settings
from baseline_support import install_recording_vendors
from factories import NOW

from sales_app.brain_registry import (
    BRAIN_KEY_PATTERN,
    ENTRY_POINT_GROUP,
    BrainLoadError,
    BrainNotInstalledError,
    BrainRegistry,
    InstalledBrain,
    discover_brains,
    installed_brains,
)
from sales_app.config.brain_settings import prepare_brain_settings
from sales_app.config.normalize import normalize
from sales_app.config.runtime_config import resolve_canonical_client_config
from sales_app.config.schema import ClientFile
from sales_app.errors import RuntimeBuildError
from sales_app.wiring import build_runtime, default_builders
from sales_brain_kit import BrainDescriptor, ConsultativeSalesSpec
from sales_core.approval import ApprovalMode
from sales_integrations.fakes import FakeClock, FakeLLMProvider

AUTO, DRY = ApprovalMode.AUTONOMOUS, ApprovalMode.DRY_RUN
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class FakeEntryPoint:
    name: str
    value: str
    target: Any = None
    error: Exception | None = None

    def load(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.target


def descriptor(key="test_brain", factory=brains.good_brain, spec=None):
    return BrainDescriptor(key=key, factory=factory, settings_spec=spec)


def entry(key="test_brain", target=None, value=None):
    return FakeEntryPoint(key, value or f"pkg.{key}:BRAIN", target or descriptor(key))


@pytest.fixture(autouse=True)
def _reset_created():
    brains.CREATED.clear()


@pytest.fixture
def import_spy(monkeypatch):
    """Every `importlib.import_module` call made while the test runs."""
    calls: list[str] = []
    real = importlib.import_module

    def spy(name, *args, **kwargs):
        calls.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", spy)
    return calls


class TestDiscovery:
    def test_installed_entry_points_become_brains_found_by_key(self):
        registry = discover_brains([entry("alpha_brain"), entry("beta_brain")])
        assert registry.keys() == ("alpha_brain", "beta_brain")
        assert registry.get("alpha_brain").factory is brains.good_brain
        assert registry.problems() == {}

    def test_the_settings_spec_comes_from_the_descriptor(self):
        spec = ConsultativeSalesSpec()
        registry = discover_brains([entry("test_brain", descriptor(spec=spec))])
        assert registry.get("test_brain").settings_spec is spec

    def test_a_brain_is_built_with_the_llm_and_settings_and_checked(self):
        registry = discover_brains([entry()])
        llm, settings = FakeLLMProvider(), {"tone": "warm"}
        assert registry.create("test_brain", llm, settings).decide is not None
        assert brains.CREATED == [(llm, settings)]

    @pytest.mark.parametrize(
        ("factory", "message"),
        [(brains.raising_brain, "failed while building"), (brains.not_a_brain, "no decide")],
    )
    def test_a_brain_that_fails_or_is_not_a_brain_is_a_load_error(self, factory, message):
        registry = discover_brains([entry("test_brain", descriptor(factory=factory))])
        with pytest.raises(BrainLoadError, match=message):
            registry.create("test_brain", FakeLLMProvider(), {})

    def test_an_unknown_brain_says_what_is_installed(self):
        registry = discover_brains([entry("alpha_brain")])
        with pytest.raises(BrainNotInstalledError, match=r"no Brain 'nobody'.*alpha_brain"):
            registry.get("nobody")
        assert registry.problem_for("nobody") and registry.problem_for("alpha_brain") is None

    def test_no_entry_point_at_all_is_an_empty_registry(self):
        with pytest.raises(BrainNotInstalledError, match="installed: none"):
            discover_brains([]).get("alpha_brain")

    def test_the_same_key_declared_twice_makes_it_unusable_not_first_wins(self):
        registry = discover_brains(
            [entry("test_brain", value="one.pkg:BRAIN"), entry("test_brain", value="two.pkg:BRAIN")]
        )
        assert registry.find("test_brain") is None
        with pytest.raises(
            BrainLoadError, match=r"more than once \(one.pkg:BRAIN, two.pkg:BRAIN\)"
        ):
            registry.get("test_brain")

    def test_the_same_entry_point_listed_twice_is_not_a_duplicate(self):
        assert discover_brains([entry(), entry()]).keys() == ("test_brain",)

    def test_a_duplicate_does_not_hide_the_other_brains(self):
        registry = discover_brains(
            [entry("test_brain", value="a:B"), entry("test_brain", value="b:B"), entry("other_one")]
        )
        assert registry.keys() == ("other_one",)

    @pytest.mark.parametrize("key", ["Naga", "os.path:join", "../x", "a", "1abc", "has space"])
    def test_an_invalid_key_is_unusable(self, key):
        registry = discover_brains([FakeEntryPoint(key, "pkg:BRAIN", descriptor("test_brain"))])
        assert registry.keys() == () and "not a valid Brain key" in registry.problems()[key]

    def test_a_broken_entry_point_is_reported_and_the_others_still_work(self):
        broken = FakeEntryPoint("broken_one", "pkg.gone:BRAIN", error=ImportError("no module"))
        registry = discover_brains([broken, entry("fine_one")])
        assert registry.keys() == ("fine_one",)
        with pytest.raises(BrainLoadError, match="unusable: cannot load 'pkg.gone:BRAIN'"):
            registry.get("broken_one")

    @pytest.mark.parametrize(
        ("target", "message"),
        [
            (object(), "not a Brain descriptor"),
            (descriptor("another_key"), "declares key 'another_key'"),
            (
                type("D", (), {"key": "test_brain", "factory": print, "settings_spec": 42})(),
                "not a BrainSettingsSpec",
            ),
        ],
    )
    def test_a_descriptor_of_the_wrong_shape_is_unusable(self, target, message):
        registry = discover_brains([FakeEntryPoint("test_brain", "pkg:BRAIN", target)])
        assert message in registry.problems()["test_brain"]

    def test_a_registry_refuses_to_hold_a_key_twice(self):
        brain = InstalledBrain("test_brain", brains.good_brain, None, "x")
        with pytest.raises(ValueError, match="registered twice"):
            BrainRegistry([brain, brain])


class TestTheInstalledBrains:
    def test_naga_is_really_discovered_through_its_entry_point(self):
        registry = installed_brains()
        installed = registry.get("naga")
        assert installed.entry_point == "naga_brain.brain:BRAIN"
        assert installed.settings_spec is not None
        assert installed.settings_spec.family == "consultative_sales"
        assert registry.problems() == {}

    def test_tounesimmo_is_not_installed_as_a_brain_yet(self):
        with pytest.raises(BrainNotInstalledError):
            installed_brains().get("tounesimmo")

    def test_the_group_and_the_key_pattern_are_the_documented_ones(self):
        assert ENTRY_POINT_GROUP == "revansa.brains"
        assert BRAIN_KEY_PATTERN == r"^[a-z][a-z0-9_-]{1,63}$"

    def test_the_installed_naga_builds_through_the_registry_and_validates_its_settings(self):
        installed = installed_brains().get("naga")
        problems = installed.settings_spec.validate({}, 1)
        assert problems  # Naga's settings are still empty: the spec says what is missing
        with pytest.raises(BrainLoadError, match="failed while building"):
            installed_brains().create("naga", FakeLLMProvider(), {})


def canonical(brain: dict[str, Any], **changes: Any) -> dict[str, Any]:
    spec = yaml.safe_load(yaml.safe_dump(normalize(client_yaml("alpha"), None).spec))
    spec["brain"] = brain
    spec.update(changes)
    return spec


def resolve(tmp_path, spec, registry, **kwargs):
    directory = tmp_path / "alpha"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "client.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return resolve_canonical_client_config(
        directory, env=env_for("alpha"), mode_ceiling=DRY, brains=registry, **kwargs
    )


def build(config, tmp_path, registry):
    install = pytest.MonkeyPatch()
    try:
        install_recording_vendors(install)
        return build_runtime(
            config, default_builders(make_settings(tmp_path)), FakeClock(NOW), brains=registry
        )
    finally:
        install.undo()


class TestACanonicalClientNamesItsBrainByKey:
    def test_the_agent_key_resolves_through_the_registry_without_any_import(
        self, tmp_path, import_spy
    ):
        registry = discover_brains([entry("test_brain")])
        result = resolve(
            tmp_path, canonical({"agent": "test_brain", "settings": {"tone": "x"}}), registry
        )
        assert result.errors == () and result.config is not None
        assert result.config.spec.brain.agent == "test_brain"
        assert result.config.spec.brain.factory is None  # the key never became a path
        build(result.config, tmp_path, registry)
        assert [dict(s) for _, s in brains.CREATED] == [{"tone": "x"}]
        assert not [name for name in import_spy if "test_brain" in name or "app_test" in name]

    def test_a_brain_nobody_installed_is_refused(self, tmp_path):
        result = resolve(tmp_path, canonical({"agent": "nobody", "settings": {}}), BrainRegistry())
        assert result.config is None
        assert result.errors == (
            "alpha: brain.agent: 'nobody' is not a Brain this runtime can find",
        )

    def test_a_brain_whose_entry_point_is_broken_is_refused(self, tmp_path):
        broken = FakeEntryPoint("test_brain", "pkg:BRAIN", error=ImportError("boom"))
        result = resolve(
            tmp_path, canonical({"agent": "test_brain", "settings": {}}), discover_brains([broken])
        )
        assert result.config is None and "not a Brain this runtime can find" in result.errors[0]

    def test_the_brains_settings_rules_come_from_its_descriptor(self, tmp_path):
        registry = discover_brains([entry("test_brain", descriptor(spec=ConsultativeSalesSpec()))])
        result = resolve(
            tmp_path, canonical({"agent": "test_brain", "settings": {"tone": "x"}}), registry
        )
        assert result.config is None
        assert any("brain.settings" in error for error in result.errors)

    def test_naga_settings_are_validated_by_the_installed_naga_descriptor(self, tmp_path):
        result = resolve(tmp_path, canonical({"agent": "naga", "settings": {}}), None)
        assert result.config is None
        assert any("brain.settings" in error for error in result.errors)

    def test_prepare_settings_uses_the_descriptor_for_an_agent(self):
        registry = discover_brains([entry("test_brain", descriptor(spec=ConsultativeSalesSpec()))])
        legacy = ClientFile.model_validate(
            {**client_yaml("alpha"), "brain": {"agent": "test_brain", "settings": {}}}
        )
        _, problems = prepare_brain_settings(legacy, require_module=True, brains=registry)
        assert problems and all(p.startswith("brain.settings.") for p in problems)
        opaque = discover_brains([entry("test_brain")])
        assert prepare_brain_settings(legacy, require_module=True, brains=opaque) == (legacy, ())

    def test_building_a_runtime_reports_a_brain_that_fails_with_its_stage(self, tmp_path):
        registry = discover_brains([entry("test_brain", descriptor(factory=brains.raising_brain))])
        result = resolve(tmp_path, canonical({"agent": "test_brain", "settings": {}}), registry)
        with pytest.raises(RuntimeBuildError, match="alpha: brain: .*failed while building"):
            build(result.config, tmp_path, registry)


class TestAClientCannotMakeTheRuntimeImportAnything:
    @pytest.mark.parametrize(
        "agent", ["os.path:join", "evil.module:fn", "../x", "naga_brain.brain"]
    )
    def test_a_python_path_is_not_a_brain_key(self, tmp_path, import_spy, agent):
        result = resolve(tmp_path, canonical({"agent": agent, "settings": {}}), discover_brains([]))
        assert result.config is None and result.errors
        assert not [n for n in import_spy if n.split(".")[0] in {"evil", "os", "naga_brain"}]

    def test_a_canonical_file_cannot_name_a_python_factory(self, tmp_path, import_spy):
        spec = canonical({"legacy_factory": "evil_module:build", "settings": {}})
        result = resolve(tmp_path, spec, discover_brains([]))
        assert result.config is None
        assert "not accepted from a canonical configuration" in result.errors[0]
        assert "evil_module" not in import_spy

    def test_a_canonical_file_cannot_name_a_factory_even_with_a_known_brains_table(
        self, tmp_path, import_spy
    ):
        spec = canonical({"legacy_factory": "app_test_brains:good_brain", "settings": {}})
        result = resolve(tmp_path, spec, discover_brains([]))
        assert result.config is None and not brains.CREATED
        assert "app_test_brains" not in import_spy

    def test_a_key_is_never_turned_into_a_path_even_when_a_module_of_that_name_exists(
        self, tmp_path, import_spy
    ):
        # `app_test_brains` is importable; as a key it must still be unknown to the registry.
        result = resolve(
            tmp_path, canonical({"agent": "app_test_brains", "settings": {}}), discover_brains([])
        )
        assert result.config is None and "not a Brain this runtime can find" in result.errors[0]
        assert "app_test_brains" not in import_spy

    def test_the_operators_legacy_factory_still_runs(self, tmp_path):
        directory = tmp_path / "alpha"
        directory.mkdir()
        (directory / "client.yaml").write_text(
            yaml.safe_dump(client_yaml("alpha")), encoding="utf-8"
        )
        result = resolve_canonical_client_config(
            directory, env=env_for("alpha"), mode_ceiling=AUTO, brains=discover_brains([])
        )
        assert result.errors == ()
        assert result.config.spec.brain.factory == "app_test_brains:good_brain"
        assert result.config.spec.brain.agent is None
        build(result.config, tmp_path, None)
        assert len(brains.CREATED) == 1

    def test_a_legacy_brain_section_needs_exactly_one_way_to_name_the_brain(self):
        base = client_yaml("alpha")
        both = {"factory": "app_test_brains:good_brain", "agent": "test_brain"}
        for brain in (both, {}):
            with pytest.raises(ValueError, match="exactly one"):
                ClientFile.model_validate({**base, "brain": brain})
        assert ClientFile.model_validate({**base, "brain": {"agent": "test_brain"}}).brain.agent
