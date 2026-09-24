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


# ---- Core facts and contact origin stay generic --------------------------------------------------

# Business vocabulary a Brain may use as fact keys; the Core's contracts must never name them.
BUSINESS_FACT_NAMES = (
    "country", "profession", "sector", "team_size", "product_interest", "budget", "interest",
)  # fmt: skip


def test_core_facts_and_origin_contracts_use_no_business_vocabulary():
    contracts = SRC / "sales_core" / "contracts"
    offenders = {
        name
        for name in _defined_and_used_names(contracts)
        if any(word in name.lower() for word in BUSINESS_FACT_NAMES)
    }
    assert not offenders, f"sales_core.contracts names business concepts: {sorted(offenders)}"
    for py in contracts.rglob("*.py"):
        strings = {
            node.value.lower()
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not strings & set(BUSINESS_FACT_NAMES), py.name


# ---- facts persistence stays behind ports ---------------------------------------------------------


def test_the_engine_only_reads_facts_and_the_core_names_no_storage_for_them():
    """Recording facts is somebody else's job (a later slice); the Engine holds a reader only."""
    engine_names = _defined_and_used_names(SRC / "sales_core" / "engine")
    assert "FactsReader" in engine_names and "FactsWriter" not in engine_names
    assert "append_facts" not in engine_names
    ports = _imported_top_levels(SRC / "sales_core" / "ports" / "facts.py")
    assert ports <= set(sys.stdlib_module_names) | {"sales_core"}


def test_the_sheets_adapter_implements_the_generic_facts_ports_from_the_core():
    imported = _imported_modules(SHEETS_ADAPTER)
    assert "sales_core.contracts" in imported
    assert not {m for m in imported if m.startswith("sales_core.engine")}


# ---- the consultative Brain runtime stays generic and small-surfaced ---------------------------------

RUNTIME = KIT / "consultative"
# Words that would tie the runtime to a client, a market or a language.
CLIENT_SPECIFIC_WORDS = (
    "naga", "tounesimmo", "immobili", "real estate", "arabic", "french", "english", "tunisia",
    "derja", "dinar", "tnd",
)  # fmt: skip


def test_the_brain_runtime_uses_the_core_only_through_contracts_and_the_llm_port():
    """It is a Brain: it reads contracts and reaches its model through the injected port. It never
    reaches into the Engine, Planner, safety or approval, which are the Core's to run."""
    for module in _imported_modules(RUNTIME):
        if module.startswith("sales_core"):
            assert module in {"sales_core.contracts", "sales_core.ports"}, module


def test_the_brain_runtime_hard_codes_no_client_market_or_language():
    for py in RUNTIME.rglob("*.py"):
        text = py.read_text(encoding="utf-8").lower()
        for word in CLIENT_SPECIFIC_WORDS:
            assert word not in text, f"{word!r} appears in {py.relative_to(ROOT)}"


def test_the_brain_runtime_builds_no_scheduler_quota_or_persistence_of_its_own():
    forbidden_imports = {"threading", "asyncio", "sched", "sqlite3", "socket", "subprocess", "os"}
    assert not _imported_top_levels(RUNTIME) & forbidden_imports
    names = {name.lower() for name in _defined_and_used_names(RUNTIME)}
    for word in ("quota", "daily_limit", "append_facts", "save_actions", "update_prospects"):
        assert not any(word in name for name in names), word


def test_the_brain_runtime_never_sends_or_writes_anything():
    """Effects are the Core's: the Brain holds no channel, repository or facts writer."""
    names = _defined_and_used_names(RUNTIME)
    for forbidden in ("MessagingChannel", "SalesRepository", "FactsWriter", "send"):
        assert forbidden not in names, forbidden


def test_the_brain_runtime_has_one_way_dependencies():
    """Foundations (facts, language, catalog, history) never import the layers above them."""
    order = [
        "errors", "facts", "applicability", "market", "language", "register", "catalog", "history",
        "interest", "selection", "pricing", "payment", "discovery", "follow_up", "workflow",
        "exact", "strategy", "context", "commerce", "output", "prompt", "validation", "decision",
        "brain",
    ]  # fmt: skip
    for index, name in enumerate(order):
        later = {f"sales_brain_kit.consultative.{other}" for other in order[index + 1 :]}
        imported = _imported_modules_of_file(RUNTIME / f"{name}.py")
        assert not imported & later, f"{name} imports a module above it: {sorted(imported & later)}"


# ---- the Naga Brain is a thin package over the kit ---------------------------------------------------

NAGA = BRAINS / "naga" / "naga_brain"


def test_the_naga_brain_depends_only_on_the_core_and_the_kit():
    allowed = set(sys.stdlib_module_names) | {"naga_brain", "sales_core", "sales_brain_kit"}
    assert _imported_top_levels(NAGA) <= allowed


def test_the_naga_brain_reaches_the_core_only_through_contracts_and_ports():
    for module in _imported_modules(NAGA):
        if module.startswith("sales_core"):
            assert module in {"sales_core.contracts", "sales_core.ports"}, module


def test_the_naga_brain_adds_no_second_brain_interface_and_no_effects():
    names = _defined_and_used_names(NAGA)
    for forbidden in ("MessagingChannel", "SalesRepository", "FactsWriter", "send", "Engine"):
        assert forbidden not in names, forbidden
    assert not _imported_top_levels(NAGA) & {"threading", "asyncio", "sqlite3", "socket", "os"}


def test_the_kit_never_depends_on_the_naga_brain_or_any_other_client_brain():
    assert not _imported_top_levels(KIT) & set(_brain_packages())


# ---- Slice 6b: payment, markets and workflows are generic capabilities -----------------------------

# Places, currencies and business names that belong to a client's configuration, never to code.
GEOGRAPHY_AND_CLIENT_WORDS = (
    r"naga", r"tounesimmo", r"tunisia", r"tunisie", r"tunisian", r"algeria", r"algerie",
    r"algerian", r"maghreb", r"\btunis\b", r"\balger\b", r"\bdinars?\b", r"\btnd\b", r"\bdzd\b",
)  # fmt: skip


def _string_constants(package_dir: Path) -> list[tuple[str, str]]:
    """(file, text) of every string literal that is code rather than a docstring."""
    found: list[tuple[str, str]] = []
    for py in package_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(getattr(node.body[0], "value", None), ast.Constant)
        }
        found += [
            (py.relative_to(ROOT).as_posix(), node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
    return found


def test_no_client_name_market_geography_or_currency_of_a_client_is_in_generic_source():
    import re

    generic = [SRC / "sales_core", SRC / "sales_brain_kit", SRC / "sales_integrations"]
    generic += [SRC / "sales_app" / name for name in ("wiring", "api", "config_store")]
    pattern = re.compile("|".join(GEOGRAPHY_AND_CLIENT_WORDS), re.IGNORECASE)
    for package in generic:
        for py in package.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            match = pattern.search(text)
            assert match is None, f"{match.group(0)!r} appears in {py.relative_to(ROOT)}"


def test_no_generic_code_branches_on_who_the_client_or_the_product_is():
    """Nothing compares a client id, a display name, a product id or a country with a literal."""
    watched = {"client_id", "display_name", "product_id", "country", "market_id"}
    offenders = []
    for py in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Compare):
                continue
            sides = [node.left, *node.comparators]
            names = {
                side.id if isinstance(side, ast.Name) else side.attr
                for side in sides
                if isinstance(side, ast.Name | ast.Attribute)
            }
            literal = any(isinstance(s, ast.Constant) and isinstance(s.value, str) for s in sides)
            if literal and names & watched:
                offenders.append(f"{py.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


def test_the_core_knows_nothing_of_payment_markets_prices_or_workflows():
    """The exact-value and workflow machinery lives in the Brain kit; the Core's contracts and
    engine are unchanged and carry none of its vocabulary."""
    offenders = {
        name
        for name in _defined_and_used_names(SRC / "sales_core")
        if any(word in name.lower() for word in ("payment", "market", "workflow", "iban", "price"))
    }
    assert not offenders, f"sales_core names commerce concepts: {sorted(offenders)}"


def test_no_account_number_link_or_payment_detail_is_written_in_source():
    import re

    iban_like = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,}\b")
    long_digits = re.compile(r"\d{9,}")
    link = re.compile(r"https?://[A-Za-z0-9-]+\.")  # a real address, not the pattern of one
    for package in (SRC / "sales_core", SRC / "sales_brain_kit", BRAINS):
        for path, text in _string_constants(package):
            assert not iban_like.search(text) and not long_digits.search(text), path
            assert not link.search(text), f"a link is written in {path}"


def test_the_exact_value_mechanism_is_pure_and_reaches_no_storage_or_network():
    forbidden = {"httpx", "requests", "socket", "sqlite3", "os", "subprocess", "threading"}
    for name in ("exact", "payment", "pricing", "commerce", "workflow", "market", "applicability"):
        assert not _imported_top_levels_of_file(RUNTIME / f"{name}.py") & forbidden, name


def test_the_settings_schema_imports_only_pydantic_and_itself():
    allowed = set(sys.stdlib_module_names) | {"pydantic", "sales_brain_kit"}
    assert _imported_top_levels(KIT / "settings") <= allowed
    for module in _imported_modules(KIT / "settings"):
        if module.startswith("sales_brain_kit"):
            assert module.startswith("sales_brain_kit.settings"), module
