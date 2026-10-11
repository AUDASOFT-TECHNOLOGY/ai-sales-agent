"""Structural rules of the consent registry, checked on the source code itself.

They keep true, whatever is written later, what the registry promises: only the operator service can
end a block; the registry knows no prospect and no commercial decision; nothing in it logs or prints;
its keys stay out of the Admin's credential catalogue.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CONSENT = SRC / "sales_app" / "sourcing" / "consent"
CONSENT_CLI = SRC / "sales_app" / "consent_cli.py"

# Where a release may be written: the event vocabulary, the registry that appends it and the
# operator service that is allowed to ask. Nowhere else.
RELEASE_ALLOWED = {
    "sales_app/sourcing/consent/events.py",
    "sales_app/sourcing/consent/registry.py",
    "sales_app/sourcing/consent/admin.py",
}
COMMERCIAL_VOCABULARY = {
    "DecisionType", "BrainDecision", "ProspectStatus", "SalesActionType", "ActionStatus",
    "MessageDraft", "ActionPriority", "Scheduling", "Prospect", "SalesAction",
}  # fmt: skip
ALLOWED_APP_IMPORTS = (
    "sales_app.secret_store",
    "sales_app.admin_auth",
    "sales_app.config_store.models",
    "sales_app.sourcing.consent",
    "sales_app.sourcing.contacts",
)


def parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


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
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found


def imports_of(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def consent_files() -> list[Path]:
    files = sorted(CONSENT.glob("*.py"))
    assert files, "the consent package is missing"
    return files


def test_only_the_operator_service_can_end_a_block():
    users = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if names_in(path) & {"append_release", "RELEASE"}
    }
    assert users == RELEASE_ALLOWED, sorted(users ^ RELEASE_ALLOWED)


def test_the_commands_and_the_sources_side_never_name_a_release():
    for path in (CONSENT_CLI, CONSENT / "recorder.py", CONSENT / "log.py", CONSENT / "keys.py"):
        assert not names_in(path) & {"append_release", "RELEASE", "EventType"}, path.name


def test_the_operator_service_checks_a_password_again_itself():
    admin = CONSENT / "admin.py"
    assert "verify_password" in names_in(admin)
    assert "get_by_username" in names_in(admin)
    assert "OWNER" in names_in(admin)


def test_the_registry_imports_only_the_standard_library_the_core_contracts_and_a_few_app_parts():
    standard = set(sys.stdlib_module_names)
    for path in consent_files():
        for module in imports_of(path):
            top = module.split(".")[0]
            if top in standard:
                continue
            if top == "sales_core":
                assert module == "sales_core.contracts", (path.name, module)
            else:
                assert top == "sales_app", (path.name, module)
                assert module.startswith(ALLOWED_APP_IMPORTS), (path.name, module)


def test_the_registry_knows_no_prospect_no_store_and_no_source():
    forbidden = (
        "sales_app.sourcing.ports",
        "sales_app.sourcing.merge",
        "sales_app.sourcing.fields",
        "sales_app.sourcing.statuses",
        "sales_app.sourcing.problems",
        "sales_app.persistence",
        "sales_app.wiring",
        "sales_app.api",
    )
    for path in consent_files():
        for module in imports_of(path):
            assert not module.startswith(forbidden), (path.name, module)


def test_the_registry_uses_no_commercial_vocabulary_at_all():
    for path in (*consent_files(), CONSENT_CLI):
        assert not names_in(path) & COMMERCIAL_VOCABULARY, path.name


def test_nothing_in_the_registry_logs_or_prints():
    for path in consent_files():
        tree = parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert "logging" not in {a.name for a in node.names}, path.name
            if isinstance(node, ast.ImportFrom):
                assert node.module != "logging", path.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"print", "breakpoint"}, path.name


def test_the_commands_print_only_fixed_text_and_counts():
    """A contact is a variable the commands hold only to hand over: it is never an f-string field."""
    tree = parse(CONSENT_CLI)
    held = {"contact", "again", "phone", "email", "password", "confirmation"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
            for argument in node.args:
                for part in ast.walk(argument):
                    if isinstance(part, ast.FormattedValue):
                        mentioned = {n.id for n in ast.walk(part.value) if isinstance(n, ast.Name)}
                        assert not mentioned & held, ast.unparse(node)


def test_the_registry_does_not_touch_the_admins_credential_catalogue():
    from sales_app.secret_store import CATALOG

    keys = {spec.key for spec in CATALOG}
    assert not {k for k in keys if "consent" in k} and not keys & {"hmac_key", "anchor"}


def test_the_reserved_scope_is_named_in_one_place_only():
    owners = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(parse(path)):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("consent:")
            ) or (
                isinstance(node, ast.JoinedStr)
                and node.values
                and isinstance(node.values[0], ast.Constant)
                and str(node.values[0].value).startswith("consent:")
            ):
                owners.add(path.relative_to(SRC).as_posix())
    assert owners == {"sales_app/sourcing/consent/anchor.py"}


def test_the_existing_cli_only_registers_and_routes_the_consent_commands():
    text = (SRC / "sales_app" / "cli.py").read_text(encoding="utf-8")
    assert text.count("consent") == 6  # import (2 names), route (2), registration (2): no more
    assert "from sales_app.consent_cli import add_consent_parser, run_consent" in text
    assert 'if args.command == "consent":' in text
    assert "add_consent_parser(commands)" in text


def test_the_lock_the_log_relies_on_is_not_the_single_instance_guard():
    """The guard refuses at once and deletes its file; a mutex around a critical section must not."""
    log, lock = CONSENT / "log.py", CONSENT / "filelock.py"
    assert "ExclusiveFileLock" in names_in(log) and "InstanceLock" not in names_in(log)
    assert "unlink" not in names_in(lock) and "remove" not in names_in(lock)
    assert "InstanceLock" in names_in(CONSENT_CLI)  # used for what it is for: one writer process
