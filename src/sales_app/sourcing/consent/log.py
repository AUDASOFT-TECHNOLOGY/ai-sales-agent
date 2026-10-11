"""Where the consent events are kept: an append-only log.

`FileConsentLog` is the default: JSON Lines on the server, next to the credential store, out of
reach of the client's own staff. The port is small so another place (a tab of the client's sheet,
later) can implement it without the registry changing.

Concurrency: every read and every append happens under an exclusive lock between processes
(`ExclusiveFileLock`) and between threads, so a reader never sees a half-written line and two
writers never pick the same sequence number. `exclusive()` lets the registry hold the lock across a
whole read-verify-append sequence; it can be entered again by the same thread.

Appends are compare-and-append: the caller says which sequence number it believes is last, and a
log that moved on refuses (`ConsentConflictError`). A line left half-written by a crash was never
acknowledged to anyone: reading ignores it, and the next append removes it before writing.
"""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from sales_app.sourcing.consent.errors import (
    ConsentConflictError,
    ConsentFormatError,
    ConsentLogUnavailableError,
)
from sales_app.sourcing.consent.events import ConsentEvent, from_line, to_line
from sales_app.sourcing.consent.filelock import DEFAULT_TIMEOUT_SECONDS, ExclusiveFileLock


@dataclass(frozen=True, slots=True)
class LogContents:
    events: tuple[ConsentEvent, ...]
    torn_tail: bool = False  # an unfinished last line was found and ignored


@runtime_checkable
class ConsentLog(Protocol):
    def exists(self) -> bool: ...

    def exclusive(self): ...  # a context manager

    def read(self) -> LogContents:
        """Raises `ConsentLogUnavailableError` or `ConsentFormatError`."""
        ...

    def append(self, event: ConsentEvent, *, expected_last_seq: int) -> None:
        """Add one event if `expected_last_seq` is still the last one, else `ConsentConflictError`."""
        ...


class _Reentrant:
    """Hold a lock once per thread, however many times it is entered."""

    def __init__(self, acquire, release) -> None:
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._acquire, self._release = acquire, release

    @contextmanager
    def __call__(self) -> Iterator[None]:
        with self._thread_lock:
            if self._depth == 0:
                self._acquire()
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if self._depth == 0:
                    self._release()


class InMemoryConsentLog:
    """For tests: the same behaviour without a file."""

    def __init__(self) -> None:
        self._events: list[ConsentEvent] = []
        self.exclusive = _Reentrant(lambda: None, lambda: None)
        self.fail_reads = False  # a test switch: make `read` fail as an unreadable file would

    def exists(self) -> bool:
        return bool(self._events)

    def read(self) -> LogContents:
        with self.exclusive():
            if self.fail_reads:
                raise ConsentLogUnavailableError("the consent log could not be read")
            return LogContents(tuple(self._events))

    def append(self, event: ConsentEvent, *, expected_last_seq: int) -> None:
        with self.exclusive():
            last = self._events[-1].seq if self._events else 0
            if last != expected_last_seq:
                raise ConsentConflictError("the consent log moved on; nothing was written")
            self._events.append(event)

    def replace_events(self, events: list[ConsentEvent]) -> None:
        """A test helper: put the log in any state, as a tamperer would."""
        self._events = list(events)


class FileConsentLog:
    def __init__(self, path: Path, *, lock_timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._path = Path(path)
        file_lock = ExclusiveFileLock(self._path.with_suffix(".lock"), timeout=lock_timeout)
        self.exclusive = _Reentrant(file_lock.acquire, file_lock.release)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def read(self) -> LogContents:
        with self.exclusive():
            return self._read()

    def append(self, event: ConsentEvent, *, expected_last_seq: int) -> None:
        with self.exclusive():
            contents = self._read()
            last = contents.events[-1].seq if contents.events else 0
            if last != expected_last_seq:
                raise ConsentConflictError("the consent log moved on; nothing was written")
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                if contents.torn_tail:
                    self._cut_unfinished_line()
                with open(self._path, "ab") as handle:
                    handle.write(to_line(event).encode("utf-8") + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                raise ConsentLogUnavailableError("the consent log could not be written") from None

    # ---- file ----------------------------------------------------------------------------

    def _read(self) -> LogContents:
        try:
            raw = self._path.read_bytes() if self._path.exists() else b""
        except OSError:
            raise ConsentLogUnavailableError("the consent log could not be read") from None
        if not raw:
            return LogContents(())
        torn = not raw.endswith(b"\n")
        lines = raw.split(b"\n")
        lines.pop()  # what follows the last newline: empty, or the unfinished line
        try:
            events = tuple(from_line(line.decode("utf-8")) for line in lines)
        except UnicodeDecodeError:
            raise ConsentFormatError("a log line is not valid text") from None
        return LogContents(events, torn_tail=torn)

    def _cut_unfinished_line(self) -> None:
        """Drop the bytes after the last newline: a write that never finished and never counted."""
        raw = self._path.read_bytes()
        keep = raw.rfind(b"\n") + 1
        with open(self._path, "r+b") as handle:
            handle.truncate(keep)
            handle.flush()
            os.fsync(handle.fileno())
