"""Brains are found by key through entry points; nothing a client writes is imported."""

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
BRAINS = ROOT / "brains"
GROUP = "revansa.brains"

# The only places in `sales_app` that import a module by a dotted name: the operator's legacy
# `brain.factory`. A Brain named by key never goes through either.
LEGACY_IMPORTERS = {
    "sales_app/config/brain_settings.py",
    "sales_app/wiring/brains.py",
}


def _calls_import_module(py: Path) -> bool:
    for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else getattr(function, "id", "")
            )
            if name in {"import_module", "__import__"}:
                return True
    return False


def test_only_the_legacy_factory_loaders_import_by_dotted_name():
    found = {
        py.relative_to(SRC).as_posix()
        for py in (SRC / "sales_app").rglob("*.py")
        if _calls_import_module(py)
    }
    assert found <= LEGACY_IMPORTERS, sorted(found - LEGACY_IMPORTERS)


def test_the_brain_registry_never_imports_by_dotted_name():
    registry = SRC / "sales_app" / "brain_registry.py"
    assert not _calls_import_module(registry)
    text = registry.read_text(encoding="utf-8")
    assert f'"{GROUP}"' in text and "entry_points(" in text


def test_the_runtime_resolves_an_agent_only_through_the_registry():
    runtime = (SRC / "sales_app" / "wiring" / "runtime.py").read_text(encoding="utf-8")
    assert "section.agent is not None" in runtime and "installed_brains()" in runtime
    config = (SRC / "sales_app" / "config" / "runtime_config.py").read_text(encoding="utf-8")
    assert "brain_factories" not in config  # `agent` is no longer mapped to a factory path


def test_every_brain_entry_point_targets_its_own_package_and_a_valid_key():
    declared = {}
    root = tomllib.loads((BRAINS.parent / "pyproject.toml").read_text(encoding="utf-8"))
    for key, target in root["project"]["entry-points"][GROUP].items():
        assert target.split(":")[0].split(".")[0] == "sales_brain_kit", (key, target)
        declared[key] = target
    for pyproject in BRAINS.glob("*/pyproject.toml"):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        points = data.get("project", {}).get("entry-points", {}).get(GROUP, {})
        package = next(p.name for p in pyproject.parent.glob("*_brain") if p.is_dir())
        for key, target in points.items():
            assert target.split(":")[0].split(".")[0] == package, (key, target)
            declared[key] = target
    # The platform ships one generic engine; no Brain package exists for any single client.
    assert declared == {"consultative": "sales_brain_kit.generic:BRAIN"}


def test_brain_packages_never_import_the_application():
    for py in BRAINS.glob("*/*_brain/**/*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(n.split(".")[0] == "sales_app" for n in names), py


def test_the_descriptor_lives_in_the_kit_and_the_core_is_untouched_by_it():
    descriptor = SRC / "sales_brain_kit" / "descriptor.py"
    assert descriptor.exists()
    assert "BrainDescriptor" not in "".join(
        p.read_text(encoding="utf-8") for p in (SRC / "sales_core").rglob("*.py")
    )
