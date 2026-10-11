# Consent registry (opt-out)

Who must never be contacted, kept per client as **keyed digests** in a **signed chain of events**.
Code: `src/sales_app/sourcing/consent/`. Operator commands: `sales-agent consent ...`.

## What it keeps

An append-only log, one JSON line per event. A contact appears only as a digest
(`HMAC-SHA256` with a key of that client): a phone number or an address is never stored, logged,
printed or put in an error. An event is an `opt_out` or a `release`; a contact is blocked when its
latest event is an `opt_out`.

- **Key**: 32 random bytes per client, kept in the `SecretStore` under the reserved scope
  `consent:<client_id>` (key `hmac_key`). It is deliberately not in the Admin's credential catalogue.
- **Anchor**: `<seq>.<signature of the last event>`, same scope (key `anchor`), written after the
  event. It says where the log must end.
- **Log**: `<secrets_dir>/consent/<client_id>.jsonl`, on the server, out of reach of the client's
  staff. Reads and writes are serialised between processes and threads by a lock file that is never
  deleted (`filelock.py`; not the single-instance guard `lockfile.InstanceLock`, which refuses at once
  and deletes its file).

## Fail closed

The registry is `ok`, `not_initialised`, `corrupt` or `unavailable`. **Anything but `ok` blocks every
contact**: a record changed, deleted, reordered or inserted; the log truncated or removed; the key or
the anchor missing or altered; the store or the log unreadable; a lock that cannot be taken; anything
unexpected. Nothing is repaired or guessed. A recorded refusal cannot be lifted by the Engine, by a
source or by a CRM: the only code able to write a `release` is the operator service
(`consent/admin.py`), and an architecture test keeps it that way.

## What it does NOT protect against

It **reveals** edits made to the log by anyone who does not hold the key. It does **not** protect
against an attacker able to restore, together and consistently, both the log and the secret store
that holds the key and the anchor: that rolls the registry back to an earlier valid state without
being noticed. A test states this limit on purpose. The key is also the registry: lose it and every
refusal is unreadable (the registry becomes `corrupt`, so everything stays blocked); back it up with
the secret store, keeping the master key apart.

## Operator commands

| Command | Writes | What it does |
|---|---|---|
| `init --client C` | yes | Sets the registry up. Explicit and safe to repeat; never makes a new key over an existing registry, healthy or not. |
| `status`, `verify-chain --client C` | no | Health, counts, integrity. Exit 4 if the chain fails. |
| `check --client C` | no | Asks for a contact (hidden) and says whether it is blocked. |
| `record --client C --reason R` | yes | Records a refusal an operator was told of. |
| `release --client C --username U --reason R --evidence E` | yes | Ends a block. |

The writing commands refuse to run while the application runs (exit 3), like `run`: two processes
must not write the credential store at once.

**Ending a block** needs an active **owner** account and its password (asked again, whatever session
exists), a reason (`written_consent`, `registration_error` or `other`), an evidence reference (plain
text, never personal data), the typed phrase `release opt-out`, fewer than 5 releases by that account
in 24 hours, and, when the refusal came from the contact's own message, the reason `written_consent`.
Every refusal to act is audited.

**Ending a block changes nothing else.** No prospect is reactivated, resumed or edited: the service
is given no prospect, no store and no source. A prospect already marked opted out, paused or closed
stays so until a person changes it on purpose, and a source that still carries a do-not-contact
signal blocks the contact again the next time its refusals are recorded.

Exit codes: 0 ok, 1 a problem (not set up, store or log unavailable), 2 invalid input or refused,
3 the application is running, 4 the registry failed its integrity check.

## Sources

`record_refusals(registry, source.refused_contacts())` records the refusals a source reports for
records it left out of the commercial cycle, as `opt_out` events of origin `source`. Idempotent,
logs nothing, returns a count. Wiring it to the scheduler and to the composing repository is a later
increment; until then the registry has no effect on a real send.
