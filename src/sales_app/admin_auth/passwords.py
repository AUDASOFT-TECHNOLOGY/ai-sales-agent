"""The one module in `admin_auth` that imports bcrypt.

Isolated the same way the Sheets adapter isolates the Google client libraries to one file
(`tests/architecture`): the rest of `admin_auth` stays a thin, stdlib-only layer, so a future
password-hashing choice changes nothing outside this file.
"""

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False  # not a bcrypt hash: never matches, never raises
