"""Structural rules of the HubSpot connector, checked on its source code.

The adapter reads, writes only through one bounded update, and holds no application code; the CRM source knows no vendor and never logs;
only the provider's descriptor builds the connector; and the token is named only where it is read.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
APP = SRC / "sales_app"
ADAPTER = SRC / "sales_integrations" / "hubspot"
CRM_SOURCE = APP / "sourcing" / "crm_source.py"
DESCRIPTOR = APP / "wiring" / "providers" / "hubspot.py"


def tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def names(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.split(".")[-1])
    return found


def test_the_adapter_depends_only_on_the_standard_library_httpx_and_itself():
    allowed = set(sys.stdlib_module_names) | {"httpx", "sales_integrations"}
    for py in ADAPTER.rglob("*.py"):
        for module in imports(py):
            assert module.split(".")[0] in allowed, (py.name, module)
            if module.startswith("sales_integrations"):
                assert module.startswith("sales_integrations.hubspot"), (py.name, module)


def test_the_adapter_has_exactly_one_way_to_write():
    """Every request goes through one method. The verbs are GET, the contact search's POST and one
    PATCH: the update of a contact, which only `update_contact` makes."""
    calls = [
        node
        for py in ADAPTER.rglob("*.py")
        for node in ast.walk(tree(py))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_request"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]
    assert {c.args[0].value for c in calls} == {"GET", "POST", "PATCH"}
    posts = [c for c in calls if c.args[0].value == "POST"]
    assert len(posts) == 1 and any(
        isinstance(a, ast.Name) and a.id == "SEARCH_PATH" for a in posts[0].args
    )
    patches = [c for c in calls if c.args[0].value == "PATCH"]
    assert len(patches) == 1
    # ...and that PATCH is inside `update_contact`, which refuses a property that was not declared
    update = next(
        node
        for node in ast.walk(tree(ADAPTER / "client.py"))
        if isinstance(node, ast.FunctionDef) and node.name == "update_contact"
    )
    assert update.lineno < patches[0].lineno <= update.end_lineno
    assert "_writable" in names(ADAPTER / "client.py")
    # nothing creates or deletes
    assert not names(ADAPTER / "client.py") & {"put", "patch", "delete", "post"}


def test_the_adapter_logs_and_prints_nothing():
    for py in ADAPTER.rglob("*.py"):
        assert "logging" not in imports(py), py.name
        assert not names(py) & {"print", "logger", "log", "breakpoint"}, py.name


def test_only_the_descriptor_knows_the_adapter():
    users = {
        py.relative_to(SRC).as_posix()
        for py in APP.rglob("*.py")
        if any(m.startswith("sales_integrations.hubspot") for m in imports(py))
    }
    assert users == {"sales_app/wiring/providers/hubspot.py"}


def test_the_crm_source_knows_no_vendor_no_http_and_no_other_layer():
    for module in imports(CRM_SOURCE):
        top = module.split(".")[0]
        assert top in set(sys.stdlib_module_names) | {"sales_app", "sales_core"}, module
        if top == "sales_app":
            assert module.startswith("sales_app.sourcing"), module
        if top == "sales_core":
            assert module == "sales_core.contracts", module
    assert not any("hubspot" in n.lower() for n in names(CRM_SOURCE))


def test_the_crm_source_never_calls_a_write_and_logs_nothing():
    used = names(CRM_SOURCE)
    assert "logging" not in imports(CRM_SOURCE) and not used & {"print", "logger"}
    # the reader port has no write at all; the optional writer port is `write_fields` (one door)
    assert not used & {"create_records", "update_records", "save", "put", "post", "patch"}


def test_the_token_is_named_only_where_it_is_read_or_declared():
    allowed = {
        APP / "config" / "secrets.py",
        APP / "wiring" / "providers" / "hubspot.py",
        APP / "api" / "client_source.py",
    }
    readers = {
        py.relative_to(APP).as_posix()
        for py in APP.rglob("*.py")
        if py not in allowed and "HUBSPOT_ACCESS_TOKEN" in names(py)
    }
    assert not readers, sorted(readers)


def test_the_runtime_never_names_a_provider_it_composes():
    """`build_runtime` composes whatever source the table holds; it knows none by name."""
    runtime = APP / "wiring" / "runtime.py"
    assert not any("hubspot" in n.lower() for n in names(runtime))
    strings = {
        n.value.lower()
        for n in ast.walk(tree(runtime))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert not any("hubspot" in s for s in strings)
