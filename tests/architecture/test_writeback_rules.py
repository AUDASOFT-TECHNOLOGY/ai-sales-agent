"""Structural rules of the write-back package, checked on its source code.

They keep true, whatever is written later, what the write-back promises: only the synchroniser ever
writes to a source, the consent registry is reached through one door, nothing logs or prints, nothing
is kept on disk, and the package knows nothing of providers, the Engine or the Brains.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SOURCING = SRC / "sales_app" / "sourcing"
WRITEBACK = SOURCING / "writeback"
SYNCER = WRITEBACK / "syncer.py"
EXPECTED_MODULES = {"__init__", "ledger", "memory", "planning", "policy", "syncer"}

STANDARD = set(sys.stdlib_module_names)
# The vocabulary the write-back may use from the Core, beyond what `sourcing` already allows: a
# status and the prospects it is read from. No decision, message, action or priority.
WRITEBACK_VOCABULARY = {"Prospect", "ProspectStatus"}
COMMERCIAL_VOCABULARY = {
    "DecisionType", "BrainDecision", "ProspectStatus", "SalesActionType", "ActionStatus",
    "MessageDraft", "ActionPriority", "Scheduling", "Prospect", "SalesAction",
}  # fmt: skip
CONSENT_DOOR = {"ConsentWriter"}
NOT_FOR_DISK_OR_CLOCK = {
    "os", "pathlib", "io", "tempfile", "shutil", "sqlite3", "shelve", "pickle", "json", "csv",
    "random", "secrets", "socket", "http", "urllib", "ssl", "subprocess", "sched", "asyncio",
}  # fmt: skip


def writeback_files() -> list[Path]:
    files = sorted(WRITEBACK.glob("*.py"))
    assert {p.stem for p in files} >= EXPECTED_MODULES, "a write-back module is missing"
    return files


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def imported(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """(module, names imported from it) for every import of a file."""
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: relative import"
            found.append((node.module or "", tuple(a.name for a in node.names)))
    return found


def names_in(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
        elif isinstance(node, ast.alias):
            found.add(node.name.split(".")[-1])
    return found


def receiver_names(call: ast.Call) -> list[str]:
    """The names along what a method is called on: `self._source.update_prospects` gives
    `['self', '_source']`."""
    chain: list[str] = []
    node = call.func.value if isinstance(call.func, ast.Attribute) else None
    while node is not None:
        if isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Name):
            chain.append(node.id)
            node = None
        else:
            node = None
    return chain[::-1]


def source_writes(path: Path) -> list[str]:
    """Methods that write to a prospect source called in this file: `update_prospects` or
    `create_prospects` on something named like a source."""
    return [
        node.func.attr
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"update_prospects", "create_prospects"}
        and any("source" in name.lower() for name in receiver_names(node))
    ]


# ---- who writes to a source --------------------------------------------------------------


def test_only_the_synchroniser_writes_to_a_source():
    writers = {
        path.relative_to(SRC).as_posix(): sorted(set(calls))
        for path in SRC.rglob("*.py")
        if (calls := source_writes(path))
    }
    assert writers == {"sales_app/sourcing/writeback/syncer.py": ["update_prospects"]}


def test_no_other_write_back_module_even_names_a_write_to_a_prospect():
    for path in writeback_files():
        if path == SYNCER:
            continue
        assert not names_in(path) & {"update_prospects", "create_prospects"}, path.name


def test_the_synchroniser_never_creates_a_prospect_in_the_source():
    assert "create_prospects" not in names_in(SYNCER)


def test_the_composite_still_never_writes_to_the_source():
    assert not source_writes(SOURCING / "composite.py")


# ---- layers -------------------------------------------------------------------------------


def test_the_write_back_imports_only_the_standard_library_the_sourcing_package_and_core_contracts():
    for path in writeback_files():
        for module, _ in imported(path):
            top = module.split(".")[0]
            if top in STANDARD:
                continue
            if top == "sales_core":
                assert module == "sales_core.contracts", (path.name, module)
            else:
                assert top == "sales_app", (path.name, module)
                assert module.startswith("sales_app.sourcing"), (path.name, module)


def test_the_write_back_knows_no_provider_no_wiring_no_persistence_and_no_engine():
    forbidden = (
        "sales_integrations",
        "sales_brain_kit",
        "sales_core.engine",
        "sales_core.ports",
        "sales_app.wiring",
        "sales_app.persistence",
        "sales_app.api",
        "sales_app.config",
        "sales_app.secret_store",
        "sales_app.sourcing.composite",
    )
    for path in writeback_files():
        for module, _ in imported(path):
            assert not module.startswith(forbidden), (path.name, module)


def test_nothing_else_depends_on_the_write_back():
    """The write-back sits above the sourcing contracts: they never import it, so the composite and
    the registry keep working, and keep their own rules, without it."""
    for path in SRC.rglob("*.py"):
        if WRITEBACK in path.parents or path == WRITEBACK:
            continue
        for module, _ in imported(path):
            assert not module.startswith("sales_app.sourcing.writeback"), (
                path.relative_to(SRC).as_posix(),
                module,
            )


# ---- the consent registry -----------------------------------------------------------------


def test_the_consent_registry_is_reached_only_through_the_writer_door():
    for path in writeback_files():
        for module, names in imported(path):
            if module.startswith("sales_app.sourcing.consent"):
                assert module == "sales_app.sourcing.consent", (path.name, module)
                assert set(names) <= CONSENT_DOOR, (path.name, names)
    used = set().union(*(names_in(path) for path in writeback_files()))
    assert "ConsentWriter" in used
    assert not used & {
        "ConsentRegistry",
        "ConsentAdmin",
        "ConsentKeys",
        "ConsentLog",
        "FileConsentLog",
        "InMemoryConsentLog",
        "ConsentSecrets",
        "append_release",
        "RELEASE",
        "EventType",
        "record_refusals",
        "record_opt_out",
    }


def test_only_the_synchroniser_talks_to_the_registry_and_only_to_read_it():
    for path in writeback_files():
        if path == SYNCER:
            continue
        assert "ConsentWriter" not in names_in(path), path.name
    assert "record_opt_out" not in names_in(SYNCER)  # an opt-out is never recorded from here


# ---- vocabulary ---------------------------------------------------------------------------


def test_the_write_back_uses_only_the_commercial_vocabulary_it_needs():
    for path in writeback_files():
        used = names_in(path) & COMMERCIAL_VOCABULARY
        assert used <= WRITEBACK_VOCABULARY, (path.name, sorted(used - WRITEBACK_VOCABULARY))


# ---- no logging, no disk, no real clock ---------------------------------------------------


def test_nothing_in_the_write_back_logs_or_prints():
    for path in writeback_files():
        tree = parse(path)
        for module, _ in imported(path):
            assert module.split(".")[0] not in {"logging", "warnings", "traceback", "pprint"}, (
                path.name,
                module,
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"print", "breakpoint", "input", "open"}, path.name
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"stdout", "stderr", "excepthook"}, path.name


def test_the_write_back_keeps_nothing_on_disk_and_reads_no_real_clock_or_randomness():
    for path in writeback_files():
        for module, _ in imported(path):
            assert module.split(".")[0] not in NOT_FOR_DISK_OR_CLOCK, (path.name, module)
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"now", "utcnow", "today", "sleep", "time"}, path.name


# The one message that interpolates: `ledger.py` names which of its two time fields is naive. The
# interpolated name is the field's own name from a fixed pair, never a value that was passed in.
INTERPOLATED = {("ledger.py", "name")}


def test_every_failure_message_of_the_package_is_a_fixed_sentence():
    """No `%`, `.format` or f-string is raised in an exception, so what a failure says can never
    carry a value that was passed in. The only exception is listed in `INTERPOLATED`."""
    for path in writeback_files():
        for node in ast.walk(parse(path)):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            for arg in node.exc.args:
                assert not isinstance(arg, ast.BinOp), path.name
                assert not (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "format"
                ), path.name
                if isinstance(arg, ast.JoinedStr):
                    inserted = {
                        (path.name, ast.unparse(part.value))
                        for part in arg.values
                        if isinstance(part, ast.FormattedValue)
                    }
                    assert inserted <= INTERPOLATED, (path.name, sorted(inserted))
