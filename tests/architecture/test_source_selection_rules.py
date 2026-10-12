"""Rules for choosing a prospect source from the Client API.

The Client routes reach the outside only through the checks of `connection_tests` and the provider
descriptors, never through an adapter or a vendor library; the HubSpot token is read by nothing that
runs an agent (its connector is a later increment); the Core knows no CRM; and a Client route never
names what belongs to WhatsApp or Meta.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
APP = SRC / "sales_app"
CLIENT_SOURCE = APP / "api" / "client_source.py"
CHECKS = APP / "connection_tests.py"
VENDORS = {"openai", "google", "googleapiclient", "gspread", "httplib2", "sqlalchemy", "hubspot"}


def parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def string_constants(path: Path) -> list[str]:
    """The strings the code uses, not its docstrings (which may say what it does not do)."""
    tree = parse(path)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def identifiers(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[-1])
    return names


def test_the_client_source_routes_import_no_adapter_and_no_vendor_library():
    for path in (CLIENT_SOURCE, CHECKS):
        modules = imported_modules(path)
        assert not {m for m in modules if m.startswith("sales_integrations")}, path.name
        assert not {m.split(".")[0] for m in modules} & VENDORS, path.name


def test_the_client_source_routes_do_not_reach_the_core_at_all():
    assert not {m for m in imported_modules(CLIENT_SOURCE) if m.startswith("sales_core")}


def test_the_client_source_routes_name_nothing_of_whatsapp_or_meta():
    words = ("whatsapp", "meta", "phone_number", "app_secret", "verify_token")
    names = {n.lower() for n in identifiers(CLIENT_SOURCE)}
    texts = [s.lower() for s in string_constants(CLIENT_SOURCE)]
    for word in words:
        assert not any(word in n for n in names), word
        assert not any(word in t for t in texts), word


def test_the_client_source_routes_write_only_the_credential_their_provider_declares():
    """The key is taken from the chosen provider's descriptor, never typed into a route."""
    texts = string_constants(CLIENT_SOURCE)
    assert not any(t.startswith(("hubspot.", "openai.", "whatsapp.", "meta.")) for t in texts)


def test_nothing_that_runs_an_agent_reads_the_hubspot_token():
    allowed = {
        APP / "config" / "secrets.py",
        APP / "wiring" / "providers" / "hubspot.py",
        CLIENT_SOURCE,
    }
    readers = {
        path.relative_to(APP).as_posix()
        for path in APP.rglob("*.py")
        if path not in allowed and "HUBSPOT_ACCESS_TOKEN" in identifiers(path)
    }
    assert not readers, f"a later increment's business, not this one's: {sorted(readers)}"


def test_the_core_and_the_adapters_other_than_hubspots_know_no_crm():
    for package in ("sales_core", "sales_integrations"):
        for path in (SRC / package).rglob("*.py"):
            if "hubspot" in path.relative_to(SRC).parts:
                continue
            assert not any("hubspot" in n.lower() for n in identifiers(path)), path.name
            assert not any("hubspot" in s.lower() for s in string_constants(path)), path.name


def test_the_connection_checks_only_read():
    """Every request a check makes is a GET; no check posts, puts, patches or deletes."""
    tree = parse(CHECKS)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "client"
    }
    # the OpenAI inference check is the one deliberate, announced, billed request (a POST)
    assert calls <= {"get", "post"}
    sources = {"check_hubspot", "check_google_sheets"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in sources:
            used = {
                n.func.attr
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }
            assert not used & {"post", "put", "patch", "delete", "write", "append", "add_tabs"}
