"""E-mail modes (T1): the historical mailbox stays as it was, and a mailbox Revansa provides resolves.

Covers: the settings of each mode (a document with no `mode` is the historical one, unchanged; a
`platform` mailbox holds nothing technical and refuses anything that is); the identity of an
assigned mailbox (one address, one client, stored in lowercase, refused for a second client even
when attempts are concurrent); what a client asks for in each mode and when it is ready; the one
place that turns a configuration into SMTP/IMAP settings; and the client API, which offers no new
way to reach any of this.

Everything is a double: the credential stores are in memory or a file in a temporary directory, and
the mail servers are fakes. Nothing here connects to anything, so nothing here proves that a real
mailbox (OVH or another) accepts the login or delivers a message.

**What the uniqueness guarantee is, and is not.** The store refuses a second holder of an address
atomically *within one process*: the check and the write happen under one lock. The application
runs as a single process (an instance lock refuses a second one) and nothing else writes the file,
so that is the whole deployment. A second process writing the same file would not be covered; the
tests below prove the first and cannot prove the second.
"""

# The fixtures come from the client channel tests (a world of clients, with the doubles they use):
# a test names them as parameters, which is what the redefinition warning is about.
# ruff: noqa: F811
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest
from app_support import client_yaml, write_client
from test_email_channel_api import CHANNEL, admin, allow_email, alpha, world  # noqa: F401

from sales_app.api.admin_clients import publish_client_settings
from sales_app.channel_status import channel_problem
from sales_app.config import (
    ClientFile,
    extract_document,
    load_client_configs,
    validate_settings_document,
)
from sales_app.config.email_settings import (
    CUSTOM_MODE,
    PLATFORM_MODE,
    EmailChannelSettings,
    EmailSettingsOfAnyMode,
    PlatformEmailSettings,
    canonical_mailbox_address,
    email_mode_of,
    mailbox_key_of,
    parse_email_settings,
    platform_mailbox_key,
)
from sales_app.config.secrets import EMAIL_PLATFORM_PROFILE_KEYS
from sales_app.email_platform import AuthLedger
from sales_app.mailbox_access import (
    MailboxNotReadyError,
    imap_settings,
    resolve_mailbox,
    smtp_settings,
)
from sales_app.mailbox_proof import verify_client_mailbox
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    CredentialService,
    DuplicateSecretError,
    InMemorySecretAudit,
    InMemorySecretStore,
    InvalidCredentialError,
    UnknownCredentialError,
    client_scope,
    generate_master_key,
)
from sales_app.secret_store.catalog import spec_for
from sales_app.secret_store.factory import build_secret_store
from sales_app.wiring.providers import ProviderKind, default_registry
from sales_app.wiring.providers import email as email_provider
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus

MAIL = {
    "from_address": "sales@example.org",
    "username": "sales@example.org",
    "smtp_host": "smtp.example.org",
    "imap_host": "imap.example.org",
}
PLATFORM = {"mode": "platform", "from_name": "Alpha Sales"}
PROFILE = {
    "email.platform_smtp_host": "smtp.platform.example",
    "email.platform_smtp_port": "465",
    "email.platform_smtp_security": "ssl",
    "email.platform_imap_host": "imap.platform.example",
    "email.platform_imap_port": "993",
    "email.platform_imap_security": "ssl",
}
ENV = {"GOOGLE_APPLICATION_CREDENTIALS": "creds.json", "OPENAI_API_KEY": "sk-test"}
PASSWORD = "S3cret-mail-pass!"
ADDRESS = "alpha@mail.example.org"
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
MAILBOX = "email.platform_mailbox"


# ---- helpers ------------------------------------------------------------------------------------


def platform_client(**extra):
    return {"channel": {"provider": "email", "settings": PLATFORM}, **extra}


def custom_client(**extra):
    return {"channel": {"provider": "email", "settings": MAIL}, **extra}


def new_store(*, profile=True, allowed=True):
    store = InMemorySecretStore()
    if allowed:
        store.set(GLOBAL_SCOPE, "channels.allowed", "whatsapp,email", by="root")
    if profile:
        for key, value in PROFILE.items():
            store.set(GLOBAL_SCOPE, key, value, by="root")
    return store


def assign(store, cid, address=ADDRESS, password=PASSWORD):
    if address:
        store.set(client_scope(cid), MAILBOX, address, by="root", unique=True)
    if password:
        store.set(client_scope(cid), "email.password", password, by="root")


