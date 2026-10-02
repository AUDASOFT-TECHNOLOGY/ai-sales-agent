"""`sales-agent config ...`: the operator's tool for versioned client settings.

    validate <client> [--file F]      check a file, or the current version, against the wiring
    publish  <client> FILE            validate, then append a new version
    show     <client> [--version N]   print a version (default: current)
    history  <client>                 list versions
    diff     <client> A B             compare two versions
    rollback <client> --to N          publish version N again as a new version
    import   <client>                 seed version 1 from the settings in client.yaml

Nothing is written unless validation passes. Exit codes: 0 ok, 1 store problem (conflict, corrupt
data, unknown version), 2 invalid configuration or input.
"""

import argparse
import difflib
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from sales_app.config import (
    import_document,
    read_wiring,
    validate_settings_document,
)
from sales_app.config_store import ConfigStore, ConfigStoreError, ConfigVersion
from sales_app.config_store.models import CLIENT_ID_PATTERN, thaw_document
from sales_app.errors import ConfigurationError
from sales_app.persistence import build_account_stores
from sales_app.settings import Settings

EXIT_OK, EXIT_STORE, EXIT_INVALID = 0, 1, 2


def add_config_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    config = commands.add_parser("config", help="manage versioned client settings")
    actions = config.add_subparsers(dest="config_command", required=True)

    def with_client(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = actions.add_parser(name, help=help_text)
        parser.add_argument("client")
        return parser

    def with_authorship(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--author", help="recorded with the version (default: your user name)")
        parser.add_argument("--note", default="", help="why this version exists")

    validate = with_client("validate", "check a settings file, or the current version")
    validate.add_argument("--file", type=Path, help="a settings JSON file (default: current)")
    publish = with_client("publish", "validate a settings file and publish it as a new version")
    publish.add_argument("file", type=Path)
    with_authorship(publish)
    show = with_client("show", "print a version")
    show.add_argument("--version", type=int, help="default: the current version")
    with_client("history", "list every version")
    diff = with_client("diff", "compare two versions")
    diff.add_argument("from_version", type=int)
    diff.add_argument("to_version", type=int)
    rollback = with_client("rollback", "publish an older version again as a new version")
    rollback.add_argument("--to", dest="target", type=int, required=True)
    with_authorship(rollback)
    seed = with_client("import", "publish the settings already in client.yaml as version 1")
    with_authorship(seed)


def run_config(settings: Settings, args: argparse.Namespace) -> int:
    stores = build_account_stores(settings)  # files or the database, as the settings say
    handler = _HANDLERS[args.config_command]
    try:
        return handler(settings, stores.config_store, args)
    except ConfigStoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_STORE
    finally:
        stores.close()


# ---- commands ---------------------------------------------------------------------------------


def _validate(settings: Settings, store: ConfigStore, args: argparse.Namespace) -> int:
    wiring = _wiring(settings, args.client)
    if args.file is not None:
        document: Any = _read_json(args.file)
        label = str(args.file)
    else:
        current = _require(store.current(args.client), args.client, None)
        document, label = thaw_document(current.document), f"{args.client} v{current.version}"
    _require_valid(args.client, wiring, document)
    print(f"ok: {label} is valid")
    return EXIT_OK


def _publish(settings: Settings, store: ConfigStore, args: argparse.Namespace) -> int:
    wiring = _wiring(settings, args.client)
    check = _require_valid(args.client, wiring, _read_json(args.file))
    return _append(store, args, check)


def _show(settings: Settings, store: ConfigStore, args: argparse.Namespace) -> int:
    _client(args.client)
    record = _require(
        store.get(args.client, args.version) if args.version else store.current(args.client),
        args.client,
        args.version,
    )
    print(
        json.dumps(
            {
                "client_id": record.client_id,
                "version": record.version,
                "parent": record.parent,
                "created_at": record.created_at.isoformat(),
                "author": record.author,
                "note": record.note,
                "document": thaw_document(record.document),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return EXIT_OK


def _history(settings: Settings, store: ConfigStore, args: argparse.Namespace) -> int:
    _client(args.client)
    versions = store.history(args.client)
    if not versions:
        print(f"{args.client}: no versions published")
    for record in versions:
        stamp = record.created_at.strftime("%Y-%m-%d %H:%M:%SZ")
        print(f"v{record.version}  {stamp}  {record.author}  {record.note}".rstrip())
    return EXIT_OK


def _diff(settings: Settings, store: ConfigStore, args: argparse.Namespace) -> int:
    _client(args.client)
    old = _require(store.get(args.client, args.from_version), args.client, args.from_version)
    new = _require(store.get(args.client, args.to_version), args.client, args.to_version)
    lines = list(
        difflib.unified_diff(
            _pretty(old).splitlines(),
            _pretty(new).splitlines(),
            fromfile=f"v{old.version}",
            tofile=f"v{new.version}",
            lineterm="",
        )
    )
    print("\n".join(lines) if lines else "no differences")
    return EXIT_OK


def _rollback(settings: Settings, store: ConfigStore, args: argparse.Namespace) -> int:
    wiring = _wiring(settings, args.client)
    target = _require(store.get(args.client, args.target), args.client, args.target)
    check = _require_valid(args.client, wiring, thaw_document(target.document))
    if not args.note:
        args.note = f"rollback to v{target.version}"
    return _append(store, args, check)


def _import(settings: Settings, store: ConfigStore, args: argparse.Namespace) -> int:
    wiring = _wiring(settings, args.client)
    if store.current(args.client) is not None:
        raise ConfigurationError(
            [f"{args.client}: versions already exist; use 'config publish' to change them"]
        )
    check = import_document(args.client, wiring)
    if check.errors:
        raise ConfigurationError([f"{args.client}: {e}" for e in check.errors])
    if not args.note:
        args.note = "imported from client.yaml"
    return _append(store, args, check)


_HANDLERS = {
    "validate": _validate,
    "publish": _publish,
    "show": _show,
    "history": _history,
    "diff": _diff,
    "rollback": _rollback,
    "import": _import,
}


# ---- helpers ----------------------------------------------------------------------------------


def _client(client: str) -> str:
    if not CLIENT_ID_PATTERN.match(client):
        raise ConfigurationError([f"invalid client id {client!r}"])
    return client


def _wiring(settings: Settings, client: str) -> dict[str, Any]:
    raw, problem = read_wiring(settings.clients_dir / _client(client))
    if raw is None:
        raise ConfigurationError([problem or f"{client}: cannot read client.yaml"])
    return raw


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ConfigurationError([f"cannot read {path}: {error}"]) from error


def _require_valid(client: str, wiring: dict[str, Any], document: Any):  # type: ignore[no-untyped-def]
    check = validate_settings_document(client, wiring, document)
    if check.errors:
        raise ConfigurationError([f"{client}: {error}" for error in check.errors])
    return check


def _require(record: ConfigVersion | None, client: str, version: int | None) -> ConfigVersion:
    if record is None:
        what = "no versions published" if version is None else f"no version {version}"
        raise ConfigStoreError(f"{client}: {what}")
    return record


def _append(store: ConfigStore, args: argparse.Namespace, check) -> int:  # type: ignore[no-untyped-def]
    assert check.document is not None
    current = store.current(args.client)
    record = store.publish(
        args.client,
        check.document,
        author=args.author or _default_author(),
        note=args.note,
        expected_parent=current.version if current else None,
    )
    print(f"published {args.client} v{record.version}")
    return EXIT_OK


def _default_author() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:  # noqa: BLE001 - no user name available (some containers)
        return "cli"


def _pretty(record: ConfigVersion) -> str:
    return json.dumps(thaw_document(record.document), indent=2, sort_keys=True, ensure_ascii=False)
