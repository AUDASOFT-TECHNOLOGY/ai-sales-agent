from datetime import UTC, datetime

import pytest

from sales_app.client_profiles import (
    ClientProfile,
    ClientProfileStoreCorruptError,
    FileClientProfileStore,
    InMemoryClientProfileStore,
)
from sales_app.persistence import SqlClientProfileStore

NOW = datetime(2026, 5, 1, tzinfo=UTC)


def profile(client_id="acme", login="acme.admin", **kw):
    return ClientProfile(
        client_id=client_id, login=login, password_hash="$2b$hash", created_at=NOW, **kw
    )


@pytest.fixture(params=["memory", "file", "sql"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryClientProfileStore()
    if request.param == "sql":
        return SqlClientProfileStore(request.getfixturevalue("sql_engine"))
    return FileClientProfileStore(tmp_path / "profiles" / "profiles.json")


def test_create_get_and_list(store):
    store.create(profile("b", "b.login"))
    store.create(profile("a", "a.login", logo="data:image/png;base64,AAAA"))
    assert store.get("a").logo == "data:image/png;base64,AAAA"
    assert store.get("missing") is None
    assert store.get_by_login(" A.LOGIN ").client_id == "a"
    assert [p.client_id for p in store.list()] == ["a", "b"]
    assert store.get("a").whatsapp_number is None
    assert store.get("a").must_change_password is True


def test_duplicate_client_or_login_is_refused(store):
    store.create(profile())
    with pytest.raises(ValueError):
        store.create(profile(login="other.login"))
    with pytest.raises(ValueError):
        store.create(profile(client_id="other"))


def test_a_profile_rejects_a_bad_login_or_naive_time():
    with pytest.raises(ValueError):
        profile(login="x")
    with pytest.raises(ValueError):
        naive = datetime(2026, 1, 1)  # noqa: DTZ001
        ClientProfile(client_id="a", login="abc", password_hash="h", created_at=naive)


def test_a_corrupt_file_is_reported_not_guessed_at(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ClientProfileStoreCorruptError):
        FileClientProfileStore(path).list()