def load(tmp_path, store, *clients, ceiling=ApprovalMode.AUTONOMOUS):
    for cid, overrides in clients:
        write_client(tmp_path, cid, **overrides)
    return load_client_configs(tmp_path, env=ENV, mode_ceiling=ceiling, secret_store=store)


def one(tmp_path, store, overrides, **kw):
    (config,) = load(tmp_path, store, ("alpha", overrides), **kw).clients
    return config


# ---- the settings of each mode ------------------------------------------------------------------


class TestTheSettingsOfEachMode:
    def test_a_document_with_no_mode_is_the_historical_mailbox_unchanged(self):
        settings = parse_email_settings(MAIL)
        assert isinstance(settings, EmailChannelSettings) and settings.mode is None
        assert email_mode_of(MAIL) == CUSTOM_MODE
        dumped = settings.model_dump(mode="json", exclude_none=True)
        assert "mode" not in dumped
        assert dumped == EmailChannelSettings.model_validate(MAIL).model_dump(
            mode="json", exclude_none=True
        )
        assert mailbox_key_of(MAIL) == "imap.example.org|sales@example.org"

    def test_custom_may_be_written_out_but_nothing_writes_it_by_itself(self):
        explicit = parse_email_settings(MAIL | {"mode": "custom"})
        assert isinstance(explicit, EmailChannelSettings) and explicit.mode == "custom"
        assert email_mode_of(MAIL | {"mode": "custom"}) == CUSTOM_MODE

    def test_a_platform_mailbox_has_a_default_subject_and_a_clean_name(self):
        settings = parse_email_settings({"mode": "platform", "from_name": "  Alpha Sales "})
        assert isinstance(settings, PlatformEmailSettings)
        assert settings.from_name == "Alpha Sales" and settings.subject
        assert parse_email_settings({"mode": "platform"}).from_name is None
        assert parse_email_settings({"mode": "platform", "from_name": "  "}).from_name is None

    @pytest.mark.parametrize(
        "extra",
        [
            {"smtp_host": "smtp.example.org"},
            {"smtp_port": 587},
            {"smtp_security": "ssl"},
            {"imap_host": "imap.example.org"},
            {"imap_port": 993},
            {"imap_security": "ssl"},
            {"username": "sales@example.org"},
            {"mailbox": "INBOX"},
            {"from_address": "sales@example.org"},
            {"password": "must-never-be-a-setting"},
            {"access_token": "EAAG-token"},
            {"phone_number_id": "123456789"},
            {"api_key": "sk-x"},
            {"email.platform_mailbox": "x@example.org"},
        ],
    )
    def test_a_platform_mailbox_refuses_anything_technical_or_secret(self, extra):
        with pytest.raises(ValueError):
            parse_email_settings(PLATFORM | extra)

    @pytest.mark.parametrize(
        "change",
        [
            {"from_name": 'Evil" <x@y.z>'},
            {"from_name": "x" * 81},
            {"subject": "Line one\nBcc: spy@example.net"},
            {"subject": ""},
            {"subject": "x" * 151},
        ],
    )
    def test_a_platform_mailbox_refuses_what_could_hurt_a_header(self, change):
        with pytest.raises(ValueError):
            parse_email_settings(PLATFORM | change)

    @pytest.mark.parametrize("mode", ["oauth", "google", "Platform", "PLATFORM", "", 1])
    def test_a_mode_that_does_not_exist_is_not_one(self, mode):
        with pytest.raises(ValueError):
            parse_email_settings(MAIL | {"mode": mode})
        assert email_mode_of({"mode": mode}) == CUSTOM_MODE

    def test_a_null_mode_is_no_mode(self):
        assert email_mode_of({"mode": None}) == CUSTOM_MODE
        assert isinstance(parse_email_settings(MAIL | {"mode": None}), EmailChannelSettings)

    def test_a_platform_mailbox_cannot_carry_the_other_modes_settings_by_mistake(self):
        with pytest.raises(ValueError):
            parse_email_settings(MAIL | {"mode": "platform"})

    def test_the_descriptor_validates_the_settings_of_whichever_mode_they_name(self):
        model = default_registry().get("email").settings_models[ProviderKind.CHANNEL]
        assert model is EmailSettingsOfAnyMode
        assert isinstance(model.model_validate(MAIL), EmailChannelSettings)
        assert isinstance(model.model_validate(PLATFORM), PlatformEmailSettings)
        with pytest.raises(ValueError):
            model.model_validate(PLATFORM | {"smtp_host": "smtp.example.org"})
        with pytest.raises(ValueError):
            model.model_validate({"smtp_host": "smtp.example.org"})

    def test_an_address_is_identified_in_lowercase_and_in_a_namespace_of_its_own(self):
        assert canonical_mailbox_address("  Alpha@Mail.Example.ORG ") == ADDRESS
        assert platform_mailbox_key("ALPHA@mail.example.org") == f"platform|{ADDRESS}"
        # Never equal to a historical key, whatever is typed into one.
        assert platform_mailbox_key(ADDRESS) != mailbox_key_of(
            MAIL | {"imap_host": "mail.example.org", "username": "alpha"}
        )


