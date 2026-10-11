"""The consent registry under attack and under failure.

Three promises are tested here:

1. **Tampering shows.** Changing, deleting, reordering, inserting or truncating what the registry
   keeps is detected, and a detected problem blocks every contact.
2. **Failure blocks.** Anything that stops the registry from being read or verified, whatever the
   cause, blocks every contact; nothing is guessed, repaired or let through.
3. **Contacts never leak.** A phone number or an address never appears in a log record, a message, a
   representation, the audit or anything written to disk. Only keyed digests do.

What is *not* promised is tested too, so the documentation cannot drift from the truth: restoring the
log and the credential store together is not detected.
"""

import json
import logging
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from consent_support import (
    CLIENT,
    EMAIL,
    LEAKY,
    OTHER,
    PHONE,
    SECOND_PHONE,
    THIRD_PHONE,
    contact,
    make_world,
)

from sales_app.secret_store import SecretStoreCorruptError
from sales_app.sourcing.consent import (
    ConsentConflictError,
    ConsentKeys,
    ConsentLockTimeoutError,
    ConsentUnavailableError,
    ConsentWriter,
    EventType,
    Health,
    Origin,
    ReasonCode,
    new_root_key,
    record_refusals,
    verify_chain,
)
from sales_app.sourcing.consent.events import sign_event, to_line
from sales_app.sourcing.consent.filelock import ExclusiveFileLock
from sales_core.contracts import ContactInfo

ROOT_DIR = Path(__file__).resolve().parents[2]
NEVER_SEEN = contact("+216 99 000 999")


def three_events(tmp_path):
    world = make_world(tmp_path)
    for number in (PHONE, SECOND_PHONE, THIRD_PHONE):
        world.block(contact(number))
    return world


def assert_blocks_everyone(world, reason, *, health=Health.CORRUPT):
    snapshot = world.registry.snapshot()
    assert snapshot.health is health and snapshot.reason == reason
    assert not snapshot.healthy
    for anyone in (contact(PHONE), contact(SECOND_PHONE), NEVER_SEEN, ContactInfo()):
        assert snapshot.blocks(anyone), "a contact got through an unhealthy registry"
    return snapshot


def flip(text: str) -> str:
    return text[:-1] + ("A" if text[-1] != "A" else "B")


def forged_tail(world, *, type_=EventType.RELEASE, reason=ReasonCode.OTHER, root=None):
    """An event an attacker could write: well shaped, linked to the real tail, signed with the
    wrong key (they do not have the right one)."""
    wrong = ConsentKeys.from_stored(root or new_root_key(), CLIENT)
    events = world.registry.events()
    mine = ConsentKeys.from_stored(world.store.get(world.scope, "hmac_key"), CLIENT)
    forged = sign_event(
        wrong,
        seq=len(events) + 1,
        key=mine.digests(contact(PHONE))[0],  # the digest of someone really blocked
        type=type_,
        origin=Origin.OPERATOR,
        at=world.clock(),
        actor="cli:attacker",
        reason=reason,
        prev_mac=events[-1].mac,
    )
    return to_line(forged)


# ---- 1. tampering shows ----------------------------------------------------------------------

