"""Enforces the dependency rules in docs/architecture.md."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
BRAINS = ROOT / "brains"


def _imported_modules(package_dir: Path) -> set[str]:
    """Fully qualified names of everything imported by the package (absolute imports only)."""
    found: set[str] = set()
    for py in package_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module)
    return found


def _imported_top_levels(package_dir: Path) -> set[str]:
    return {name.split(".")[0] for name in _imported_modules(package_dir)}


def _brain_packages() -> list[str]:
    return sorted(p.name for p in BRAINS.glob("*/*_brain") if p.is_dir())


def test_core_is_generic():
    forbidden = {"sales_integrations", "sales_app", *_brain_packages()}
    assert not _imported_top_levels(SRC / "sales_core") & forbidden


def test_integrations_do_not_know_app_or_brains():
    forbidden = {"sales_app", *_brain_packages()}
    assert not _imported_top_levels(SRC / "sales_integrations") & forbidden


def test_brains_import_only_core_and_do_not_import_each_other():
    for brain_dir in BRAINS.glob("*/*_brain"):
        others = set(_brain_packages()) - {brain_dir.name}
        forbidden = {"sales_integrations", "sales_app", *others}
        assert not _imported_top_levels(brain_dir) & forbidden, brain_dir.name


def test_core_contracts_and_ports_use_only_stdlib_and_core():
    """Brains depend on these, so they must stay free of third-party and vendor imports."""
    allowed = set(sys.stdlib_module_names) | {"sales_core"}
    for name in ("contracts", "ports"):
        extra = _imported_top_levels(SRC / "sales_core" / name) - allowed
        assert not extra, f"sales_core.{name} imports {sorted(extra)}"


def test_integrations_do_not_import_each_other():
    root = SRC / "sales_integrations"
    adapters = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != "__pycache__")
    for adapter in adapters:
        siblings = {f"sales_integrations.{other}" for other in adapters if other != adapter}
        for module in _imported_modules(root / adapter):
            assert not any(module == s or module.startswith(s + ".") for s in siblings), (
                f"{adapter} imports {module}"
            )


def test_fakes_use_only_stdlib_and_core():
    allowed = set(sys.stdlib_module_names) | {"sales_core", "sales_integrations"}
    extra = _imported_top_levels(SRC / "sales_integrations" / "fakes") - allowed
    assert not extra, f"fakes import {sorted(extra)}"