# ---- the settings document ----------------------------------------------------------------------


def spec_with(**overrides):
    return ClientFile.model_validate(client_yaml("alpha", **overrides) | {"inbound": {}})


class TestTheDocument:
    def test_a_platform_channel_is_written_and_round_trips(self):
        spec = spec_with(channel={"provider": "email", "settings": PLATFORM})
        document = extract_document(spec)
        assert document["channel"] == {"provider": "email", "settings": PLATFORM}
        check = validate_settings_document("alpha", client_yaml("alpha"), document)
        assert check.errors == () and check.document["channel"]["settings"]["mode"] == "platform"

    def test_a_historical_document_dumps_exactly_as_before(self):
        spec = spec_with(channel={"provider": "email", "settings": MAIL})
        assert extract_document(spec)["channel"] == {"provider": "email", "settings": MAIL}

    def test_a_server_or_a_password_in_a_platform_document_is_refused_and_not_echoed(self):
        spec = spec_with(channel={"provider": "email", "settings": PLATFORM})
        for key, value in (("smtp_host", "smtp.leak.example"), ("password", "leak-me-1234")):
            document = extract_document(spec)
            document["channel"]["settings"][key] = value
            check = validate_settings_document("alpha", client_yaml("alpha"), document)
            assert check.errors and not any(value in e for e in check.errors)

    def test_a_platform_channel_with_no_settings_yet_is_allowed_like_any_other(self):
        assert spec_with(channel={"provider": "email"}).channel_settings == {}

    def test_a_half_written_historical_mailbox_is_still_refused(self):
        with pytest.raises(ValueError):
            spec_with(channel={"provider": "email", "settings": {"smtp_host": "smtp.example.org"}})


# ---- the identity of an assigned mailbox --------------------------------------------------------


@pytest.fixture
def service():
    return CredentialService(InMemorySecretStore(), InMemorySecretAudit(), now=lambda: NOW)


class TestTheMailboxIdentity:
    def test_it_is_a_client_credential_that_is_not_secret_and_is_unique(self):
        spec = spec_for(client_scope("alpha"), MAILBOX)
        assert (spec.sensitive, spec.unique) == (False, True)
        assert spec.canonical is not None and spec.pattern is not None
        with pytest.raises(UnknownCredentialError):
            spec_for(GLOBAL_SCOPE, MAILBOX)

    def test_it_is_stored_stripped_and_in_lowercase_and_is_shown(self, service):
        status = service.save(
            client_scope("alpha"), MAILBOX, "  Alpha@Mail.Example.ORG ", actor="root"
        )
        assert status.value == ADDRESS and status.configured and not status.sensitive
        assert service.reveal(client_scope("alpha"), MAILBOX) == ADDRESS

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "no-at-sign",
            "a b@example.org",
            "a@b",
            "a@example.org\nBcc: x@y.z",
            "<a@example.org>",
        ],
    )
    def test_what_is_not_an_address_is_refused_and_not_echoed(self, service, value):
        with pytest.raises(InvalidCredentialError) as error:
            service.save(client_scope("alpha"), MAILBOX, value, actor="root")
        assert value.strip() == "" or value not in str(error.value)
        assert service.status(client_scope("alpha"), MAILBOX).configured is False

    def test_a_second_client_cannot_hold_the_same_address_whatever_its_spelling(self, service):
        service.save(client_scope("alpha"), MAILBOX, ADDRESS, actor="root")
        for spelling in (ADDRESS, ADDRESS.upper(), f"  {ADDRESS.title()} "):
            with pytest.raises(DuplicateSecretError) as error:
                service.save(client_scope("beta"), MAILBOX, spelling, actor="root")
            assert error.value.holder_scope == client_scope("alpha")
        assert service.status(client_scope("beta"), MAILBOX).configured is False
        assert [e.outcome for e in service._audit.recent()[:1]] == ["rejected"]

    def test_the_same_client_may_save_its_own_address_again(self, service):
        service.save(client_scope("alpha"), MAILBOX, ADDRESS, actor="root")
        service.save(client_scope("alpha"), MAILBOX, ADDRESS.upper(), actor="root")
        assert service.reveal(client_scope("alpha"), MAILBOX) == ADDRESS

    def test_a_removed_assignment_frees_the_address(self, service):
        service.save(client_scope("alpha"), MAILBOX, ADDRESS, actor="root")
        service.remove(client_scope("alpha"), MAILBOX, actor="root")
        service.save(client_scope("beta"), MAILBOX, ADDRESS, actor="root")
        assert service.reveal(client_scope("beta"), MAILBOX) == ADDRESS

    def test_a_different_address_is_never_a_conflict(self, service):
        service.save(client_scope("alpha"), MAILBOX, ADDRESS, actor="root")
        service.save(client_scope("beta"), MAILBOX, "beta@mail.example.org", actor="root")


