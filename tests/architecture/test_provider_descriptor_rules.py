"""Rules for the provider descriptors in `sales_app/wiring/providers`.

A descriptor is declared data about one provider. It may import the one integration adapter it
builds (and nothing of another provider's), and it never imports the Core: what it hands the
application is plain data and plain callables. The shared modules (types, registry) import no
adapter at all.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = ROOT / "src" / "sales_app" / "wiring" / "providers"

# Descriptor module -> the `sales_integrations` adapters it may import. `meta` builds nothing yet.
OWN_ADAPTER = {
    "google_sheets": {"google_sheets"},
    "hubspot": set(),  # declared only: its connector is a later increment
    "whatsapp": {"whatsapp"},
    "openai": {"openai"},
    "meta": set(),
}
SHARED = ("types", "registry", "credentials", "__init__")


def imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def adapters_of(modules: set[str]) -> set[str]:
    return {
        m.split(".")[1] for m in modules if m.split(".")[0] == "sales_integrations" and "." in m
    }


def test_every_descriptor_module_is_covered_by_these_rules():
    names = {p.stem for p in PROVIDERS.glob("*.py")}
    assert names == set(OWN_ADAPTER) | set(SHARED), "a provider module is missing from OWN_ADAPTER"


@pytest.mark.parametrize("name", sorted(OWN_ADAPTER))
def test_a_descriptor_never_imports_the_core(name):
    modules = imported_modules(PROVIDERS / f"{name}.py")
    assert not {m for m in modules if m.split(".")[0] == "sales_core"}, name


@pytest.mark.parametrize("name", sorted(OWN_ADAPTER))
def test_a_descriptor_imports_only_its_own_adapter(name):
    modules = imported_modules(PROVIDERS / f"{name}.py")
    assert adapters_of(modules) <= OWN_ADAPTER[name], name
    # the bare package `sales_integrations` would reach every adapter
    assert "sales_integrations" not in modules, name


@pytest.mark.parametrize("name", sorted(OWN_ADAPTER))
def test_a_descriptor_does_not_import_another_provider(name):
    others = {f"sales_app.wiring.providers.{other}" for other in OWN_ADAPTER if other != name}
    for module in imported_modules(PROVIDERS / f"{name}.py"):
        assert not any(module == o or module.startswith(o + ".") for o in others), (name, module)


@pytest.mark.parametrize("name", ["types", "registry", "credentials"])
def test_the_shared_modules_import_no_adapter_and_no_core(name):
    modules = imported_modules(PROVIDERS / f"{name}.py")
    tops = {m.split(".")[0] for m in modules}
    assert not tops & {"sales_core", "sales_integrations", "sales_brain_kit"}, name
    assert not any(
        m.startswith("sales_app.wiring.providers.") and m.split(".")[-1] in OWN_ADAPTER
        for m in modules
    )


def test_a_descriptor_imports_no_vendor_sdk_directly():
    vendors = {"openai", "google", "googleapiclient", "gspread", "httpx", "requests"}
    for name in OWN_ADAPTER:
        tops = {m.split(".")[0] for m in imported_modules(PROVIDERS / f"{name}.py")}
        assert not tops & vendors, name


def test_the_integration_table_imports_no_adapter_itself():
    """`default_builders` is built from the descriptors: the adapters are theirs to import."""
    table = ROOT / "src" / "sales_app" / "wiring" / "integrations.py"
    assert not adapters_of(imported_modules(table))
