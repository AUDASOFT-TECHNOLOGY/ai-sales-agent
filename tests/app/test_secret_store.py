"""The credential store: encryption at rest, the file and memory stores, the catalog rules, the
audit trail and the startup choice of store. The values used here are marker strings so every test
can also assert that they never appear where they must not."""

import json
import threading
from datetime import UTC, datetime

import pytest

from sales_app.secret_store import (
    GLOBAL_SCOPE,
    AesGcmCipher,
    CredentialService,
    DuplicateSecretError,
    FileSecretAudit,
    FileSecretStore,
    InMemorySecretAudit,
    InMemorySecretStore,
    InvalidCredentialError,
    InvalidMasterKeyError,
    SecretDecryptError,
    SecretStoreCorruptError,
    SecretStoreUnavailableError,
    UnavailableSecretStore,
    UnknownCredentialError,
    build_secret_store,
    client_scope,
    generate_master_key,
)
from sales_app.secret_store.catalog import CATALOG, ScopeKind, scope_kind

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
SECRET = "sk-LIVE-very-secret-marker-123"
TOKEN = "EAAG-token-marker-456"


def cipher() -> AesGcmCipher:
    return AesGcmCipher.from_text(generate_master_key())


# ---- master key and cipher ---------------------------------------------------------------------


class TestCipher:
    def test_round_trip(self):
        c = cipher()
        assert c.decrypt(c.encrypt(SECRET, "ctx"), "ctx") == SECRET

    def test_ciphertext_does_not_contain_the_plaintext(self):
        assert SECRET not in cipher().encrypt(SECRET, "ctx")

    def test_a_fresh_nonce_makes_two_encryptions_differ(self):
        c = cipher()
        assert c.encrypt(SECRET, "ctx") != c.encrypt(SECRET, "ctx")

    def test_another_context_cannot_open_it(self):
        c = cipher()
        with pytest.raises(SecretDecryptError):
            c.decrypt(c.encrypt(SECRET, "client:a|k"), "client:b|k")

    def test_another_key_cannot_open_it(self):
        with pytest.raises(SecretDecryptError):
            cipher().decrypt(cipher().encrypt(SECRET, "ctx"), "ctx")

    def test_garbage_is_a_decrypt_error_not_a_crash(self):
        with pytest.raises(SecretDecryptError):
            cipher().decrypt("not-base64-!!", "ctx")

    @pytest.mark.parametrize("bad", ["", "short", "!!!not base64!!!", "QUJD"])
    def test_a_malformed_master_key_is_refused_without_echoing_it(self, bad):
        with pytest.raises(InvalidMasterKeyError) as error:
            AesGcmCipher.from_text(bad)
        assert bad not in str(error.value) or bad == ""

    def test_repr_hides_the_key(self):
        text = generate_master_key()
        assert text not in repr(AesGcmCipher.from_text(text))

    def test_generated_keys_are_valid_and_distinct(self):
        a, b = generate_master_key(), generate_master_key()
        assert a != b
        AesGcmCipher.from_text(a)


# ---- both stores behave the same ---------------------------------------------------------------


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    clock = lambda: NOW
    if request.param == "file":
        return FileSecretStore(tmp_path / "secrets.json", cipher(), now=clock)
    return InMemorySecretStore(now=clock)


