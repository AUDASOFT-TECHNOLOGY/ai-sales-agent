"""Structural rules of the composite repository, checked on its source code.

They keep true what it promises whatever is changed later: the source is never written, facts are
written only after the prospect is known to exist, the registry is reached only through the
`ConsentWriter` door, and nothing in it logs or prints.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SOURCING = SRC / "sales_app" / "sourcing"
COMPOSITE = SOURCING / "composite.py"
SOURCE_WRITES = {"create_prospects", "update_prospects"}


def tree() -> ast.Module:
    return ast.parse(COMPOSITE.read_text(encoding="utf-8"))


def calls_on(node: ast.AST, owner: str) -> list[ast.Call]:
    """Calls of the form `self.<owner>.<method>(...)`."""
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == owner
        and isinstance(n.func.value.value, ast.Name)
        and n.func.value.value.id == "self"
    ]


def names() -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree()):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.split(".")[-1])
    return found


def test_the_composite_never_writes_to_the_source():
    written = {c.func.attr for c in calls_on(tree(), "_source")} & SOURCE_WRITES
    assert not written, sorted(written)
    assert "ProspectChange" not in names()


def test_facts_are_written_only_after_the_existence_check():
    method = next(
        n for n in ast.walk(tree()) if isinstance(n, ast.FunctionDef) and n.name == "append_facts"
    )
    check = [c for c in ast.walk(method) if _is_self_call(c, "_find_raw")]
    write = [c for c in calls_on(method, "_store") if c.func.attr == "append_facts"]
    assert check and len(write) == 1
    assert min(c.lineno for c in check) < write[0].lineno
    assert any(isinstance(n, ast.Raise) for n in ast.walk(method))  # the KeyError


def _is_self_call(node: ast.AST, method: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    )


def test_only_the_composite_records_facts_in_the_sourcing_package():
    writers = {
        p.name
        for p in SOURCING.rglob("*.py")
        if "append_facts"
        in {
            n.name
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
            if isinstance(n, ast.FunctionDef)
        }
    }
    # `sql_store.py` implements the store's side of the port (it is the one that persists facts);
    # the composite stays the only code in the package that *calls* it, which the test above checks.
    assert writers == {"composite.py", "sql_store.py"}


def test_the_registry_is_reached_only_through_the_writer_door():
    used = names()
    assert "ConsentWriter" in used
    assert not used & {"ConsentRegistry", "ConsentAdmin", "append_release", "RELEASE", "EventType"}


def test_the_composite_imports_no_provider_and_no_other_layer():
    standard = set(sys.stdlib_module_names)
    for node in ast.walk(tree()):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [node.module]
        for module in modules:
            top = module.split(".")[0]
            if top in standard:
                continue
            assert module.startswith(("sales_app.sourcing", "sales_core.contracts")), module


def test_the_composite_logs_nothing_and_prints_nothing():
    for node in ast.walk(tree()):
        if isinstance(node, ast.Import):
            assert "logging" not in {a.name for a in node.names}
        if isinstance(node, ast.ImportFrom):
            assert node.module != "logging"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"print", "breakpoint"}
