"""The AdminUserStore and AdminSessionStore ports: one contract, run against every
implementation, plus file-store specifics (atomic writes, corruption)."""

from datetime import UTC, datetime, timedelta

import pytest

from sales_app.admin_auth import (
    AdminRole,
    AdminSession,
    AdminSessionStore,
    AdminStoreCorruptError,
    AdminUser,
    AdminUserStore,
    FileAdminSessionStore,
    FileAdminUserStore,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
)

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


def user(username="alice", *, role=AdminRole.EDITOR, client_ids=frozenset({"alpha"}), **kw):
    return AdminUser(
        id=kw.pop("id", f"id-{username}"),
        username=username,
        password_hash=kw.pop("password_hash", "hash"),
        role=role,
        client_ids=client_ids,
        created_at=kw.pop("created_at", NOW),
        **kw,
    )


def session(token_hash="a" * 64, *, admin_id="id-alice", **kw):
    return AdminSession(
        token_hash=token_hash,
        admin_id=admin_id,
        created_at=kw.pop("created_at", NOW),
        expires_at=kw.pop("expires_at", NOW + timedelta(hours=1)),
    )


# ---- AdminUser model -------------------------------------------------------------------------


class TestAdminUserModel:
    def test_owner_reaches_every_client_regardless_of_client_ids(self):
        owner = user("root", role=AdminRole.OWNER, client_ids=frozenset())
        assert owner.can_reach("anything") and owner.can_reach("")

    def test_other_roles_are_bound_to_their_explicit_allow_list(self):
        editor = user("alice", role=AdminRole.EDITOR, client_ids=frozenset({"alpha"}))
        assert editor.can_reach("alpha")
        assert not editor.can_reach("beta")

    def test_empty_username_or_id_is_rejected(self):
        with pytest.raises(ValueError):
            user(username="  ")
        with pytest.raises(ValueError):
            user(id="  ")

    def test_naive_created_at_is_rejected(self):
        with pytest.raises(ValueError):
            user(created_at=datetime(2026, 5, 1, 9, 30))  # noqa: DTZ001 - naive is the point


class TestAdminSessionModel:
    def test_expiry(self):
        s = session(expires_at=NOW + timedelta(hours=1))
        assert not s.is_expired(NOW)
        assert s.is_expired(NOW + timedelta(hours=1))
        assert s.is_expired(NOW + timedelta(hours=2))

    def test_expires_at_must_be_after_created_at(self):
        with pytest.raises(ValueError):
            session(created_at=NOW, expires_at=NOW)

    def test_token_hash_must_look_like_sha256(self):
        with pytest.raises(ValueError):
            session(token_hash="not-a-hash")


# ---- AdminUserStore contract -------------------------------------------------------------------


@pytest.fixture(params=["file", "memory"])
def user_store(request, tmp_path) -> AdminUserStore:
    if request.param == "file":
        return FileAdminUserStore(tmp_path / "admin" / "users.json")
    return InMemoryAdminUserStore()


class TestAdminUserStoreContract:
    def test_both_implementations_satisfy_the_port(self, user_store):
        assert isinstance(user_store, AdminUserStore)

    def test_nothing_stored_yet(self, user_store):
        assert user_store.get("id-alice") is None
        assert user_store.get_by_username("alice") is None
        assert user_store.list() == ()

    def test_create_then_read_back(self, user_store):
        user_store.create(user("alice"))
        assert user_store.get("id-alice").username == "alice"
        assert user_store.get_by_username("alice").id == "id-alice"

    def test_username_lookup_is_case_and_whitespace_insensitive(self, user_store):
        user_store.create(user("alice"))
        assert user_store.get_by_username(" Alice ").id == "id-alice"

    def test_list_is_ordered_by_username(self, user_store):
        user_store.create(user("zoe", id="id-zoe"))
        user_store.create(user("alice", id="id-alice"))
        assert [u.username for u in user_store.list()] == ["alice", "zoe"]

    def test_duplicate_username_is_rejected(self, user_store):
        user_store.create(user("alice", id="id-1"))
        with pytest.raises(ValueError):
            user_store.create(user("alice", id="id-2"))
        assert len(user_store.list()) == 1

    def test_duplicate_id_is_rejected(self, user_store):
        user_store.create(user("alice", id="id-1"))
        with pytest.raises(ValueError):
            user_store.create(user("bob", id="id-1"))

    def test_update_replaces_the_record(self, user_store):
        user_store.create(user("alice", role=AdminRole.VIEWER))
        user_store.update(user("alice", role=AdminRole.EDITOR))
        assert user_store.get("id-alice").role is AdminRole.EDITOR

    def test_update_of_an_unknown_account_raises(self, user_store):
        with pytest.raises(KeyError):
            user_store.update(user("ghost"))