class TestStoreContract:
    def test_set_then_get(self, store):
        store.set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        assert store.get(GLOBAL_SCOPE, "openai.api_key") == SECRET

    def test_missing_is_none(self, store):
        assert store.get(GLOBAL_SCOPE, "openai.api_key") is None
        assert store.entry(GLOBAL_SCOPE, "openai.api_key") is None

    def test_entry_has_metadata_and_no_value(self, store):
        entry = store.set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        assert (entry.scope, entry.key, entry.updated_by, entry.updated_at) == (
            GLOBAL_SCOPE,
            "openai.api_key",
            "root",
            NOW,
        )
        assert SECRET not in repr(entry)
        assert not hasattr(entry, "value")

    def test_replace_overwrites(self, store):
        store.set(GLOBAL_SCOPE, "openai.api_key", "old", by="a")
        store.set(GLOBAL_SCOPE, "openai.api_key", "new", by="b")
        assert store.get(GLOBAL_SCOPE, "openai.api_key") == "new"
        assert store.entry(GLOBAL_SCOPE, "openai.api_key").updated_by == "b"
        assert len(store.entries(GLOBAL_SCOPE)) == 1

    def test_scopes_are_isolated(self, store):
        store.set(client_scope("a"), "whatsapp.access_token", TOKEN, by="r")
        assert store.get(client_scope("b"), "whatsapp.access_token") is None
        assert store.get(GLOBAL_SCOPE, "whatsapp.access_token") is None

    def test_entries_are_ordered_by_key(self, store):
        store.set(GLOBAL_SCOPE, "whatsapp.verify_token", "x", by="r")
        store.set(GLOBAL_SCOPE, "openai.api_key", "y", by="r")
        assert [e.key for e in store.entries(GLOBAL_SCOPE)] == [
            "openai.api_key",
            "whatsapp.verify_token",
        ]

    def test_delete(self, store):
        store.set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="r")
        assert store.delete(GLOBAL_SCOPE, "openai.api_key") is True
        assert store.get(GLOBAL_SCOPE, "openai.api_key") is None
        assert store.delete(GLOBAL_SCOPE, "openai.api_key") is False

    def test_unique_refuses_a_value_held_by_another_scope(self, store):
        store.set(client_scope("a"), "whatsapp.phone_number_id", "123456789", by="r", unique=True)
        with pytest.raises(DuplicateSecretError) as error:
            store.set(
                client_scope("b"), "whatsapp.phone_number_id", "123456789", by="r", unique=True
            )
        assert error.value.holder_scope == client_scope("a")
        assert store.get(client_scope("b"), "whatsapp.phone_number_id") is None

    def test_unique_allows_the_same_scope_to_save_its_own_value_again(self, store):
        store.set(client_scope("a"), "whatsapp.phone_number_id", "123456789", by="r", unique=True)
        store.set(client_scope("a"), "whatsapp.phone_number_id", "123456789", by="r", unique=True)

    def test_unique_allows_a_value_freed_by_deletion(self, store):
        store.set(client_scope("a"), "whatsapp.phone_number_id", "123456789", by="r", unique=True)
        store.delete(client_scope("a"), "whatsapp.phone_number_id")
        store.set(client_scope("b"), "whatsapp.phone_number_id", "123456789", by="r", unique=True)

    def test_concurrent_claims_of_one_unique_value_yield_exactly_one_winner(self, store):
        outcomes: list[str] = []

        def claim(client: str) -> None:
            try:
                store.set(
                    client_scope(client),
                    "whatsapp.phone_number_id",
                    "555555555",
                    by="r",
                    unique=True,
                )
                outcomes.append("won")
            except DuplicateSecretError:
                outcomes.append("lost")

        threads = [threading.Thread(target=claim, args=(f"c{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(outcomes) == ["lost"] * 7 + ["won"]


# ---- the encrypted file ------------------------------------------------------------------------


class TestFileStore:
    def test_nothing_on_disk_is_readable(self, tmp_path):
        path = tmp_path / "secrets.json"
        s = FileSecretStore(path, cipher(), now=lambda: NOW)
        s.set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        s.set(client_scope("a"), "whatsapp.access_token", TOKEN, by="root")
        text = path.read_text(encoding="utf-8")
        assert SECRET not in text
        assert TOKEN not in text
        assert not list(tmp_path.glob(".tmp-*"))  # the atomic write leaves nothing behind

    def test_a_new_instance_with_the_same_key_reads_it_back(self, tmp_path):
        path = tmp_path / "secrets.json"
        c = cipher()
        FileSecretStore(path, c).set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        assert FileSecretStore(path, c).get(GLOBAL_SCOPE, "openai.api_key") == SECRET

    def test_verify_with_the_wrong_key_is_an_invalid_master_key_error(self, tmp_path):
        path = tmp_path / "secrets.json"
        FileSecretStore(path, cipher()).set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        with pytest.raises(InvalidMasterKeyError) as error:
            FileSecretStore(path, cipher()).verify()
        assert SECRET not in str(error.value)

    def test_a_record_moved_to_another_client_cannot_be_opened(self, tmp_path):
        path = tmp_path / "secrets.json"
        c = cipher()
        s = FileSecretStore(path, c)
        s.set(client_scope("a"), "whatsapp.access_token", TOKEN, by="root")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["records"][0]["scope"] = client_scope("b")  # an edit made by hand
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(SecretDecryptError):
            FileSecretStore(path, c).get(client_scope("b"), "whatsapp.access_token")

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            "[]",
            '{"format": 99, "records": []}',
            '{"format": 1}',
            '{"format": 1, "records": [{}]}',
        ],
    )
    def test_a_corrupt_file_is_reported_not_guessed_at(self, tmp_path, content):
        path = tmp_path / "secrets.json"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(SecretStoreCorruptError):
            FileSecretStore(path, cipher()).get(GLOBAL_SCOPE, "openai.api_key")

    def test_reads_and_writes_from_different_threads_do_not_collide(self, tmp_path):
        """On Windows a file cannot be replaced while another thread has it open: the runtime
        refresh reads the store while an admin saves, so the two must never overlap."""
        s = FileSecretStore(tmp_path / "secrets.json", cipher(), now=lambda: NOW)
        s.set(GLOBAL_SCOPE, "openai.api_key", "v0", by="r")
        stop, errors = threading.Event(), []

        def reader():
            while not stop.is_set():
                try:
                    assert s.get(GLOBAL_SCOPE, "openai.api_key")
                except Exception as error:  # noqa: BLE001
                    errors.append(error)

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()
        for i in range(60):
            s.set(GLOBAL_SCOPE, "openai.api_key", f"v{i}", by="r")
        stop.set()
        for t in readers:
            t.join(10)
        assert errors == []

    def test_a_missing_file_is_an_empty_store(self, tmp_path):
        assert (
            FileSecretStore(tmp_path / "nope" / "secrets.json", cipher()).entries(GLOBAL_SCOPE)
            == ()
        )


# ---- startup choice of store -------------------------------------------------------------------


class TestBuildSecretStore:
    def test_no_key_and_no_file_means_unavailable_storage(self, tmp_path):
        s = build_secret_store(tmp_path, None)
        assert isinstance(s, UnavailableSecretStore)
        assert s.get(GLOBAL_SCOPE, "openai.api_key") is None
        with pytest.raises(SecretStoreUnavailableError):
            s.set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        with pytest.raises(SecretStoreUnavailableError):
            s.delete(GLOBAL_SCOPE, "openai.api_key")

    def test_a_blank_key_counts_as_no_key(self, tmp_path):
        assert isinstance(build_secret_store(tmp_path, "   "), UnavailableSecretStore)

    def test_a_key_and_no_file_gives_a_working_empty_store(self, tmp_path):
        s = build_secret_store(tmp_path, generate_master_key())
        s.set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        assert s.get(GLOBAL_SCOPE, "openai.api_key") == SECRET

    def test_an_existing_file_without_a_key_refuses_to_start(self, tmp_path):
        key = generate_master_key()
        build_secret_store(tmp_path, key).set(GLOBAL_SCOPE, "openai.api_key", SECRET, by="root")
        with pytest.raises(InvalidMasterKeyError, match="SALES_AGENT_SECRETS_KEY"):
            build_secret_store(tmp_path, None)

    def test_an_existing_file_with_the_wrong_key_refuses_to_start(self, tmp_path):
        build_secret_store(tmp_path, generate_master_key()).set(
            GLOBAL_SCOPE, "openai.api_key", SECRET, by="root"
        )
        with pytest.raises(InvalidMasterKeyError):
            build_secret_store(tmp_path, generate_master_key())

    def test_an_existing_file_with_a_malformed_key_refuses_to_start(self, tmp_path):
        build_secret_store(tmp_path, generate_master_key()).set(
            GLOBAL_SCOPE, "openai.api_key", SECRET, by="root"
        )
        with pytest.raises(InvalidMasterKeyError):
            build_secret_store(tmp_path, "garbage")


# ---- catalog and service -----------------------------------------------------------------------


@pytest.fixture
def service():
    audit = InMemorySecretAudit()
    return CredentialService(InMemorySecretStore(now=lambda: NOW), audit, now=lambda: NOW), audit


class TestCatalog:
    def test_scopes(self):
        assert scope_kind(GLOBAL_SCOPE) is ScopeKind.GLOBAL
        assert scope_kind(client_scope("alpha")) is ScopeKind.CLIENT

    @pytest.mark.parametrize("bad", ["", "client:", "client:Bad Id", "other", "Global"])
    def test_invalid_scopes_are_refused(self, bad):
        with pytest.raises(ValueError):
            scope_kind(bad)

    def test_only_identifiers_are_non_sensitive(self):
        assert {s.key for s in CATALOG if not s.sensitive} == {
            "whatsapp.api_version",
            "whatsapp.phone_number_id",
            "openai.organization",
            "openai.project",
            "openai.base_url",
            "openai.model_sol",
            "openai.model_lune",
            "openai.models",
        }

    def test_phone_number_id_and_hubspot_token_are_unique_per_client(self):
        assert {s.key for s in CATALOG if s.unique} == {
            "whatsapp.phone_number_id",
            "hubspot.access_token",
        }

    def test_no_provider_that_does_not_exist_is_listed(self):
        keys = {s.key for s in CATALOG}
        assert not any(k.startswith(("muse", "meta_ai", "google")) for k in keys)


class TestService:
    def test_describe_lists_every_credential_of_the_scope_kind(self, service):
        svc, _ = service
        assert {s.key for s in svc.describe(GLOBAL_SCOPE)} == {
            "openai.api_key",
            "openai.organization",
            "openai.project",
            "openai.base_url",
            "openai.model_sol",
            "openai.model_lune",
            "openai.models",
            "whatsapp.verify_token",
            "whatsapp.app_secret",
            "whatsapp.api_version",
        }
        assert {s.key for s in svc.describe(client_scope("a"))} == {
            "whatsapp.access_token",
            "whatsapp.phone_number_id",
            "hubspot.access_token",
            "email.password",
            "telegram.bot_token",
            "telegram.webhook_secret",
        }
        assert not any(s.configured for s in svc.describe(GLOBAL_SCOPE))

    def test_saved_sensitive_value_is_never_in_the_status(self, service):
        svc, _ = service
        status = svc.save(GLOBAL_SCOPE, "openai.api_key", SECRET, actor="root")
        assert status.configured and status.value is None and status.updated_by == "root"
        assert SECRET not in repr(svc.describe(GLOBAL_SCOPE))

    def test_non_sensitive_values_are_shown(self, service):
        svc, _ = service
        svc.save(client_scope("a"), "whatsapp.phone_number_id", "123456789", actor="root")
        svc.save(GLOBAL_SCOPE, "whatsapp.api_version", "v21.0", actor="root")
        assert svc.status(client_scope("a"), "whatsapp.phone_number_id").value == "123456789"
        assert svc.status(GLOBAL_SCOPE, "whatsapp.api_version").value == "v21.0"

    def test_surrounding_whitespace_is_stripped(self, service):
        svc, _ = service
        svc.save(GLOBAL_SCOPE, "openai.api_key", f"  {SECRET}\n", actor="root")
        assert svc._store.get(GLOBAL_SCOPE, "openai.api_key") == SECRET

    @pytest.mark.parametrize("bad", ["", "   ", "a\nb", "x" * 5000, "a\x00b"])
    def test_unacceptable_values_are_refused(self, service, bad):
        svc, _ = service
        with pytest.raises(InvalidCredentialError):
            svc.save(GLOBAL_SCOPE, "openai.api_key", bad, actor="root")
        assert not svc.status(GLOBAL_SCOPE, "openai.api_key").configured

    @pytest.mark.parametrize("bad", ["21.0", "v21", "latest", "v21.0\nx"])
    def test_api_version_must_look_like_one(self, service, bad):
        svc, _ = service
        with pytest.raises(InvalidCredentialError):
            svc.save(GLOBAL_SCOPE, "whatsapp.api_version", bad, actor="root")

    @pytest.mark.parametrize("bad", ["abc", "12", "+216 12 345 678", "phone-alpha"])
    def test_phone_number_id_must_be_numeric(self, service, bad):
        svc, _ = service
        with pytest.raises(InvalidCredentialError):
            svc.save(client_scope("a"), "whatsapp.phone_number_id", bad, actor="root")

    def test_refusal_message_never_contains_the_value(self, service):
        svc, _ = service
        with pytest.raises(InvalidCredentialError) as error:
            svc.save(
                client_scope("a"), "whatsapp.phone_number_id", "SECRET-LOOKING-abc", actor="root"
            )
        assert "SECRET-LOOKING-abc" not in str(error.value)

    def test_a_key_of_the_wrong_scope_kind_is_unknown(self, service):
        svc, _ = service
        with pytest.raises(UnknownCredentialError):
            svc.save(GLOBAL_SCOPE, "whatsapp.access_token", TOKEN, actor="root")
        with pytest.raises(UnknownCredentialError):
            svc.save(client_scope("a"), "openai.api_key", SECRET, actor="root")
        with pytest.raises(UnknownCredentialError):
            svc.save(GLOBAL_SCOPE, "muse_spark.api_key", SECRET, actor="root")

    def test_a_phone_number_id_cannot_be_shared_by_two_clients(self, service):
        svc, _ = service
        svc.save(client_scope("naga"), "whatsapp.phone_number_id", "123456789", actor="root")
        with pytest.raises(DuplicateSecretError):
            svc.save(client_scope("other"), "whatsapp.phone_number_id", "123456789", actor="root")

    def test_remove(self, service):
        svc, _ = service
        svc.save(GLOBAL_SCOPE, "openai.api_key", SECRET, actor="root")
        assert not svc.remove(GLOBAL_SCOPE, "openai.api_key", actor="root").configured

    def test_replacing_keeps_one_status(self, service):
        svc, _ = service
        svc.save(GLOBAL_SCOPE, "openai.api_key", "first", actor="a")
        svc.save(GLOBAL_SCOPE, "openai.api_key", "second", actor="b")
        assert svc.status(GLOBAL_SCOPE, "openai.api_key").updated_by == "b"

    def test_unavailable_storage_refuses_and_audits(self):
        audit = InMemorySecretAudit()
        svc = CredentialService(UnavailableSecretStore(), audit, now=lambda: NOW)
        with pytest.raises(SecretStoreUnavailableError):
            svc.save(GLOBAL_SCOPE, "openai.api_key", SECRET, actor="root")
        assert audit.recent()[0].reason == "unavailable"
        assert not svc.status(GLOBAL_SCOPE, "openai.api_key").configured


class TestAudit:
    def test_every_outcome_is_recorded_without_the_value(self, service):
        svc, audit = service
        svc.save(client_scope("a"), "whatsapp.phone_number_id", "123456789", actor="root")
        with pytest.raises(DuplicateSecretError):
            svc.save(client_scope("b"), "whatsapp.phone_number_id", "123456789", actor="root")
        with pytest.raises(InvalidCredentialError):
            svc.save(GLOBAL_SCOPE, "openai.api_key", "   ", actor="root")
        svc.remove(client_scope("a"), "whatsapp.phone_number_id", actor="root")
        svc.remove(client_scope("a"), "whatsapp.phone_number_id", actor="root")
        events = list(reversed(audit.recent()))
        assert [(e.action, e.outcome, e.reason) for e in events] == [
            ("set", "stored", None),
            ("set", "rejected", "duplicate"),
            ("set", "rejected", "invalid_value"),
            ("delete", "removed", None),
            ("delete", "not_found", None),
        ]
        assert "123456789" not in repr(events)
        assert all(e.actor == "root" and e.at == NOW for e in events)

    def test_the_file_audit_is_append_only_json_lines_without_values(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        svc = CredentialService(
            InMemorySecretStore(now=lambda: NOW), FileSecretAudit(path), now=lambda: NOW
        )
        svc.save(GLOBAL_SCOPE, "openai.api_key", SECRET, actor="root")
        svc.remove(GLOBAL_SCOPE, "openai.api_key", actor="root")
        text = path.read_text(encoding="utf-8")
        assert SECRET not in text
        lines = [json.loads(line) for line in text.splitlines()]
        assert [(l["action"], l["outcome"]) for l in lines] == [
            ("set", "stored"),
            ("delete", "removed"),
        ]
        recent = FileSecretAudit(path).recent()
        assert [e.action for e in recent] == ["delete", "set"]  # most recent first
