"""The ConfigStore port: one contract, run against every implementation, plus file-store specifics."""

import json
import threading
from datetime import UTC, datetime

import pytest

from sales_app.config_store import (
    ConfigConflictError,
    ConfigCorruptError,
    ConfigStore,
    ConfigStoreError,
    FileConfigStore,
    InMemoryConfigStore,
)

DOC = {"display_name": "Alpha", "daily_action_limit": 20, "tags": ["a", "b"], "nested": {"k": 1}}
FIXED = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path) -> ConfigStore:
    if request.param == "file":
        return FileConfigStore(tmp_path / "config", now=lambda: FIXED)
    return InMemoryConfigStore(now=lambda: FIXED)


def publish(store, document=DOC, parent=None, client="alpha", **kw):
    return store.publish(client, document, author="tester", expected_parent=parent, **kw)


class TestContract:
    def test_both_implementations_satisfy_the_port(self, store):
        assert isinstance(store, ConfigStore)

    def test_a_client_with_nothing_published(self, store):
        assert store.current("alpha") is None
        assert store.get("alpha", 1) is None
        assert store.history("alpha") == ()

    def test_versions_are_numbered_from_one_and_linked_to_their_parent(self, store):
        first = publish(store, note="first")
        second = publish(store, {"display_name": "Alpha 2"}, parent=1)
        assert (first.version, first.parent) == (1, None)
        assert (second.version, second.parent) == (2, 1)
        assert (first.author, first.note, first.created_at) == ("tester", "first", FIXED)

    def test_current_get_and_history(self, store):
        publish(store)
        publish(store, {"display_name": "B"}, parent=1)
        publish(store, {"display_name": "C"}, parent=2)
        assert store.current("alpha").document["display_name"] == "C"
        assert store.get("alpha", 1).document["display_name"] == "Alpha"
        assert [v.version for v in store.history("alpha")] == [1, 2, 3]
        assert store.get("alpha", 4) is None and store.get("alpha", 0) is None

    def test_the_document_round_trips(self, store):
        publish(store)
        stored = store.current("alpha").document
        assert stored["tags"] == ("a", "b") and stored["nested"]["k"] == 1
        assert json.loads(json.dumps({"tags": list(stored["tags"])})) == {"tags": ["a", "b"]}

    def test_a_stale_view_of_the_latest_version_is_refused_and_writes_nothing(self, store):
        publish(store)
        with pytest.raises(ConfigConflictError):
            publish(store, {"display_name": "x"}, parent=None)  # believes nothing exists
        with pytest.raises(ConfigConflictError):
            publish(store, {"display_name": "x"}, parent=5)
        assert [v.version for v in store.history("alpha")] == [1]

    def test_versions_cannot_be_changed_through_what_was_returned_or_passed(self, store):
        source = {"display_name": "Alpha", "nested": {"k": 1}, "items": [1]}
        record = publish(store, source)
        source["nested"]["k"] = 99  # the caller's own object
        source["items"].append(2)
        with pytest.raises(TypeError):
            record.document["display_name"] = "changed"
        with pytest.raises(TypeError):
            record.document["nested"]["k"] = 2
        assert store.current("alpha").document["nested"]["k"] == 1
        assert store.current("alpha").document["items"] == (1,)

    @pytest.mark.parametrize(
        "bad", [{"x": object()}, {"x": {1, 2}}, {1: "non-text key"}, {"x": float("nan")}]
    )
    def test_documents_that_are_not_json_are_refused_and_write_nothing(self, store, bad):
        with pytest.raises((TypeError, ValueError)):
            publish(store, bad)
        assert store.current("alpha") is None

    @pytest.mark.parametrize("bad_id", ["../x", "a/b", "", "Alpha", "-x", "a b"])
    def test_client_ids_cannot_escape_or_be_malformed(self, store, bad_id):
        with pytest.raises(ValueError):
            store.current(bad_id)
        with pytest.raises(ValueError):
            publish(store, client=bad_id)

    def test_clients_are_isolated(self, store):
        publish(store, client="alpha")
        publish(store, {"display_name": "Beta"}, client="beta")
        assert store.current("beta").version == 1
        assert store.current("beta").document["display_name"] == "Beta"
        assert store.current("gamma") is None


