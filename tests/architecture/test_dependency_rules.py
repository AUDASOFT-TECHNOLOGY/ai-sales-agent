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


# ---- the application layer ---------------------------------------------------------------

VENDOR_AND_STORAGE = {
    "openai", "google", "googleapiclient", "gspread", "psycopg2", "psycopg", "sqlalchemy",
    "redis", "celery", "apscheduler", "sqlite3", "pymongo",
}  # fmt: skip
# Commercial vocabulary: decisions and lifecycle belong to the Brain and the Core, never here.
COMMERCIAL_VOCABULARY = {
    "DecisionType", "BrainDecision", "ProspectStatus", "SalesActionType", "ActionStatus",
    "MessageDraft", "ActionPriority", "Scheduling", "Prospect", "SalesAction",
}  # fmt: skip


def _identifiers(package_dir: Path) -> set[str]:
    names: set[str] = set()
    for py in package_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.add(node.name.split(".")[-1])
    return names


def test_the_app_never_imports_a_brain_package():
    assert not _imported_top_levels(SRC / "sales_app") & set(_brain_packages())


def test_the_app_contains_no_vendor_or_storage_code():
    assert not _imported_top_levels(SRC / "sales_app") & VENDOR_AND_STORAGE


def test_the_app_uses_no_commercial_vocabulary():
    assert not _identifiers(SRC / "sales_app") & COMMERCIAL_VOCABULARY


def test_no_client_names_appear_in_the_app_or_the_core():
    for package in ("sales_app", "sales_core"):
        for py in (SRC / package).rglob("*.py"):
            text = py.read_text(encoding="utf-8").lower()
            for client in ("naga", "tounesimmo"):
                assert client not in text, f"{client} mentioned in {py.relative_to(ROOT)}"


def test_the_scheduler_only_knows_the_clock_and_the_registry():
    """It triggers the Engine; choosing prospects (the Planner) is out of its reach."""
    scheduler = SRC / "sales_app" / "scheduler.py"
    tree = ast.parse(scheduler.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    assert {m for m in imported if m.startswith("sales_core")} == {"sales_core.ports"}
    assert not any(m.startswith("sales_integrations") for m in imported)


def test_the_webhook_route_reaches_the_core_only_through_the_engine_and_ports():
    tree = ast.parse((SRC / "sales_app" / "api" / "app.py").read_text(encoding="utf-8"))
    core = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("sales_core")
    }
    assert core == {"sales_core.engine", "sales_core.ports"}
    assert not any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sales_integrations")
        for node in ast.walk(tree)
    )


def test_configuration_lives_in_the_app_not_the_core():
    assert not (SRC / "sales_core" / "config").exists()
    assert not (SRC / "sales_core" / "registry.py").exists()
    assert (SRC / "sales_app" / "registry.py").exists()


# ---- the Google Sheets adapter must not leak into the Core or the app --------------------------

GOOGLE_LIBRARIES = {"google", "googleapiclient", "httplib2", "gspread"}
SHEETS_VOCABULARY = ("sheet", "google", "gspread")  # substrings that must not appear in Core code
SHEETS_ADAPTER = SRC / "sales_integrations" / "google_sheets"


def _defined_and_used_names(package_dir: Path) -> set[str]:
    """Every identifier in the code (not comments or docstrings): names, attributes, defs, args."""
    names: set[str] = set()
    for py in package_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.alias):
                names.add(node.name.split(".")[-1])
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
    return names


def test_the_core_knows_nothing_about_google_or_spreadsheets():
    core = SRC / "sales_core"
    assert not _imported_top_levels(core) & GOOGLE_LIBRARIES
    offenders = {
        name
        for name in _defined_and_used_names(core)
        if any(word in name.lower() for word in SHEETS_VOCABULARY)
    }
    assert not offenders, f"sales_core uses Sheets/Google vocabulary: {sorted(offenders)}"


def test_google_libraries_are_imported_by_one_module_only():
    importers = {
        py.relative_to(SRC).as_posix()
        for py in (SRC).rglob("*.py")
        if _imported_top_levels_of_file(py) & GOOGLE_LIBRARIES
    }
    assert importers == {"sales_integrations/google_sheets/api.py"}


