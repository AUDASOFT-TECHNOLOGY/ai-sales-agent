"""The SQL operational store keeps SQL in the persistence package, the Core's types out of it, and
every statement scoped to one client."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "sales_app"
ROWS = SRC / "persistence" / "operational_rows.py"
STORE = SRC / "sourcing" / "sql_store.py"
CODEC = SRC / "sourcing" / "store_codec.py"


def tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_rows_layer_is_the_only_new_code_that_speaks_sql():
    assert any(m.startswith("sqlalchemy") for m in imports(ROWS))
    for path in (STORE, CODEC):
        assert not any(m.split(".")[0] in {"sqlalchemy", "alembic"} for m in imports(path)), path


def test_the_rows_layer_knows_no_core_type_and_the_store_no_persistence():
    assert not {m for m in imports(ROWS) if m.split(".")[0].startswith("sales_core")}
    assert not {m for m in imports(STORE) | imports(CODEC) if m.startswith("sales_app.persistence")}


def test_every_query_of_the_rows_layer_is_filtered_on_the_client():
    wheres = [
        n
        for n in ast.walk(tree(ROWS))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "where"
        and not isinstance(n.func.value, ast.Name)  # `query.where(...)` only narrows a built query
    ]
    assert wheres
    for call in wheres:
        assert "client_id" in ast.unparse(call), ast.unparse(call)[:80]


def test_every_insert_of_the_rows_layer_names_the_client():
    inserts = [
        n
        for n in ast.walk(tree(ROWS))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "values"
        and isinstance(n.func.value, ast.Call)
        and getattr(n.func.value.func, "id", "") == "insert"
    ]
    assert inserts
    for call in inserts:
        assert any(k.arg == "client_id" for k in call.keywords), ast.unparse(call)[:80]


def test_nothing_in_the_operational_store_logs_or_prints():
    for path in (ROWS, STORE, CODEC):
        assert "logging" not in imports(path), path
        names = {n.id for n in ast.walk(tree(path)) if isinstance(n, ast.Name)}
        assert "print" not in names, path


def test_the_store_is_not_part_of_the_sourcing_package_exports():
    """Importing `sales_app.sourcing` must not pull SQL in: the store is imported by its module."""
    assert "sql_store" not in "\n".join(
        ast.unparse(n)
        for n in ast.walk(tree(SRC / "sourcing" / "__init__.py"))
        if isinstance(n, ast.ImportFrom)
    )