TAMPERINGS = {
    # what changed in the log
    "a reason changed": (lambda w: w.edit_line(1, reason="other"), "mac_invalid"),
    "an actor changed": (lambda w: w.edit_line(1, actor="cli:evil"), "mac_invalid"),
    "a digest changed": (lambda w: w.edit_line(0, key="A" * 43), "mac_invalid"),
    "a signature flipped": (lambda w: w.edit_line(2, mac=flip(w.registry.events()[2].mac)), "mac_invalid"),
    "a time changed": (lambda w: w.edit_line(1, at="2030-01-01T00:00:00.000000+00:00"), "mac_invalid"),
    "an origin changed": (lambda w: w.edit_line(0, origin="import"), "mac_invalid"),
    "an evidence changed": (lambda w: w.edit_line(0, evidence_ref="forged"), "mac_invalid"),
    "a link changed": (lambda w: w.edit_line(1, prev_mac="A" * 43), "chain_broken"),
    "the middle event deleted": (lambda w: w.rewrite([w.line_texts()[0], w.line_texts()[2]]), "seq_gap"),
    "the first event deleted": (lambda w: w.rewrite(w.line_texts()[1:]), "seq_gap"),
    "the last event deleted": (lambda w: w.rewrite(w.line_texts()[:2]), "anchor_ahead"),
    "every event deleted": (lambda w: w.rewrite([], newline=False), "anchor_ahead"),
    "the file deleted": (lambda w: w.log.path.unlink(), "anchor_ahead"),
    "an event repeated": (lambda w: w.rewrite([*w.line_texts()[:2], w.line_texts()[1], w.line_texts()[2]]), "seq_gap"),
    "two events swapped": (lambda w: w.rewrite([w.line_texts()[0], w.line_texts()[2], w.line_texts()[1]]), "seq_gap"),
    "a release forged without the key": (lambda w: w.rewrite([*w.line_texts(), forged_tail(w)]), "mac_invalid"),
    "an opt-out forged without the key": (
        lambda w: w.rewrite([*w.line_texts(), forged_tail(w, type_=EventType.OPT_OUT, reason=ReasonCode.KEYWORD)]),
        "mac_invalid",
    ),
    "garbage appended": (lambda w: w.rewrite([*w.line_texts(), "not json"]), "format_invalid"),
    "a field added": (lambda w: w.edit_line(0, extra="x"), "format_invalid"),
    "a blank line inserted": (lambda w: w.rewrite([w.line_texts()[0], "", *w.line_texts()[1:]]), "format_invalid"),
    "a line of the wrong type": (lambda w: w.rewrite([*w.line_texts()[:2], "[1, 2]"]), "format_invalid"),
    # what changed in the secret store
    "the key removed": (lambda w: w.store.delete(w.scope, "hmac_key"), "key_missing"),
    "the anchor removed": (lambda w: w.store.delete(w.scope, "anchor"), "anchor_missing"),
    "the key replaced by another": (lambda w: w.store.set(w.scope, "hmac_key", new_root_key(), by="x"), "chain_broken"),
    "the key malformed": (lambda w: w.store.set(w.scope, "hmac_key", "not-a-key", by="x"), "key_invalid"),
    "the anchor malformed": (lambda w: w.store.set(w.scope, "anchor", "3", by="x"), "anchor_invalid"),
    "the anchor with the wrong signature": (lambda w: w.store.set(w.scope, "anchor", f"3.{'A' * 43}", by="x"), "anchor_mismatch"),
    "the anchor ahead of the log": (lambda w: w.store.set(w.scope, "anchor", f"4.{w.registry.events()[2].mac}", by="x"), "anchor_ahead"),
}  # fmt: skip


