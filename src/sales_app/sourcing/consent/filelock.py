"""An exclusive lock between processes, for the consent log.

Not `sales_app.lockfile.InstanceLock`, on purpose. That one is a single-instance guard: it refuses at
once instead of waiting, and it deletes its file on release, which on POSIX lets a process that
opened the old file lock an orphaned inode while another locks a fresh one. A mutex around a
critical section needs the opposite: it waits (with a limit), and the lock file is never deleted.

The lock is held through an open file descriptor, so the operating system releases it by itself if
the process dies. It is not re-entrant: `FileConsentLog` tracks re-entry itself.
"""

import os
import sys
import time

from sales_app.sourcing.consent.errors import ConsentLockTimeoutError

DEFAULT_TIMEOUT_SECONDS = 10.0
_POLL_SECONDS = 0.02


class ExclusiveFileLock:
    def __init__(self, path: os.PathLike[str] | str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._path = os.fspath(path)
        self._timeout = timeout
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise RuntimeError("lock already held by this object")
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                _try_lock(fd)
                self._fd = fd
                return
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise ConsentLockTimeoutError(
                        "the consent log is locked by another process"
                    ) from None
                time.sleep(_POLL_SECONDS)

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)  # the file stays: deleting it would reopen the orphaned-inode race


def _try_lock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