def memory_service(_tmp_path):
    return CredentialService(InMemorySecretStore(), InMemorySecretAudit(), now=lambda: NOW)


def file_service(tmp_path):
    key = generate_master_key()
    store = build_secret_store(tmp_path / "secrets", key)
    return CredentialService(store, InMemorySecretAudit(), now=lambda: NOW), key


class TestTheUniquenessIsAtomic:
    """Many clients claim one address at the same instant: exactly one gets it. A thread barrier
    makes the attempts overlap; rounds are repeated so that a race would be seen."""

    CLIENTS = 16
    ROUNDS = 12

    def claim_together(self, service, address_for):
        outcomes: list[object] = []
        gate = threading.Barrier(self.CLIENTS)
        lock = threading.Lock()

        def attempt(index):
            gate.wait()
            try:
                service.save(
                    client_scope(f"c{index}"), MAILBOX, address_for(index), actor=f"c{index}"
                )
                result: object = "won"
            except DuplicateSecretError as error:
                result = error
            except Exception as error:  # noqa: BLE001 - any other failure must show in the test
                result = error
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(self.CLIENTS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert len(outcomes) == self.CLIENTS, "an attempt did not finish"
        return outcomes

    @pytest.mark.parametrize("kind", ["memory", "file"])
    def test_exactly_one_of_many_simultaneous_claims_wins(self, tmp_path, kind):
        if kind == "memory":
            service = memory_service(tmp_path)
        else:
            service, _ = file_service(tmp_path)
        for round_number in range(self.ROUNDS):
            address = f"shared{round_number}@mail.example.org"
            spellings = [address, address.upper(), f" {address.title()} "]
            outcomes = self.claim_together(service, lambda i, s=spellings: s[i % 3])
            won = [o for o in outcomes if o == "won"]
            refused = [o for o in outcomes if isinstance(o, DuplicateSecretError)]
            others = [o for o in outcomes if o != "won" and not isinstance(o, DuplicateSecretError)]
            assert not others, others
            assert len(won) == 1 and len(refused) == self.CLIENTS - 1, round_number
            holders = [
                i
                for i in range(self.CLIENTS)
                if service.reveal(client_scope(f"c{i}"), MAILBOX) == address
            ]
            assert len(holders) == 1
            for i in range(self.CLIENTS):  # release it for the next round
                service.remove(client_scope(f"c{i}"), MAILBOX, actor="root")

    def test_simultaneous_claims_of_different_addresses_all_succeed_and_none_is_lost(
        self, tmp_path
    ):
        service, key = file_service(tmp_path)
        outcomes = self.claim_together(service, lambda i: f"user{i}@mail.example.org")
        assert outcomes == ["won"] * self.CLIENTS
        reopened = build_secret_store(tmp_path / "secrets", key)  # what the file itself holds
        for i in range(self.CLIENTS):
            assert reopened.get(client_scope(f"c{i}"), MAILBOX) == f"user{i}@mail.example.org"


# ---- what a client asks for, and when it is ready -----------------------------------------------


class TestWhenAPlatformClientIsReady:
    def test_a_platform_client_needs_a_mailbox_a_password_and_the_servers(self, tmp_path):
        config = one(tmp_path, new_store(profile=False), platform_client())
        assert config.missing_credentials == (
            "Email password is not configured",
            "Email mailbox has not been assigned to this account yet",
            "The platform mail servers are not configured",
        )
        assert not config.ready

    @pytest.mark.parametrize(
        ("assigned", "profile", "expected"),
        [
            (True, True, ("Email password is not configured",)),
            (
                True,
                False,
                (
                    "Email password is not configured",
                    "The platform mail servers are not configured",
                ),
            ),
        ],
    )
    def test_each_missing_piece_is_named_once_and_no_value_is_in_it(
        self, tmp_path, assigned, profile, expected
    ):
        store = new_store(profile=profile)
        if assigned:
            assign(store, "alpha", password=None)
        config = one(tmp_path, store, platform_client())
        assert config.missing_credentials == expected
        assert ADDRESS not in " ".join(config.missing_credentials)

    def test_one_missing_server_setting_is_enough_to_say_so(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        store.delete(GLOBAL_SCOPE, "email.platform_imap_host")
        config = one(tmp_path, store, platform_client())
        assert config.missing_credentials == ("The platform mail servers are not configured",)

    def test_with_everything_in_place_it_is_ready_and_holds_what_it_resolved(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        config = one(tmp_path, store, platform_client())
        assert config.ready
        assert config.secrets.get("email.password") == PASSWORD
        assert config.secrets.get(MAILBOX) == ADDRESS
        for key in EMAIL_PLATFORM_PROFILE_KEYS:
            assert config.secrets.get(key) == PROFILE[key]
        assert PASSWORD not in repr(config.secrets)

    def test_the_channel_still_has_to_be_authorized(self, tmp_path):
        store = new_store(allowed=False)
        assign(store, "alpha")
        config = one(tmp_path, store, platform_client())
        assert config.missing_credentials == (
            "The email channel is not authorized for this account",
        )

    def test_a_dry_run_needs_none_of_it_but_the_authorization(self, tmp_path):
        config = one(
            tmp_path, new_store(profile=False), platform_client(), ceiling=ApprovalMode.DRY_RUN
        )
        assert config.ready

    def test_a_platform_client_has_no_historical_settings_to_complete(self, tmp_path):
        store = new_store()
        assign(store, "alpha")
        assert (
            "Email settings are incomplete"
            not in one(tmp_path, store, platform_client()).missing_credentials
        )

    def test_a_historical_client_asks_for_exactly_what_it_always_did(self, tmp_path):
        store = new_store()
        config = one(tmp_path, store, custom_client())
        assert config.missing_credentials == ("Email password is not configured",)
        store.set(client_scope("alpha"), "email.password", PASSWORD, by="alpha")
        config = one(tmp_path, store, custom_client())
        assert config.ready
        assert MAILBOX not in config.secrets
        assert not any(key in config.secrets for key in EMAIL_PLATFORM_PROFILE_KEYS)

    def test_the_historical_mailbox_of_one_client_is_not_the_platform_one_of_another(
        self, tmp_path
    ):
        store = new_store()
        assign(store, "alpha")
        store.set(client_scope("beta"), "email.password", PASSWORD, by="beta")
        loaded = load(tmp_path, store, ("alpha", platform_client()), ("beta", custom_client()))
        assert loaded.errors == ()
        keys = {c.client_id: c.mailbox_key for c in loaded.clients}
        assert keys == {
            "alpha": f"platform|{ADDRESS}",
            "beta": "imap.example.org|sales@example.org",
        }


class TestTheMailboxKey:
    def test_it_is_the_assigned_address_not_anything_the_settings_say(self, tmp_path):
        store = new_store()
        assert one(tmp_path, store, platform_client()).mailbox_key is None  # nothing assigned yet
        assign(store, "alpha", address="Alpha@Mail.example.org")
        assert (
            one(tmp_path, store, platform_client()).mailbox_key == "platform|alpha@mail.example.org"
        )

    def test_two_clients_on_one_address_are_an_error_at_startup(self, tmp_path):
        # The store refuses this write; here it is bypassed on purpose, to show that loading would
        # still catch a store that held it (the second line of defence).
        store = new_store()
        for cid in ("alpha", "beta"):
            store.set(client_scope(cid), MAILBOX, ADDRESS, by="root")  # no `unique`
            store.set(client_scope(cid), "email.password", PASSWORD, by="root")
        loaded = load(tmp_path, store, ("alpha", platform_client()), ("beta", platform_client()))
        assert any("email mailbox" in e and "alpha" in e and "beta" in e for e in loaded.errors)
        assert not any(PASSWORD in e for e in loaded.errors)


# ---- the one place that resolves a mailbox ------------------------------------------------------


class FakeSmtp:
    def __init__(self):
        self.logins: list[tuple[str, str]] = []
        self.sent = []

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        pass


class FakeImap:
    def __init__(self):
        self.logins: list[tuple[str, str]] = []
        self.selected = None

    def login(self, user, password):
        self.logins.append((user, password))

    def select(self, mailbox="INBOX", readonly=False):
        self.selected = mailbox
        return "OK", [b"0"]

    def uid(self, command, *args):
        assert command == "SEARCH"
        return "OK", [b""]

    def logout(self):
        pass


def outbound(recipient="alex@example.com"):
    return OutboundMessage(
        client_id="alpha",
        prospect_id="p1",
        channel=Channel.EMAIL,
        recipient=recipient,
        draft=MessageDraft(text="Hello Alex"),
        idempotency_key="key-1",
    )


class TestTheResolver:
    def ready_config(self, tmp_path, overrides=None, store=None):
        store = store or new_store()
        assign(store, "alpha")
        return one(tmp_path, store, overrides or platform_client()), store

    def test_a_platform_mailbox_logs_in_and_sends_as_the_assigned_address(self, tmp_path):
        config, _ = self.ready_config(tmp_path)
        access = resolve_mailbox(config)
        assert access.mode == PLATFORM_MODE
        smtp, imap = access.smtp, access.imap
        assert (smtp.host, smtp.port, smtp.security) == ("smtp.platform.example", 465, "ssl")
        assert (smtp.username, smtp.password, smtp.from_address) == (ADDRESS, PASSWORD, ADDRESS)
        assert smtp.from_name == "Alpha Sales" and smtp.subject  # the confirmed Sender Name
        assert (imap.host, imap.port, imap.security) == ("imap.platform.example", 993, "ssl")
        assert (imap.username, imap.password, imap.mailbox) == (ADDRESS, PASSWORD, "INBOX")
        assert imap.own_addresses == (ADDRESS,)

    def test_no_credential_shows_in_a_repr(self, tmp_path):
        config, _ = self.ready_config(tmp_path)
        assert PASSWORD not in repr(resolve_mailbox(config))

    def test_a_historical_mailbox_resolves_exactly_as_before(self, tmp_path):
        store = new_store()
        store.set(client_scope("alpha"), "email.password", PASSWORD, by="alpha")
        config = one(tmp_path, store, custom_client())
        access = resolve_mailbox(config)
        settings = EmailChannelSettings.model_validate(MAIL)
        assert access.mode == CUSTOM_MODE
        assert access.smtp == smtp_settings(settings, PASSWORD)
        assert access.imap == imap_settings(settings, PASSWORD)
        assert (access.smtp.host, access.smtp.port, access.smtp.username) == (
            "smtp.example.org",
            587,
            "sales@example.org",
        )
        assert access.imap.own_addresses == ("sales@example.org", "sales@example.org")

    def test_a_change_of_the_platform_servers_reaches_the_client_without_touching_its_document(
        self, tmp_path
    ):
        config, store = self.ready_config(tmp_path)
        assert resolve_mailbox(config).smtp.host == "smtp.platform.example"
        store.set(GLOBAL_SCOPE, "email.platform_smtp_host", "smtp.other.example", by="root")
        store.set(GLOBAL_SCOPE, "email.platform_smtp_port", "587", by="root")
        store.set(GLOBAL_SCOPE, "email.platform_smtp_security", "starttls", by="root")
        again = one(tmp_path, store, platform_client())
        smtp = resolve_mailbox(again).smtp
        assert (smtp.host, smtp.port, smtp.security) == ("smtp.other.example", 587, "starttls")
        assert again.spec.channel_settings_for("email") == config.spec.channel_settings_for("email")

    def test_a_platform_mailbox_that_lacks_something_says_so_in_a_fixed_sentence(self, tmp_path):
        no_servers = one(tmp_path, new_store(profile=False), platform_client())
        with pytest.raises(MailboxNotReadyError, match="mail servers are not configured") as error:
            resolve_mailbox(no_servers)
        store = new_store()
        store.set(client_scope("alpha"), "email.password", PASSWORD, by="alpha")
        no_mailbox = one(tmp_path, store, platform_client())
        with pytest.raises(MailboxNotReadyError, match="no mailbox has been assigned"):
            resolve_mailbox(no_mailbox)
        assert PASSWORD not in str(error.value)

    def test_the_channel_built_for_a_platform_client_sends_through_the_platform(self, tmp_path):
        config, _ = self.ready_config(tmp_path)
        server = FakeSmtp()
        channel = email_provider.build_channel(config, smtp_factory=lambda settings: server)
        result = channel.send(outbound())
        assert result.status is SendStatus.ACCEPTED
        assert server.logins == [(ADDRESS, PASSWORD)]
        (sent,) = server.sent
        assert sent["From"] == f"Alpha Sales <{ADDRESS}>" and sent["To"] == "alex@example.com"

    def test_the_inbox_built_for_a_platform_client_reads_the_assigned_mailbox(self, tmp_path):
        config, _ = self.ready_config(tmp_path)
        server = FakeImap()
        inbox = email_provider.build_inbox(config, imap_factory=lambda settings: server)
        assert inbox.fetch() == []
        inbox.close()
        assert server.logins == [(ADDRESS, PASSWORD)] and server.selected == "INBOX"

    def test_the_channel_built_for_a_historical_client_is_unchanged(self, tmp_path):
        store = new_store()
        store.set(client_scope("alpha"), "email.password", PASSWORD, by="alpha")
        config = one(tmp_path, store, custom_client())
        server = FakeSmtp()
        channel = email_provider.build_channel(config, smtp_factory=lambda settings: server)
        assert channel.send(outbound()).status is SendStatus.ACCEPTED
        assert server.logins == [("sales@example.org", PASSWORD)]


# ---- what the client is told --------------------------------------------------------------------


def fake_application(store):
    return SimpleNamespace(
        channel_policy=SimpleNamespace(
            effective=lambda client_id: frozenset({"whatsapp", "email"})
        ),
        credentials=CredentialService(store, InMemorySecretAudit(), now=lambda: NOW),
        mail_auth=AuthLedger(),
    )


class AcceptingProbe:
    """A mailbox whose two logins are accepted."""

    def __init__(self, smtp, imap):
        pass

    def check_smtp(self):
        pass

    def check_imap(self):
        pass


SETTING_UP = "Revansa is setting up this channel for you"


class TestWhatTheClientIsTold:
    DOCUMENT: ClassVar[dict] = {"channel": {"provider": "email", "settings": PLATFORM}}

    def problem(self, store, application=None):
        return channel_problem(
            application or fake_application(store), "alpha", "email", self.DOCUMENT
        )

    def test_each_missing_piece_is_the_administrators_to_fix(self):
        store = new_store(profile=False)
        assert SETTING_UP in self.problem(store)  # no mailbox assigned
        assign(store, "alpha", password=None)
        assert SETTING_UP in self.problem(store)  # its password is not stored
        assign(store, "alpha")
        assert SETTING_UP in self.problem(store)  # the platform's servers are not set up
        for key, value in PROFILE.items():
            store.set(GLOBAL_SCOPE, key, value, by="root")
        app = fake_application(store)
        # Everything is stored, but nothing has been logged in to: not ready.
        assert SETTING_UP in self.problem(store, app)
        verify_client_mailbox(app, "alpha", AcceptingProbe)
        assert self.problem(store, app) is None

    def test_a_client_is_never_asked_for_anything_technical(self):
        store = new_store(profile=False)
        words = " ".join(filter(None, [self.problem(store)])).lower()
        for technical in ("smtp", "imap", "port", "password", "host"):
            assert technical not in words
        assign(store, "alpha", password=None)
        assert "password" not in self.problem(store).lower()

    def test_a_historical_client_is_told_what_it_always_was(self):
        document = {"channel": {"provider": "email", "settings": MAIL}}
        store = new_store()
        message = channel_problem(fake_application(store), "alpha", "email", document)
        assert SETTING_UP in message  # its password is the platform's to store, not the client's
        assert "password" not in message.lower()
        incomplete = {"channel": {"provider": "email", "settings": {"smtp_host": "x.example.org"}}}
        assert SETTING_UP in channel_problem(fake_application(store), "alpha", "email", incomplete)


# ---- the client API offers no new way in --------------------------------------------------------


def publish_platform(world, version):
    publish_client_settings(
        world.application,
        "alpha",
        {"channel": {"provider": "email", "settings": PLATFORM}},
        expected_version=version,
        author="root",
        top_keys=(),
        sections={"channel": ("provider", "settings")},
    )


class TestTheClientApiOffersNoNewWayIn:
    def test_a_client_on_a_platform_mailbox_sees_no_technical_setting(self, world, alpha, admin):
        allow_email(admin)
        publish_platform(world, alpha.get(CHANNEL).json()["version"])
        body = alpha.get(CHANNEL).json()
        assert body["channel"] == "email" and body["authorized"] is True
        assert body["email"] is not None
        shown = {k: v for k, v in body["email"].items() if v is not None}
        # The daily sending limit is the one other thing said: a state and a sentence, no setting.
        sending = shown.pop("sending")
        assert sending["state"] in {"ok", "not_set", "reached", "unavailable"}
        assert set(sending) == {"state", "message", "daily_limit", "sent_today"}
        # The wording and whose mailbox it is: nothing technical is stored or shown.
        assert shown == {
            "mode": "platform",
            "from_name": "Alpha Sales",
            "sender_name_confirmed": True,
        }
        assert body["status"] == "waiting_platform" and "setting up" in body["status_message"]

    def test_a_client_cannot_turn_its_platform_mailbox_into_a_historical_one(
        self, world, alpha, admin
    ):
        allow_email(admin)
        publish_platform(world, alpha.get(CHANNEL).json()["version"])
        version = alpha.get(CHANNEL).json()["version"]
        mail = {
            "from_address": "sales@example.org",
            "username": "sales@example.org",
            "smtp_host": "smtp.example.org",
            "imap_host": "imap.example.org",
        }
        response = alpha.put(
            CHANNEL, json={"expected_version": version, "channel": "email", "email": mail}
        )
        assert response.status_code == 422
        assert "smtp.example.org" not in response.text
        after = alpha.get(CHANNEL).json()
        assert after["version"] == version

    def test_a_client_cannot_name_a_mode_or_a_mailbox(self, world, alpha, admin):
        allow_email(admin)
        version = alpha.get(CHANNEL).json()["version"]
        for field in ("mode", "platform_mailbox", "email.platform_mailbox"):
            response = alpha.put(
                CHANNEL,
                json={
                    "expected_version": version,
                    "channel": "email",
                    "email": {field: "platform"},
                },
            )
            assert response.status_code == 422, field
        assert alpha.get(CHANNEL).json()["version"] == version


# ---- the Admin's credential routes --------------------------------------------------------------


class TestTheAdminsRoutesHoldTheSameGuarantee:
    URL = "/admin/v1/clients/{}/integrations/email.platform_mailbox"

    @pytest.fixture
    def fx(self, tmp_path):
        from test_admin_integrations_api import Fixture, user

        from sales_app.admin_auth import AdminRole

        fixture = Fixture(tmp_path)
        fixture.users.create(user("root", AdminRole.OWNER))
        return fixture

    def test_the_same_address_for_a_second_client_is_a_409_whatever_its_case(self, fx):
        client = fx.login("root")
        first = client.put(self.URL.format("alpha"), json={"value": "Alpha@Mail.Example.org"})
        assert first.status_code == 200 and first.json()["value"] == ADDRESS
        second = client.put(self.URL.format("beta"), json={"value": ADDRESS.upper()})
        assert second.status_code == 409
        assert "alpha" in second.json()["detail"] and ADDRESS not in second.text
        listed = client.get("/admin/v1/clients/beta/integrations").json()
        assert next(c for c in listed if c["key"] == MAILBOX)["configured"] is False

    def test_a_value_that_is_not_an_address_is_a_422_that_does_not_echo_it(self, fx):
        client = fx.login("root")
        response = client.put(self.URL.format("alpha"), json={"value": "not an address"})
        assert response.status_code == 422 and "not an address" not in response.text

    def test_it_is_the_owners_to_write(self, fx):
        from test_admin_integrations_api import user

        from sales_app.admin_auth import AdminRole

        fx.users.create(user("eve", AdminRole.EDITOR, frozenset({"alpha"})))
        client = fx.login("eve")
        assert client.put(self.URL.format("alpha"), json={"value": ADDRESS}).status_code == 403