class TestTamperingShows:
    @pytest.mark.parametrize("name", sorted(TAMPERINGS))
    def test_each_tampering_is_detected_and_blocks_every_contact(self, tmp_path, name):
        world = three_events(tmp_path)
        assert world.registry.snapshot().healthy
        change, reason = TAMPERINGS[name]
        change(world)
        assert_blocks_everyone(world, reason)

    @pytest.mark.parametrize("name", sorted(TAMPERINGS))
    def test_an_unhealthy_registry_records_nothing_and_changes_nothing(self, tmp_path, name):
        world = three_events(tmp_path)
        TAMPERINGS[name][0](world)
        before_log = world.log.path.read_bytes() if world.log.path.exists() else None
        before_anchor = world.store.get(world.scope, "anchor")
        before_key = world.store.get(world.scope, "hmac_key")
        with pytest.raises(ConsentUnavailableError):
            world.block(NEVER_SEEN)
        after_log = world.log.path.read_bytes() if world.log.path.exists() else None
        assert after_log == before_log
        assert world.store.get(world.scope, "anchor") == before_anchor
        assert world.store.get(world.scope, "hmac_key") == before_key

    def test_the_untouched_registry_is_healthy_and_blocks_only_who_it_should(self, tmp_path):
        world = three_events(tmp_path)
        snapshot = world.registry.snapshot()
        assert snapshot.healthy
        assert snapshot.blocks(contact(PHONE)) and not snapshot.blocks(NEVER_SEEN)

    def test_a_log_of_another_client_is_not_accepted(self, tmp_path):
        mine = three_events(tmp_path / "mine")
        theirs = make_world(tmp_path / "theirs", client_id=OTHER)
        theirs.block()
        mine.rewrite(theirs.line_texts())
        assert_blocks_everyone(
            mine, "chain_broken"
        )  # it does not even start from this client's origin

    def test_a_forged_release_cannot_unblock_anyone(self, tmp_path):
        world = three_events(tmp_path)
        world.rewrite([*world.line_texts(), forged_tail(world)])
        assert world.registry.snapshot().blocks(contact(PHONE))

    def test_the_chain_check_alone_catches_every_change_to_an_event(self, tmp_path):
        world = three_events(tmp_path)
        keys = ConsentKeys.from_stored(world.store.get(world.scope, "hmac_key"), CLIENT)
        events = list(world.registry.events())
        assert verify_chain(keys, events).ok
        assert not verify_chain(keys, events[:1] + events[2:]).ok
        assert not verify_chain(keys, [events[1], events[0], events[2]]).ok
        assert not verify_chain(ConsentKeys.from_stored(new_root_key(), CLIENT), events).ok


class TestWhatIsHealthyDespiteOddities:
    def test_an_anchor_behind_valid_events_is_healthy_with_a_warning_and_catches_up(self, tmp_path):
        world = three_events(tmp_path)
        events = world.registry.events()
        world.store.set(world.scope, "anchor", f"2.{events[1].mac}", by="x")
        report = world.registry.report()
        assert report.health is Health.OK and report.anchor_behind
        world.block(NEVER_SEEN)
        assert not world.registry.report().anchor_behind
        assert world.store.get(world.scope, "anchor").startswith("4.")

    def test_an_unfinished_last_line_is_ignored_and_removed_by_the_next_write(self, tmp_path):
        world = three_events(tmp_path)
        with open(world.log.path, "ab") as handle:
            handle.write(b'{"actor":"cli:te')  # a write that never finished
        report = world.registry.report()
        assert report.health is Health.OK and report.torn_tail and report.events == 3
        assert world.registry.snapshot().blocks(contact(PHONE))
        world.block(NEVER_SEEN)
        assert not world.registry.report().torn_tail
        assert len(world.line_texts()) == 4
        assert all(json.loads(line) for line in world.line_texts())

    def test_an_event_cut_in_half_after_it_was_acknowledged_is_a_truncation(self, tmp_path):
        world = three_events(tmp_path)
        raw = world.log.path.read_bytes()
        last = raw.rstrip(b"\n").rfind(b"\n") + 1
        world.log.path.write_bytes(raw[: last + 20])  # the third event, cut short
        assert_blocks_everyone(world, "anchor_ahead")


class TestLimitsAreTold:
    def test_restoring_the_log_and_the_anchor_together_is_not_detected(self, tmp_path):
        """The documented limit: someone who can put back, consistently, both the log and the secret
        store holding the key and the anchor can roll the registry back unnoticed. Nothing here claims
        otherwise."""
        world = make_world(tmp_path)
        world.block(contact(PHONE))
        old_log = world.log.path.read_bytes()
        old_anchor = world.store.get(world.scope, "anchor")
        world.block(contact(SECOND_PHONE))
        assert world.registry.snapshot().blocks(contact(SECOND_PHONE))
        world.log.path.write_bytes(old_log)
        world.store.set(world.scope, "anchor", old_anchor, by="attacker")
        snapshot = world.registry.snapshot()
        assert snapshot.healthy  # undetected
        assert not snapshot.blocks(contact(SECOND_PHONE))  # and the second refusal is gone

    def test_the_protection_note_is_written_where_people_read_it(self):
        import sales_app.consent_cli as cli
        from sales_app.sourcing.consent import events, registry

        assert "restore" in events.__doc__ and "together" in events.__doc__
        assert "does not protect" in registry.__doc__
        assert "restore the log and the credential store together" in cli.PROTECTION_NOTE