class TestFileAdminUserStoreSpecifics:
    def test_data_survives_a_new_store_instance(self, tmp_path):
        path = tmp_path / "admin" / "users.json"
        FileAdminUserStore(path).create(user("alice"))
        reopened = FileAdminUserStore(path)
        assert reopened.get_by_username("alice") is not None

    def test_reading_never_creates_the_directory(self, tmp_path):
        path = tmp_path / "admin" / "users.json"
        FileAdminUserStore(path).get_by_username("alice")
        assert not path.parent.exists()

    def test_a_corrupt_file_is_reported_and_never_silently_accepted(self, tmp_path):
        path = tmp_path / "admin" / "users.json"
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(AdminStoreCorruptError):
            FileAdminUserStore(path).list()


# ---- AdminSessionStore contract -----------------------------------------------------------------


@pytest.fixture(params=["file", "memory"])
def session_store(request, tmp_path) -> AdminSessionStore:
    if request.param == "file":
        return FileAdminSessionStore(tmp_path / "admin" / "sessions")
    return InMemoryAdminSessionStore()


class TestAdminSessionStoreContract:
    def test_both_implementations_satisfy_the_port(self, session_store):
        assert isinstance(session_store, AdminSessionStore)

    def test_nothing_stored_yet(self, session_store):
        assert session_store.get("a" * 64) is None

    def test_create_then_read_back(self, session_store):
        session_store.create(session("b" * 64, admin_id="id-alice"))
        found = session_store.get("b" * 64)
        assert found is not None and found.admin_id == "id-alice"

    def test_delete_is_idempotent(self, session_store):
        session_store.delete("c" * 64)  # never created; must not raise
        session_store.create(session("c" * 64))
        session_store.delete("c" * 64)
        session_store.delete("c" * 64)
        assert session_store.get("c" * 64) is None

    def test_delete_all_for_removes_only_that_accounts_sessions(self, session_store):
        session_store.create(session("d" * 64, admin_id="id-alice"))
        session_store.create(session("e" * 64, admin_id="id-bob"))
        session_store.delete_all_for("id-alice")
        assert session_store.get("d" * 64) is None
        assert session_store.get("e" * 64) is not None


class TestFileAdminSessionStoreSpecifics:
    def test_reading_never_creates_the_directory(self, tmp_path):
        root = tmp_path / "admin" / "sessions"
        FileAdminSessionStore(root).get("a" * 64)
        assert not root.exists()

    def test_delete_all_for_on_an_empty_store_does_nothing(self, tmp_path):
        FileAdminSessionStore(tmp_path / "admin" / "sessions").delete_all_for("id-alice")

    def test_a_corrupt_session_file_is_reported(self, tmp_path):
        root = tmp_path / "admin" / "sessions"
        root.mkdir(parents=True)
        (root / f"{'a' * 64}.json").write_text("not json", encoding="utf-8")
        with pytest.raises(AdminStoreCorruptError):
            FileAdminSessionStore(root).get("a" * 64)
