"""`sales-agent secrets ...`: the credential store, its master key and its move to the database.

    generate-key                  print a new random master key (it is not stored anywhere)
    migrate-to-db [--apply]       copy the credentials of the encrypted file into the database
    import-env CLIENT [--apply]   store the credentials a client's legacy `client.yaml` took from
                                  environment variables, in that client's own scope

`migrate-to-db` and `import-env` print what they would do and write nothing unless `--apply` is
given; they never print a value, never delete the file or the variable, and are safe to repeat.

Put the key printed by `generate-key` in the SALES_AGENT_SECRETS_KEY environment variable of the
deployment, and keep a copy somewhere safe and separate from the database backups: without it the
stored credentials cannot be read, and with it anyone holding those backups can read them.
"""

import argparse
import os
import sys
from collections.abc import Mapping

from sales_app.client_records import ClientRecordStore
from sales_app.config.client_wiring import wiring_for
from sales_app.config.schema import LLMSection
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.persistence import create_database_engine, current_revision, head_revision
from sales_app.persistence.client_record_rows import SqlClientRecordStore
from sales_app.persistence.secret_rows import SqlSecretAudit, SqlSecretStore
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    SECRETS_FILENAME,
    AesGcmCipher,
    CredentialService,
    DuplicateSecretError,
    FileSecretStore,
    InvalidCredentialError,
    SecretStoreError,
    client_scope,
    generate_master_key,
)
from sales_app.settings import Settings

EXIT_OK, EXIT_PROBLEM, EXIT_INVALID = 0, 1, 2

# What a legacy `client.yaml` names by environment variable: (credential key, scope, section, field).
# The scope is the one the runtime looks the credential up in (see `config.loader._resolve_secrets`).
_LEGACY_ENV = (
    (WHATSAPP_ACCESS_TOKEN, "client", "whatsapp", "token_env"),
    (WHATSAPP_PHONE_NUMBER_ID, "client", "whatsapp", "phone_number_id_env"),
    (WHATSAPP_APP_SECRET, "global", "whatsapp", "app_secret_env"),
    (OPENAI_API_KEY, "global", "llm", "api_key_env"),
)


# A field the file leaves out takes the schema's default (the OpenAI key's variable has one).
_DEFAULTS = {"api_key_env": LLMSection.model_fields["api_key_env"].default}


def add_secrets_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    secrets = commands.add_parser("secrets", help="manage the credential store")
    actions = secrets.add_subparsers(dest="secrets_command", required=True)
    actions.add_parser("generate-key", help="print a new random master key")
    migrate = actions.add_parser(
        "migrate-to-db", help="copy the encrypted credential file into the database"
    )
    migrate.add_argument("--apply", action="store_true", help="write (without it: report only)")
    legacy = actions.add_parser(
        "import-env",
        help="store a client's legacy environment-variable credentials in the database",
    )
    legacy.add_argument("client")
    legacy.add_argument("--apply", action="store_true", help="write (without it: report only)")


def run_secrets(args: argparse.Namespace, settings: Settings | None = None) -> int:
    if args.secrets_command == "generate-key":
        print(generate_master_key())
        return EXIT_OK
    assert settings is not None
    if settings.database_url is None:
        return _fail("SALES_AGENT_DATABASE_URL is not set", EXIT_INVALID)
    if not (settings.secrets_key and settings.secrets_key.get_secret_value().strip()):
        return _fail("SALES_AGENT_SECRETS_KEY is not set", EXIT_INVALID)
    engine = create_database_engine(settings.database_url.get_secret_value())
    try:
        if current_revision(engine) != head_revision():
            return _fail("the schema is not up to date: run `sales-agent db upgrade` first", 1)
        cipher = AesGcmCipher.from_text(settings.secrets_key.get_secret_value())
        store = SqlSecretStore(engine, cipher)
        store.verify()
        if args.secrets_command == "migrate-to-db":
            return migrate_to_db(settings, store, cipher, apply=args.apply)
        service = CredentialService(store, SqlSecretAudit(engine))
        records = SqlClientRecordStore(engine)
        return import_env(
            settings, service, store, records, args.client, os.environ, apply=args.apply
        )
    except SecretStoreError as error:
        return _fail(str(error), EXIT_PROBLEM)
    finally:
        engine.dispose()


def migrate_to_db(
    settings: Settings, store: SqlSecretStore, cipher: AesGcmCipher, *, apply: bool
) -> int:
    path = settings.secrets_dir / SECRETS_FILENAME
    if not path.exists():
        print(f"nothing to move: {path} does not exist")
        return EXIT_OK
    source = FileSecretStore(path, cipher)
    source.verify()
    moved = same = conflicts = 0
    for scope in sorted({e.scope for e in _all_entries(source)}):
        for entry in source.entries(scope):
            value = source.get(scope, entry.key)
            held = store.get(scope, entry.key)
            if held is None:
                print(
                    f"{scope}: {entry.key}: would copy"
                    if not apply
                    else f"{scope}: {entry.key}: copied"
                )
                if apply:
                    store.set(scope, entry.key, value, by=f"migrate:{entry.updated_by}")
                moved += 1
            elif held == value:
                same += 1
            else:
                print(f"{scope}: {entry.key}: CONFLICT, the database value is kept")
                conflicts += 1
    print(
        f"{moved} to copy, {same} already identical, {conflicts} conflict(s); the file is untouched"
    )
    if not apply:
        print("report only: nothing was written")
    return EXIT_PROBLEM if conflicts else EXIT_OK


def _all_entries(source: FileSecretStore):
    # The file store lists by scope; its scopes are the global one and the clients' (read raw).
    return [e for scope in _scopes_of(source) for e in source.entries(scope)]


def _scopes_of(source: FileSecretStore) -> list[str]:
    return sorted({r["scope"] for r in source._read()})


def import_env(
    settings: Settings,
    service: CredentialService,
    store: SqlSecretStore,
    records: ClientRecordStore,
    client_id: str,
    environment: Mapping[str, str],
    *,
    apply: bool,
) -> int:
    if not CLIENT_ID_PATTERN.fullmatch(client_id):
        return _fail("invalid client id", EXIT_INVALID)
    raw, problem = wiring_for(settings.client_files_dir, client_id, records)
    if raw is None:
        return _fail(problem or "unknown client", EXIT_INVALID)
    done = rejected = 0
    for key, kind, section, field in _LEGACY_ENV:
        body = raw.get(section)
        variable = (
            body.get(field) if isinstance(body, dict) and field in body else _DEFAULTS.get(field)
        )
        scope = client_scope(client_id) if kind == "client" else GLOBAL_SCOPE
        if not isinstance(variable, str) or not variable:
            continue
        value = (environment.get(variable) or "").strip()
        if not value:
            print(f"{scope}: {key}: the environment variable {variable} is not set, skipped")
        elif store.entry(scope, key) is not None:
            print(f"{scope}: {key}: already stored, kept")
        else:
            print(f"{scope}: {key}: {'stored' if apply else 'would store'} (from {variable})")
            if apply:
                try:
                    service.save(scope, key, value, actor="import-env")
                except (InvalidCredentialError, DuplicateSecretError) as error:
                    print(f"{scope}: {key}: REJECTED, {error}")
                    rejected += 1
                    continue
            done += 1
    if not apply:
        print("report only: nothing was written")
    print(f"{done} credential(s) {'stored' if apply else 'to store'} for {client_id}")
    return EXIT_PROBLEM if rejected else EXIT_OK


def _fail(message: str, code: int) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


__all__ = ["add_secrets_parser", "import_env", "migrate_to_db", "run_secrets"]
