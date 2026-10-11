"""Prospect identifiers: what a source may hand over, and how they are named once inside Revansa.

Three kinds of identifier meet in one client's data, and they must never be confused:

- a **native** id, the one a source gives its record (a CRM id, a Sheets `prospect_id`);
- a **public** id, the one the Core sees and stores in actions and messages. For a source with an
  `id_namespace` it is `<namespace>:<native id>`; for a source with none (Google Sheets today) it is
  the native id **unchanged**, so every identifier already in a spreadsheet keeps working;
- a **local** id, the one the Engine gives a prospect it creates from an unknown sender
  (`inbound-<day>-<key>`). It lives in the operational store, never in a source.

`:` is safe as the separator because nothing parses an identifier: the Core joins ids with `:` into
idempotency keys and message ids but never splits them. To keep those joins unambiguous, a
namespaced native id is limited to an alphabet without `:` and the namespaced form always contains
exactly one `:` before the native part, while a local id never contains one.

A source with no namespace may legitimately hold ids that look like local ones: a spreadsheet in
which the Engine already created prospects has `inbound-...` rows. They are kept as they are. What
the code that composes sources must do is refuse to create a local prospect under an id a source
already has (the repository port's own `ValueError`), not hide the source's record.
"""

import re

from sales_app.sourcing.errors import InvalidNativeIdError

# Every pattern is applied with `fullmatch`, never `match` with `$`: `$` also accepts a final newline.
NAMESPACE_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,31}")  # the shape of a provider key
NATIVE_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,128}")
# What the Engine builds is `inbound-<ISO day>-<digits or lower-cased address>`: letters, digits and
# `@ + . _ -`. Anything else (a sender with no digit and a blank or a colon in it) is refused rather
# than stored under an id that could be misread.
LOCAL_ID_PREFIX = "inbound-"
LOCAL_ID_PATTERN = re.compile(r"inbound-[A-Za-z0-9@+._-]{1,160}")
SEPARATOR = ":"


def native_id_problem(value: object) -> str | None:
    """Why `value` is not an acceptable native id, or None. Never repeats the value."""
    if not isinstance(value, str):
        return "the id is not text"
    if not value:
        return "the id is empty"
    if not NATIVE_ID_PATTERN.fullmatch(value):
        return "the id has a character outside letters, digits and . _ - or is longer than 128"
    return None


def check_namespace(namespace: str | None) -> str | None:
    """A namespace is a provider key or None (ids kept as they are)."""
    if namespace is not None and not NAMESPACE_PATTERN.fullmatch(namespace):
        raise ValueError("an id namespace must look like a provider key (a-z, 0-9, _)")
    return namespace


def to_public_id(namespace: str | None, native_id: str) -> str:
    """The id the Core sees for a source's record.

    With no namespace the native id is returned untouched and **not validated**: those are the
    identifiers a client's spreadsheet already holds, and they must not change. With a namespace the
    native id must pass `native_id_problem`; otherwise `InvalidNativeIdError` (the record is to be
    left out and reported).
    """
    check_namespace(namespace)
    if namespace is None:
        return native_id
    problem = native_id_problem(native_id)
    if problem is not None:
        raise InvalidNativeIdError(problem)
    return f"{namespace}{SEPARATOR}{native_id}"


def to_native_id(namespace: str | None, public_id: str) -> str:
    """The inverse of `to_public_id`. Raises `ValueError` if `public_id` is not of this namespace
    (for instance a local id), so a lookup can never be sent to the wrong place."""
    check_namespace(namespace)
    if namespace is None:
        return public_id
    prefix = f"{namespace}{SEPARATOR}"
    if not public_id.startswith(prefix):
        raise ValueError("the id does not belong to this source")
    native = public_id[len(prefix) :]
    if native_id_problem(native) is not None:
        raise ValueError("the id does not belong to this source")
    return native


def is_local_id(public_id: str) -> bool:
    """Whether an id has the shape of one the Engine creates for an unknown sender."""
    return public_id.startswith(LOCAL_ID_PREFIX)


def local_id_problem(public_id: str) -> str | None:
    """Why an Engine-made id cannot be stored, or None. Never repeats the id."""
    if not is_local_id(public_id):
        return "the id is not one of an unknown sender"
    if not LOCAL_ID_PATTERN.fullmatch(public_id):
        return "the id has a character outside letters, digits and @ + . _ - or is too long"
    return None