# ---- 2. failure blocks -------------------------------------------------------------------------


class TestFailureBlocks:
    def test_a_log_that_cannot_be_read_blocks_everyone(self, tmp_path):
        world = make_world()  # in memory
        world.block()
        world.log.fail_reads = True
        assert_blocks_everyone(world, "log_unreadable", health=Health.UNAVAILABLE)
        with pytest.raises(ConsentUnavailableError):
            world.block(NEVER_SEEN)

    def test_a_log_file_that_is_not_a_file_blocks_everyone(self, tmp_path):
        world = three_events(tmp_path)
        world.log.path.unlink()
        world.log.path.mkdir()
        assert_blocks_everyone(world, "log_unreadable", health=Health.UNAVAILABLE)

    def test_a_log_locked_by_another_process_blocks_everyone_instead_of_waiting_for_ever(
        self, tmp_path
    ):
        world = three_events(tmp_path)
        holder = ExclusiveFileLock(world.log.path.with_suffix(".lock"), timeout=1)
        holder.acquire()
        try:
            short = make_world(
                tmp_path, store=world.store, audit=world.audit, initialise=False, lock_timeout=0.2
            )
            assert_blocks_everyone(short, "lock_timeout", health=Health.UNAVAILABLE)
            with pytest.raises(ConsentUnavailableError):
                short.block(NEVER_SEEN)
        finally:
            holder.release()
        assert world.registry.snapshot().healthy

    def test_a_secret_store_that_fails_blocks_everyone(self, tmp_path):
        world = three_events(tmp_path)

        def broken(*args, **kwargs):
            raise SecretStoreCorruptError("unreadable")

        world.store.get = broken  # type: ignore[method-assign]
        assert_blocks_everyone(world, "store_unavailable", health=Health.UNAVAILABLE)

    def test_something_nobody_foresaw_blocks_everyone_too(self, tmp_path, monkeypatch):
        world = three_events(tmp_path)

        def boom(self):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("sales_app.sourcing.consent.anchor.ConsentSecrets.get_anchor", boom)
        assert_blocks_everyone(world, "internal_error", health=Health.UNAVAILABLE)

    def test_a_registry_nobody_set_up_blocks_everyone(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        assert_blocks_everyone(world, "not_initialised", health=Health.NOT_INITIALISED)

    def test_a_recovered_registry_stops_blocking_by_itself_when_it_is_healthy_again(self, tmp_path):
        world = three_events(tmp_path)
        saved = world.log.path.read_bytes()
        world.log.path.write_bytes(saved[:-5])
        assert not world.registry.snapshot().healthy
        world.log.path.write_bytes(saved)
        assert world.registry.snapshot().healthy and not world.registry.snapshot().blocks(
            NEVER_SEEN
        )

    def test_a_registry_that_cannot_record_says_so_instead_of_pretending(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        with pytest.raises(ConsentUnavailableError) as raised:
            record_refusals(world.registry, [contact()])
        assert raised.value.health == "not_initialised"

    def test_the_door_given_to_the_rest_of_the_application_cannot_lift_a_block(self):
        names = {n for n in dir(ConsentWriter) if not n.startswith("_")}
        assert {"snapshot", "record_opt_out"} <= names
        assert not any("release" in n or "unblock" in n or "delete" in n for n in names)

    def test_a_snapshot_cannot_be_used_to_lift_a_block(self, tmp_path):
        snapshot = three_events(tmp_path).registry.snapshot()
        assert not any(n for n in dir(snapshot) if "release" in n or "remove" in n or "clear" in n)


# ---- 3. contacts never leak --------------------------------------------------------------------


class TestContactsNeverLeak:
    def surfaces(self, world, caplog, capsys, extra):
        out, err = capsys.readouterr()
        parts = [
            caplog.text,
            out,
            err,
            repr(world.audit.recent()),
            repr(world.store),
            repr(world.registry),
        ]
        if world.log.path.exists():
            parts.append(world.log.path.read_text(encoding="utf-8"))
        parts.extend(extra)
        return "\n".join(parts)

    def test_no_surface_ever_holds_a_contact(self, tmp_path, caplog, capsys):
        caplog.set_level(logging.DEBUG)
        world = make_world(tmp_path)
        seen = []
        both = contact(PHONE, EMAIL)
        world.block(both, evidence_ref="ticket 77")
        world.block(both)  # idempotent path
        seen += [repr(world.registry.snapshot()), repr(world.registry.report())]
        seen += [repr(e) for e in world.registry.history(both)]
        seen += [repr(e) for e in world.registry.events()]
        snapshot = world.registry.snapshot()
        assert snapshot.blocks(both)
        # failures, with their messages and their representations
        world.rewrite([*world.line_texts(), "not json"])
        with pytest.raises(ConsentUnavailableError) as raised:
            world.block(both)
        seen += [str(raised.value), repr(raised.value), repr(raised.value.__dict__)]
        seen.append(repr(world.registry.snapshot()))
        world.rewrite(world.line_texts()[:-1])
        world.log.path.unlink()
        with pytest.raises(ConsentUnavailableError) as raised:
            world.block(both)
        seen += [str(raised.value), repr(raised.value)]
        text = self.surfaces(world, caplog, capsys, seen)
        for leaky in LEAKY:
            assert leaky not in text, f"{leaky!r} leaked"
        assert "consent" in text  # the surfaces were really collected

    def test_the_key_never_appears_anywhere_but_the_secret_store(self, tmp_path, caplog, capsys):
        caplog.set_level(logging.DEBUG)
        world = three_events(tmp_path)
        key = world.store.get(world.scope, "hmac_key")
        keys = ConsentKeys.from_stored(key, CLIENT)
        text = self.surfaces(
            world, caplog, capsys, [repr(keys), str(keys), repr(world.registry.snapshot())]
        )
        assert key not in text
        raw = __import__("base64").urlsafe_b64decode(key + "=")
        assert raw.hex() not in text

    def test_the_digests_are_in_the_log_but_not_in_the_audit(self, tmp_path):
        world = three_events(tmp_path)
        digests = {e.key for e in world.registry.events()}
        assert digests and all(d in world.log.path.read_text(encoding="utf-8") for d in digests)
        assert not any(d in repr(world.audit.recent()) for d in digests)

    def test_what_is_written_to_disk_has_no_contact_in_any_spelling(self, tmp_path):
        world = make_world(tmp_path)
        world.block(contact(PHONE, EMAIL))
        for path in tmp_path.rglob("*"):
            if path.is_file():
                data = path.read_bytes().decode("utf-8", "replace")
                for leaky in LEAKY:
                    assert leaky not in data, f"{leaky!r} found in {path.name}"

    def test_the_library_does_not_log_and_does_not_print(self):
        """A static guarantee that no future change can quietly start logging a contact."""
        import ast

        package = ROOT_DIR / "src" / "sales_app" / "sourcing" / "consent"
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert "logging" not in {a.name for a in node.names}, path.name
                if isinstance(node, ast.ImportFrom):
                    assert node.module != "logging", path.name
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in {"print", "breakpoint"}, path.name


# ---- 4. several writers --------------------------------------------------------------------------


class TestSeveralWriters:
    def test_threads_recording_at_once_leave_a_valid_chain_without_a_gap(self, tmp_path):
        first = make_world(tmp_path)
        second = make_world(tmp_path, store=first.store, audit=first.audit, initialise=False)
        errors: list[BaseException] = []

        def work(world, offset):
            try:
                for i in range(6):
                    world.block(contact(f"+216 98 {offset:03d} {i:03d}"))
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [
            threading.Thread(target=work, args=(first if n % 2 else second, n)) for n in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        report = first.registry.report()
        assert report.health is Health.OK and report.events == 36 and report.blocked == 36
        assert [e.seq for e in first.registry.events()] == list(range(1, 37))

    def test_an_append_that_is_not_based_on_the_latest_event_is_refused(self, tmp_path):
        world = three_events(tmp_path)
        keys = ConsentKeys.from_stored(world.store.get(world.scope, "hmac_key"), CLIENT)
        events = world.registry.events()
        stale = sign_event(
            keys, seq=3, key=keys.digest("phone:1"), type=EventType.OPT_OUT, origin=Origin.OPERATOR,
            at=world.clock(), actor="cli:tester", reason=ReasonCode.OTHER, prev_mac=events[1].mac,
        )  # fmt: skip
        before = world.log.path.read_bytes()
        with pytest.raises(ConsentConflictError):
            world.log.append(stale, expected_last_seq=2)  # the log is already at 3
        assert world.log.path.read_bytes() == before

    def test_a_lock_is_exclusive_waits_and_is_never_deleted(self, tmp_path):
        path = tmp_path / "x.lock"
        first, second = ExclusiveFileLock(path), ExclusiveFileLock(path, timeout=0.2)
        first.acquire()
        with pytest.raises(ConsentLockTimeoutError):
            second.acquire()
        with pytest.raises(RuntimeError):
            first.acquire()
        first.release()
        assert path.exists()  # deleting it would reopen a race on POSIX
        second.acquire()
        second.release()
        assert path.exists()

    def test_processes_taking_the_lock_never_overlap(self, tmp_path):
        counter = tmp_path / "counter.txt"
        counter.write_text("0")
        script = tmp_path / "count.py"
        script.write_text(
            textwrap.dedent(
                """
                import sys
                from sales_app.sourcing.consent.filelock import ExclusiveFileLock
                lock = ExclusiveFileLock(sys.argv[1] + ".lock", timeout=30)
                for _ in range(40):
                    lock.acquire()
                    try:
                        value = int(open(sys.argv[1]).read())
                        open(sys.argv[1], "w").write(str(value + 1))
                    finally:
                        lock.release()
                """
            )
        )
        run_processes(script, [[str(counter)]] * 4)
        assert counter.read_text() == "160"

    def test_processes_appending_to_the_log_leave_a_valid_chain(self, tmp_path):
        from sales_app.sourcing.consent.log import FileConsentLog

        path = tmp_path / "consent" / "client-a.jsonl"
        root = new_root_key()
        script = tmp_path / "append.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import sys
                from datetime import datetime, UTC
                from sales_app.sourcing.consent import ConsentKeys, FileConsentLog
                from sales_app.sourcing.consent import EventType, Origin, ReasonCode
                from sales_app.sourcing.consent.events import sign_event
                keys = ConsentKeys.from_stored({root!r}, "client-a")
                log = FileConsentLog(sys.argv[1], lock_timeout=30)
                me = sys.argv[2]
                for i in range(15):
                    with log.exclusive():
                        events = log.read().events
                        previous = events[-1].mac if events else keys.genesis_mac()
                        event = sign_event(
                            keys, seq=len(events) + 1, key=keys.digest(f"phone:{{me}}{{i}}"),
                            type=EventType.OPT_OUT, origin=Origin.OPERATOR,
                            at=datetime.now(UTC), actor="cli:proc", reason=ReasonCode.OTHER,
                            prev_mac=previous,
                        )
                        log.append(event, expected_last_seq=len(events))
                """
            )
        )
        run_processes(script, [[str(path), tag] for tag in ("1", "2", "3", "4")])
        events = FileConsentLog(path).read().events
        assert len(events) == 60
        keys = ConsentKeys.from_stored(root, "client-a")
        assert verify_chain(keys, list(events)).ok
        assert len({e.key for e in events}) == 60


def run_processes(script: Path, jobs: list[list[str]]) -> None:
    """Run `script` once per job, all at the same time, and require every one to succeed."""
    env = {**os.environ, "PYTHONPATH": str(ROOT_DIR / "src")}
    processes = [
        subprocess.Popen(
            [sys.executable, str(script), *job],
            env=env,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for job in jobs
    ]
    for process in processes:
        _, stderr = process.communicate(timeout=180)
        assert process.returncode == 0, stderr.decode("utf-8", "replace")[-2000:]