class TestFileStore:
    @pytest.fixture
    def root(self, tmp_path):
        return tmp_path / "config"

    @pytest.fixture
    def files(self, root):
        return root / "alpha" / "versions"

    def test_one_json_file_per_version_and_no_temporary_files_left(self, root, files):
        store = FileConfigStore(root, now=lambda: FIXED)
        publish(store)
        publish(store, {"display_name": "B"}, parent=1)
        assert sorted(p.name for p in files.iterdir()) == ["000001.json", "000002.json"]
        envelope = json.loads((files / "000002.json").read_text(encoding="utf-8"))
        assert envelope["client_id"] == "alpha" and envelope["parent"] == 1
        assert envelope["author"] == "tester" and envelope["created_at"].startswith("2026-05-01")
        assert envelope["document"] == {"display_name": "B"} and len(envelope["checksum"]) == 64

    def test_reading_never_creates_anything(self, root):
        store = FileConfigStore(root)
        assert store.current("alpha") is None and store.history("alpha") == ()
        assert not root.exists()

    def test_versions_survive_a_new_instance(self, root):
        publish(FileConfigStore(root))
        assert FileConfigStore(root).current("alpha").document["display_name"] == "Alpha"

    def test_stray_files_are_ignored(self, root, files):
        store = FileConfigStore(root)
        publish(store)
        (files / "notes.txt").write_text("hello")
        (files / ".tmp-abandoned").write_text("{")
        assert [v.version for v in store.history("alpha")] == [1]
        assert publish(store, {"display_name": "B"}, parent=1).version == 2

    def test_a_file_edited_after_publishing_is_detected(self, root, files):
        store = FileConfigStore(root)
        publish(store)
        path = files / "000001.json"
        text = path.read_text(encoding="utf-8")
        assert '"daily_action_limit": 20' in text
        path.write_text(
            text.replace('"daily_action_limit": 20', '"daily_action_limit": 999'), encoding="utf-8"
        )
        with pytest.raises(ConfigCorruptError, match="checksum"):
            store.current("alpha")

    @pytest.mark.parametrize(
        "content", ["", "{", "[]", '{"format": 99}', '{"format": 1}', "not json at all"]
    )
    def test_unreadable_or_foreign_files_are_corruption_not_guesses(self, root, files, content):
        store = FileConfigStore(root)
        publish(store)
        (files / "000001.json").write_text(content, encoding="utf-8")
        with pytest.raises(ConfigCorruptError):
            store.current("alpha")
        with pytest.raises(ConfigStoreError):
            store.history("alpha")

    def test_a_missing_version_in_the_middle_is_corruption(self, root, files):
        store = FileConfigStore(root)
        publish(store)
        publish(store, {"display_name": "B"}, parent=1)
        (files / "000001.json").unlink()
        with pytest.raises(ConfigCorruptError, match="numbered"):
            store.current("alpha")
        with pytest.raises(ConfigCorruptError):
            publish(store, {"display_name": "C"}, parent=2)

    def test_a_file_moved_under_another_version_number_is_detected(self, root, files):
        store = FileConfigStore(root)
        publish(store)
        publish(store, {"display_name": "B"}, parent=1)
        (files / "000002.json").write_bytes((files / "000001.json").read_bytes())
        with pytest.raises(ConfigCorruptError, match="does not match"):
            store.current("alpha")

    def test_a_corrupt_store_never_accepts_a_new_version(self, root, files):
        store = FileConfigStore(root)
        publish(store)
        (files / "000001.json").write_text("{", encoding="utf-8")
        with pytest.raises(ConfigCorruptError):
            store.publish("alpha", DOC, author="t", expected_parent=1)
        assert sorted(p.name for p in files.iterdir()) == ["000001.json"]

    def test_only_one_of_many_racing_publishers_wins_even_across_instances(self, root):
        stores = [FileConfigStore(root) for _ in range(8)]  # separate instances: separate locks
        outcomes: list[object] = []
        barrier = threading.Barrier(len(stores))

        def race(index):
            barrier.wait()
            try:
                outcomes.append(
                    stores[index].publish(
                        "alpha", {"display_name": f"w{index}"}, author="t", expected_parent=None
                    )
                )
            except ConfigConflictError as error:
                outcomes.append(error)

        threads = [threading.Thread(target=race, args=(i,)) for i in range(len(stores))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [o for o in outcomes if not isinstance(o, Exception)]
        assert len(winners) == 1 and len(outcomes) == len(stores)
        assert [v.version for v in FileConfigStore(root).history("alpha")] == [1]

    def test_unwritable_location_is_a_store_error_not_a_crash(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory")
        with pytest.raises(ConfigStoreError):
            FileConfigStore(blocker).publish("alpha", DOC, author="t", expected_parent=None)
