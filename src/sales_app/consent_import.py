"""Moving a client's consent file into the database, without losing or altering a single event.

`import_log` first reads the file (read only: no lock file is created, nothing is written, the file is
never removed), then checks the whole chain of signatures and the anchor with the client's own key,
exactly as the registry does when it starts. Nothing is written unless that check passes, the database
log agrees with the file on every event it already holds, and `apply` is true. What is written is the
file's events, unchanged and in order, one atomic append each; an interrupted import leaves a valid
prefix and running it again completes it. Afterwards the database log is read back and must equal the
file, and its chain must verify, or the import reports a failure.
"""

from dataclasses import dataclass, field
from pathlib import Path

from sales_app.sourcing.consent.anchor import ConsentSecrets
from sales_app.sourcing.consent.errors import ConsentError, ConsentFormatError
from sales_app.sourcing.consent.events import ConsentEvent, from_line
from sales_app.sourcing.consent.log import ConsentLog, InMemoryConsentLog
from sales_app.sourcing.consent.registry import ConsentRegistry, Health


@dataclass(slots=True)
class ImportResult:
    client_id: str
    file_events: int = 0
    already: int = 0
    to_import: int = 0
    imported: int = 0
    torn_tail: bool = False
    problems: list[str] = field(default_factory=list)
    applied: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> list[str]:
        out = [
            (
                f"{self.client_id}: {self.file_events} event(s) in the file, {self.already} "
                f"already in the database, {self.to_import} to copy"
            )
        ]
        if self.torn_tail:
            out.append(f"{self.client_id}: an unfinished last line in the file is ignored")
        out += [f"{self.client_id}: PROBLEM, {p}" for p in self.problems]
        if self.applied and self.ok:
            out.append(f"{self.client_id}: {self.imported} event(s) copied and verified")
        return out


def read_file_events(path: Path) -> tuple[tuple[ConsentEvent, ...], bool]:
    """The events of a consent file and whether it ends with an unfinished line. Read only."""
    if not path.exists():
        return (), False
    raw = path.read_bytes()
    if not raw:
        return (), False
    torn = not raw.endswith(b"\n")
    lines = raw.split(b"\n")
    lines.pop()
    try:
        return tuple(from_line(line.decode("utf-8")) for line in lines), torn
    except UnicodeDecodeError:
        raise ConsentFormatError("a log line is not valid text") from None


def import_log(
    client_id: str,
    path: Path,
    target: ConsentLog,
    secrets: ConsentSecrets,
    *,
    apply: bool,
) -> ImportResult:
    result = ImportResult(client_id)
    try:
        events, result.torn_tail = read_file_events(path)
    except ConsentError as error:
        result.problems.append(f"the file cannot be read ({error})")
        return result
    result.file_events = len(events)
    if not events:
        result.problems.append(
            "there is no consent file to import" if not path.exists() else "the file holds no event"
        )
        return result

    # 1. the chain and the anchor, with the client's own key, before anything else
    scratch = InMemoryConsentLog()
    scratch.replace_events(list(events))
    verdict = ConsentRegistry(client_id, scratch, secrets).report()
    if verdict.health is not Health.OK:
        result.problems.append(
            f"the file's chain does not verify ({verdict.reason or verdict.health.value})"
        )
        return result

    # 2. what the database already holds must be exactly the start of the file
    held = target.read().events
    if len(held) > len(events) or any(held[i] != events[i] for i in range(len(held))):
        result.problems.append(
            "the database holds events that the file does not: nothing is touched"
        )
        return result
    result.already = len(held)
    result.to_import = len(events) - len(held)

    if not apply or result.to_import == 0:
        return result

    # 3. copy, in order; each append is atomic and refuses if the log moved on
    for event in events[len(held) :]:
        target.append(event, expected_last_seq=event.seq - 1)
        result.imported += 1
    result.applied = True

    # 4. read back: identical to the file, and the chain verifies from the database itself
    stored = target.read().events
    if stored != events:
        result.problems.append("the database does not read back identical to the file")
    elif ConsentRegistry(client_id, target, secrets).report().health is not Health.OK:
        result.problems.append("the database log does not verify after the import")
    return result