def _imported_top_levels_of_file(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_the_sheets_adapter_depends_only_on_the_core_and_itself():
    allowed = set(sys.stdlib_module_names) | GOOGLE_LIBRARIES | {"sales_core", "sales_integrations"}
    assert not _imported_top_levels(SHEETS_ADAPTER) - allowed
    for module in _imported_modules(SHEETS_ADAPTER):
        if module.startswith("sales_integrations"):
            assert module.startswith("sales_integrations.google_sheets"), module
        if module.startswith("sales_core"):
            # contracts and ports only: the adapter never reaches into the planner or engine
            assert module.split(".")[1] in {"contracts", "ports"} if "." in module else True, module


def test_the_pure_adapter_modules_import_no_google_library():
    for name in ("codecs.py", "layout.py", "repository.py", "errors.py"):
        assert not _imported_top_levels_of_file(SHEETS_ADAPTER / name) & GOOGLE_LIBRARIES, name


def test_only_the_wiring_table_of_the_app_knows_the_sheets_adapter():
    for py in (SRC / "sales_app").rglob("*.py"):
        uses = any(
            m.startswith("sales_integrations.google_sheets") for m in _imported_modules_of_file(py)
        )
        assert uses == (py.name == "integrations.py" and py.parent.name == "wiring"), py.name


def _imported_modules_of_file(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_the_engine_and_planner_do_not_depend_on_any_adapter():
    for part in ("engine", "planner", "safety", "approval"):
        assert not {
            m
            for m in _imported_modules(SRC / "sales_core" / part)
            if m.startswith("sales_integrations")
        }


# ---- versioned client settings (ConfigStore) -----------------------------------------------------

CONFIG_STORE = SRC / "sales_app" / "config_store"


def test_the_config_store_is_plain_python_that_knows_only_itself():
    """Stdlib only: a database-backed store belongs in an adapter, never in this package."""
    allowed = set(sys.stdlib_module_names) | {"sales_app"}
    assert _imported_top_levels(CONFIG_STORE) <= allowed
    for module in _imported_modules(CONFIG_STORE):
        if module.startswith("sales_app"):
            assert module.startswith("sales_app.config_store"), module


def test_the_core_knows_nothing_about_configuration_storage():
    core = SRC / "sales_core"
    offenders = {
        name
        for name in _defined_and_used_names(core)
        if name in {"ConfigStore", "ConfigVersion", "FileConfigStore", "InMemoryConfigStore"}
        or "config_store" in name.lower()
    }
    assert not offenders, f"sales_core mentions the config store: {sorted(offenders)}"
    assert "sales_app" not in _imported_top_levels(core)


def test_the_engine_planner_and_integrations_do_not_use_the_config_store():
    for package in ("sales_core", "sales_integrations"):
        assert not any(
            module.startswith("sales_app") for module in _imported_modules(SRC / package)
        ), package


def test_only_the_composition_layer_reads_the_store_and_only_the_cli_writes_to_it():
    """Reading happens in the loader and bootstrap; publishing is an operator command."""
    writers = {
        py.relative_to(SRC).as_posix()
        for py in (SRC / "sales_app").rglob("*.py")
        if ".publish(" in py.read_text(encoding="utf-8")
        and "config_store" not in py.relative_to(SRC).as_posix()
    }
    assert writers == {"sales_app/config_cli.py"}


# ---- the shared Brain kit (sales_brain_kit) ----------------------------------------------------------

KIT = SRC / "sales_brain_kit"


def _class_names(package_dir: Path) -> set[str]:
    return {
        node.name
        for py in package_dir.rglob("*.py")
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
    }


def test_nothing_below_the_kit_imports_it():
    """The kit builds on the Core; the Core, the adapters and the app never depend on it."""
    for package in ("sales_core", "sales_integrations"):
        assert "sales_brain_kit" not in _imported_top_levels(SRC / package), package
    # sales_app finds a Brain's spec with importlib, never with a static import.
    assert "sales_brain_kit" not in _imported_top_levels(SRC / "sales_app")


def test_the_kit_imports_only_the_standard_library_pydantic_and_the_core():
    allowed = set(sys.stdlib_module_names) | {"sales_core", "sales_brain_kit", "pydantic"}
    assert _imported_top_levels(KIT) <= allowed


def test_the_kit_knows_no_client_and_none_of_the_layers_above_it():
    forbidden = {"sales_app", "sales_integrations", *_brain_packages()}
    assert not _imported_top_levels(KIT) & forbidden
    for py in KIT.rglob("*.py"):
        text = py.read_text(encoding="utf-8").lower()
        for client in ("naga", "tounesimmo"):
            assert client not in text, f"{client} mentioned in {py.relative_to(ROOT)}"


def test_the_core_holds_none_of_the_kits_business_vocabulary():
    """Products, discovery and follow-up strategy are Brain settings; the Core never names them."""
    kit_classes = _class_names(KIT) - {"Model"}
    leaked = kit_classes & _defined_and_used_names(SRC / "sales_core")
    assert not leaked, f"sales_core uses kit vocabulary: {sorted(leaked)}"


def test_the_settings_port_in_the_core_is_generic():
    """BrainSettingsSpec is a shape only: it may not import a schema library or the kit."""
    imported = _imported_top_levels(SRC / "sales_core" / "ports" / "brain.py")
    assert imported <= set(sys.stdlib_module_names) | {"sales_core"}
