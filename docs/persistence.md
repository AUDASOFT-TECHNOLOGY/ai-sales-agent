# Persistence (Phase 3)

Accounts, client profiles, sessions and versioned client settings can be kept in a SQL database.
PostgreSQL is the production target; SQLite is accepted for tests and local development only.

## What lives where

| Data | Where | Port |
|---|---|---|
| Admin accounts and their client scope | database (`admin_users`, `admin_user_clients`) | `AdminUserStore` |
| Admin sessions | database (`admin_sessions`) | `AdminSessionStore` |
| Client profiles (login, hashed password, logo, WhatsApp number) | database (`client_profiles`) | `ClientProfileStore` |
| Client sessions | database (`client_sessions`) | `AdminSessionStore` |
| Versioned client settings | database (`config_versions`, append-only) | `ConfigStore` |
| Client identity (anchor of every foreign key) | database (`clients`) | - |
| `enabled`, Brain/Sheets/WhatsApp wiring, schedule polling | `clients/<id>/client.yaml` | - |
| API keys, Meta token, phone number id, app secret, each client's Telegram/e-mail/Google grants | encrypted `SecretStore`: database table `secrets` (AES-256-GCM, scope `global` or `client:<id>`) when a database is configured, else an encrypted file | `SecretStore` |
| Prospects, actions, messages | Google Sheets | `SalesRepository` |
| Actions, messages, audit, facts, per-prospect state, prospects created from unknown senders — of a client whose prospects live in another system | database (`op_*`, migration 0004) | `OperationalStore` |

Credentials are in the database only as ciphertext, in the `secrets` table (migration 0016), opened with the
deployment's `SALES_AGENT_SECRETS_KEY`, which is never stored there. No other table holds a credential: a test
fails if a table or column names one.
`enabled` stays in `client.yaml`; the `clients` table does not hold a second copy of a client's name or
status. Agents, usage, quota, billing and revenue have no table yet: they will be modelled in the
phases that need them.

## Operational store (`op_*` tables)

A client whose prospects are in a CRM keeps what Revansa itself does in seven tables, each row owned by one
client (`client_id`, a foreign key to `clients`; every query filters on it): `op_actions` (unique idempotency key
per client), `op_messages` and `op_audit` (append-only, in order), `op_facts` (idempotent by `fact_identity`),
`op_prospect_states` (an optimistic `revision`: a stale save is refused), `op_local_prospects` and
`op_local_contacts` (the database itself refuses two local prospects sharing a phone number or address).
The full record is a JSON document; other columns are only what is queried or kept unique. The SQL is in
`persistence/operational_rows.py` (it knows no Core type); `sourcing/sql_store.py` and `store_codec.py` turn the
Core's objects into documents and implement the `OperationalStore` port. Nothing runs on it yet: wiring it to the
runtime is the next increment. Sheets clients are untouched.

## Relations

```
clients 1 --- 0..1 client_profiles
clients 1 --- N    client_sessions        (deleted with the client)
clients 1 --- N    config_versions        (append-only; triggers refuse UPDATE / DELETE)
admin_users N --- N clients  via admin_user_clients  (foreign keys: an unknown client is refused)
admin_users 1 --- N admin_sessions        (deleted with the account)
```

`client_profiles.login` and `client_profiles.whatsapp_digits` are unique: two requests racing for the
same login or number end in a refusal, never a duplicate. A profile and its `clients` row are created
in one transaction.

## Choosing files or the database

`SALES_AGENT_DATABASE_URL` set: everything above is in the database, and the application refuses to
start if the schema is missing or outdated. Unset: the JSON files (`admin/`, `profiles/`, `config/`)
are used exactly as before. `sales-agent admin`, `sales-agent config` and the server all go through
the same choice (`sales_app/persistence/factory.py`).

## Development setup (PostgreSQL)

```powershell
# 1. A local PostgreSQL (any install; Docker shown):
docker run -d --name revansa-pg -e POSTGRES_USER=revansa -e POSTGRES_PASSWORD=revansa `
  -e POSTGRES_DB=revansa -p 5432:5432 postgres:16

# 2. Point the app at it (.env or the environment):
SALES_AGENT_DATABASE_URL=postgresql://revansa:revansa@localhost:5432/revansa

# 3. Create the schema, then check it:
uv run sales-agent db upgrade
uv run sales-agent db status
```

`postgresql://` URLs use the psycopg 3 driver this project installs. For a throwaway local database
without PostgreSQL, `SALES_AGENT_DATABASE_URL=sqlite:///revansa.db` works (development and tests only).

## Moving the existing file data

```powershell
uv run sales-agent db upgrade
uv run sales-agent db import --dry-run   # what would be copied; nothing is written
uv run sales-agent db import             # copy, in one transaction, then verify
uv run sales-agent db import --dry-run   # again: a complete import reports "0 to insert"
```

The import reads `admin/users.json`, `profiles/profiles.json` and `config/<id>/versions/*.json` plus
the `clients/` directories. It never changes or deletes a file, so the files stay as a backup. It is
idempotent, and it stops with nothing written if it finds:

- a duplicate username, login or WhatsApp number (compared by digits);
- an Admin scoped to a client that does not exist (the foreign key would refuse it);
- a settings version whose checksum no longer matches its file, or a broken version chain;
- a record already in the database with different content (reported as a conflict, never overwritten).

Sessions are not imported: people log in again. Switch by setting `SALES_AGENT_DATABASE_URL` once the
import is verified; unset it to go back to the files.

## Back up

Back up the database (`pg_dump`) **and, separately,** the master key (and the secrets directory if a file store is
still in use). Keep
`SALES_AGENT_SECRETS_KEY` apart from every backup.

## Tests

Store contracts (`tests/app/test_admin_auth_store.py`, `test_client_profiles.py`,
`test_config_store.py`) run against files, memory and SQL. `tests/app/test_persistence.py` covers the
migrations, foreign keys, uniqueness, append-only history, concurrency, the import and the
application end to end; `tests/app/test_db_cli.py` the commands. They use SQLite built by the real
Alembic migrations; the same migrations create the PostgreSQL schema (including its own triggers).
