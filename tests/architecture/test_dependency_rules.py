"""Enforces the dependency rules in docs/architecture.md."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
BRAINS = ROOT / "brains"


def _imported_top_levels(package_dir: Path) -> set[str]:
    found: set[str] = set()
    for py in package_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    return found


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
