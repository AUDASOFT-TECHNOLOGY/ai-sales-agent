"""Single-instance guard.

The Engine's single-writer guarantee is an in-process lock, so two application processes over
the same Sheets would break it. An OS-level exclusive file lock refuses the second process and
disappears on its own if the first one dies (no stale lock files).
"""

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Self


class InstanceAlreadyRunningError(RuntimeError):
    """Another process holds the lock."""


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise RuntimeError("lock already held by this object")
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            self._lock(fd)
        except OSError as error:
            os.close(fd)
            raise InstanceAlreadyRunningError(
                f"another instance holds {self._path}; only one process may run at a time"
            ) from error
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            self._unlock(fd)
        finally:
            os.close(fd)
        try:
            self._path.unlink()
        except OSError:
            pass  # another process may already be starting; the lock, not the file, is what counts

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    # ---- platform specifics --------------------------------------------------------------

    @staticmethod
    def _lock(fd: int) -> None:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fd: int) -> None:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
