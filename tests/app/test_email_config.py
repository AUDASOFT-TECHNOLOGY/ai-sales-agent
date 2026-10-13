"""The e-mail channel in configuration: the settings document, the credentials a client needs, which
channels it may use, and the policy service.

Covers: the channel section of a client's settings (written only when chosen, no secret allowed in
it); which credentials a client asks for according to its channel; the Admin's authorization judged
every time a configuration is resolved; the text format of the two stored policy values; the
service that writes them (audited, handed to the runtime, no value in any event); a mailbox that
belongs to one client; and the health of a mailbox followed apart from the sending.
"""

from datetime import UTC, datetime

import pytest
from app_support import client_yaml, write_client
from consent_support import Clock

from sales_app.channel_policy import ChannelPolicyService, InvalidChannelPolicyError
from sales_app.config import (
    ClientFile,
    extract_document,
    import_document,
    load_client_configs,
    validate_settings_document,
)
from sales_app.config.channel_access import (
    DEFAULT_ALLOWED,
    effective_channels,
    format_access,
    format_allowed,
    parse_access,
    parse_allowed,
)
from sales_app.config.email_settings import EmailChannelSettings, mailbox_key_of
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    InMemorySecretAudit,
    InMemorySecretStore,
    client_scope,
)
from sales_app.wiring.runtime import ClientHealth
from sales_core.approval import ApprovalMode

MAIL = {
    "from_address": "sales@example.org",
    "username": "sales@example.org",
    "smtp_host": "smtp.example.org",
    "imap_host": "imap.example.org",
}
ENV = {"GOOGLE_APPLICATION_CREDENTIALS": "creds.json", "OPENAI_API_KEY": "sk-test"}
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


# ---- the mailbox's own settings -----------------------------------------------------------------


class TestEmailSettings:
    def test_a_mailbox_has_sensible_defaults(self):
        settings = EmailChannelSettings.model_validate(MAIL)
        assert (settings.smtp_port, settings.smtp_security) == (587, "starttls")
        assert (settings.imap_port, settings.imap_security, settings.mailbox) == (
            993,
            "ssl",
            "INBOX",
        )

    def test_addresses_and_hosts_are_lowercased_and_stripped(self):
        settings = EmailChannelSettings.model_validate(
            MAIL | {"from_address": " Sales@Example.ORG ", "smtp_host": "SMTP.Example.org "}
        )
        assert (
            settings.from_address == "sales@example.org"
            and settings.smtp_host == "smtp.example.org"
        )

    @pytest.mark.parametrize(
        "change",
        [
            {"smtp_port": 22},
            {"smtp_port": 3306},
            {"imap_port": 587},
            {"imap_port": 80},
            {"from_address": "no-at-sign"},
            {"from_address": "a b@example.org"},
            {"from_address": "a@example.org\r\nBcc: x@y.z"},
            {"smtp_host": "smtp.example.org/../x"},
            {"smtp_host": "user@smtp.example.org"},
            {"imap_host": "-bad.example.org"},
            {"smtp_security": "none"},
            {"imap_security": "plain"},
            {"subject": "Line one\nBcc: spy@example.net"},
            {"from_name": 'Evil" <x@y.z>'},
            {"username": "a\nb"},
            {"password": "must-never-be-a-setting"},
            {"phone_number_id": "123456789"},
            {"access_token": "EAAG-token"},
        ],
    )
    def test_what_could_hurt_or_does_not_belong_is_refused(self, change):
        with pytest.raises(ValueError):
            EmailChannelSettings.model_validate(MAIL | change)

    def test_a_mailbox_is_identified_by_its_server_and_login_in_lowercase(self):
        assert mailbox_key_of(MAIL) == "imap.example.org|sales@example.org"
        assert mailbox_key_of(MAIL | {"username": "SALES@example.org"}) == mailbox_key_of(MAIL)
        assert mailbox_key_of(MAIL | {"username": "other@example.org"}) != mailbox_key_of(MAIL)
        assert mailbox_key_of({"from_address": "x"}) is None


# ---- the settings document ----------------------------------------------------------------------


def spec_with(**overrides):
    return ClientFile.model_validate(client_yaml("alpha", **overrides) | {"inbound": {}})


class TestTheChannelInTheSettingsDocument:
    def test_a_client_that_never_chose_a_channel_dumps_exactly_as_before(self):
        spec = spec_with()
        assert "channel" not in spec.model_dump() and "channel" not in extract_document(spec)
        assert spec.channel_provider == "whatsapp" and spec.channel_settings == {}

    def test_a_chosen_channel_is_written_with_its_settings(self):
        spec = spec_with(channel={"provider": "email", "settings": MAIL})
        assert spec.channel_provider == "email"
        document = extract_document(spec)
        assert document["channel"] == {"provider": "email", "settings": MAIL}

    def test_the_document_round_trips(self):
        spec = spec_with(channel={"provider": "email", "settings": MAIL})
        document = extract_document(spec)
        check = validate_settings_document("alpha", client_yaml("alpha"), document)
        assert check.errors == () and check.document["channel"]["provider"] == "email"

    def test_a_password_in_the_settings_is_refused(self):
        spec = spec_with(channel={"provider": "email", "settings": MAIL})
        document = extract_document(spec)
        document["channel"]["settings"]["password"] = "leak-me-1234"
        check = validate_settings_document("alpha", client_yaml("alpha"), document)
        assert check.errors and not any("leak-me-1234" in e for e in check.errors)

    def test_a_half_written_mailbox_is_refused(self):
        with pytest.raises(ValueError):
            spec_with(channel={"provider": "email", "settings": {"smtp_host": "smtp.example.org"}})

    def test_a_channel_with_no_settings_yet_is_allowed(self):
        assert spec_with(channel={"provider": "email"}).channel_settings == {}

    def test_an_unknown_key_in_the_section_is_refused(self):
        with pytest.raises(ValueError):
            spec_with(channel={"provider": "email", "whatsapp": {"phone_number_id": "1"}})

    def test_an_operator_wiring_file_may_not_set_the_channel_for_a_document(self):
        # `channel` is a client setting: a stored document wins over whatever client.yaml says.
        spec = import_document("alpha", client_yaml("alpha", channel={"provider": "email"}))
        assert spec.errors == () and spec.document["channel"] == {"provider": "email"}


# ---- the credentials a client asks for ----------------------------------------------------------


def load(tmp_path, store, *clients, ceiling=ApprovalMode.AUTONOMOUS):
    for cid, overrides in clients:
        write_client(tmp_path, cid, **overrides)
    return load_client_configs(tmp_path, env=ENV, mode_ceiling=ceiling, secret_store=store)


def whatsapp_env(cid):
    upper = cid.upper()
    return {
        f"{upper}_TOKEN": f"token-{cid}",
        f"{upper}_PHONE_ID": f"1000000{len(cid)}",
        f"{upper}_APP_SECRET": f"secret-{cid}-abcdef",
    }


def email_client(**extra):
    return {"channel": {"provider": "email", "settings": MAIL}, **extra}


def allow(store, client="alpha", global_value="whatsapp,email"):
    store.set(GLOBAL_SCOPE, "channels.allowed", global_value, by="root")


class TestWhichCredentialsAClientAsksFor:
    def test_a_client_on_email_needs_its_password_and_none_of_whatsapps(self, tmp_path):
        store = InMemorySecretStore()
        allow(store)
        (config,) = load(tmp_path, store, ("alpha", email_client())).clients
        assert config.missing_credentials == ("Email password is not configured",)
        store.set(client_scope("alpha"), "email.password", "S3cret-mail-pass!", by="alpha")
        (config,) = load(tmp_path, store, ("alpha", email_client())).clients
        assert config.ready and config.secrets.get("email.password") == "S3cret-mail-pass!"
        assert "whatsapp.access_token" not in config.secrets

    def test_a_client_on_whatsapp_still_needs_its_own_and_not_a_mailbox(self, tmp_path):
        store = InMemorySecretStore()
        (config,) = load(tmp_path, store, ("alpha", {})).clients
        wanted = " ".join(config.missing_credentials)
        assert "WhatsApp access token" in wanted and "Email" not in wanted

    def test_incomplete_settings_are_a_reason_not_a_crash(self, tmp_path):
        store = InMemorySecretStore()
        allow(store)
        store.set(client_scope("alpha"), "email.password", "S3cret-mail-pass!", by="alpha")
        # The channel chosen in the wiring file without its mailbox: the client is not ready.
        (config,) = load(tmp_path, store, ("alpha", {"channel": {"provider": "email"}})).clients
        assert "Email settings are incomplete" in config.missing_credentials

    def test_a_dry_run_needs_no_password_but_still_needs_the_authorization(self, tmp_path):
        store = InMemorySecretStore()
        (config,) = load(
            tmp_path, store, ("alpha", email_client()), ceiling=ApprovalMode.DRY_RUN
        ).clients
        assert config.missing_credentials == (
            "The email channel is not authorized for this account",
        )
        allow(store)
        (config,) = load(
            tmp_path, store, ("alpha", email_client()), ceiling=ApprovalMode.DRY_RUN
        ).clients
        assert config.ready

    def test_the_password_of_one_client_is_never_the_others(self, tmp_path):
        store = InMemorySecretStore()
        allow(store)
        store.set(client_scope("alpha"), "email.password", "alpha-pass-12345", by="a")
        other = {"channel": {"provider": "email", "settings": MAIL | {"username": "b@example.org"}}}
        configs = load(tmp_path, store, ("alpha", email_client()), ("beta", other)).clients
        by_id = {c.client_id: c for c in configs}
        assert by_id["alpha"].ready and not by_id["beta"].ready


class TestTheAuthorizationIsJudgedOnEveryResolution:
    def test_without_any_decision_only_whatsapp_is_allowed(self, tmp_path):
        (config,) = load(tmp_path, InMemorySecretStore(), ("alpha", email_client())).clients
        assert any("not authorized" in m for m in config.missing_credentials)

    def test_without_a_secret_store_the_default_still_applies(self, tmp_path):
        (config,) = load(tmp_path, None, ("alpha", email_client())).clients
        assert any("not authorized" in m for m in config.missing_credentials)

    def test_a_channel_forbidden_after_it_was_set_up_makes_the_client_not_ready(self, tmp_path):
        store = InMemorySecretStore()
        allow(store)
        store.set(client_scope("alpha"), "email.password", "S3cret-mail-pass!", by="alpha")
        (ready,) = load(tmp_path, store, ("alpha", email_client())).clients
        assert ready.ready
        store.set(client_scope("alpha"), "channels.access", "-email", by="root")
        (stopped,) = load(tmp_path, store, ("alpha", email_client())).clients
        assert not stopped.ready
        assert any("not authorized" in m for m in stopped.missing_credentials)
        assert "S3cret-mail-pass!" not in " ".join(stopped.missing_credentials)

    def test_a_global_ban_reaches_every_client_and_an_exception_saves_one(self, tmp_path):
        store = InMemorySecretStore()
        store.set(GLOBAL_SCOPE, "channels.allowed", "whatsapp", by="root")
        store.set(client_scope("alpha"), "channels.access", "+email", by="root")
        store.set(client_scope("alpha"), "email.password", "alpha-pass-12345", by="a")
        store.set(client_scope("beta"), "email.password", "beta-pass-12345", by="b")
        other = {"channel": {"provider": "email", "settings": MAIL | {"username": "b@example.org"}}}
        configs = load(tmp_path, store, ("alpha", email_client()), ("beta", other)).clients
        by_id = {c.client_id: c for c in configs}
        assert by_id["alpha"].ready and not by_id["beta"].ready

    def test_whatsapp_forbidden_stops_a_client_that_is_still_on_it(self, tmp_path):
        store = InMemorySecretStore()
        store.set(GLOBAL_SCOPE, "channels.allowed", "email", by="root")
        (config,) = load(tmp_path, store, ("alpha", {})).clients
        assert any("whatsapp channel is not authorized" in m for m in config.missing_credentials)

    def test_a_mailbox_two_clients_share_is_an_error_at_startup(self, tmp_path):
        store = InMemorySecretStore()
        allow(store)
        loaded = load(tmp_path, store, ("alpha", email_client()), ("beta", email_client()))
        assert any("email mailbox" in e and "alpha" in e and "beta" in e for e in loaded.errors)


# ---- the format of the two stored values --------------------------------------------------------


class TestThePolicyValues:
    def test_the_default_is_whatsapp_only(self):
        assert parse_allowed(None) == DEFAULT_ALLOWED == frozenset({"whatsapp"})

    @pytest.mark.parametrize("junk", ["", "Email", "whatsapp;email", "a", "email,", ",email", "*"])
    def test_something_unreadable_falls_back_to_the_safe_default(self, junk):
        assert parse_allowed(junk) == DEFAULT_ALLOWED
        assert parse_access(junk) == (frozenset(), frozenset())

    def test_a_list_round_trips_and_none_means_nobody(self):
        assert parse_allowed(format_allowed({"email", "whatsapp"})) == {"email", "whatsapp"}
        assert format_allowed({"email", "whatsapp"}) == "email,whatsapp"
        assert format_allowed(set()) == "none" and parse_allowed("none") == frozenset()

    def test_exceptions_round_trip_and_nothing_is_none(self):
        assert format_access({"email"}, {"whatsapp"}) == "-whatsapp,+email".replace(
            "-whatsapp,+email", "+email,-whatsapp"
        )
        assert parse_access("+email,-whatsapp") == ({"email"}, {"whatsapp"})
        assert format_access(set(), set()) is None

    def test_a_refusal_wins_over_a_permission(self):
        assert format_access({"email"}, {"email"}) == "-email"
        assert effective_channels("whatsapp,email", "-email") == {"whatsapp"}
        assert effective_channels("whatsapp", "+email,-email") == {"whatsapp"}
        assert effective_channels("whatsapp", "+email") == {"whatsapp", "email"}
        assert effective_channels("none", None) == frozenset()


class TestThePolicyService:
    @pytest.fixture
    def service(self):
        store, audit = InMemorySecretStore(), InMemorySecretAudit()
        service = ChannelPolicyService(store, audit, now=Clock(NOW))
        service.changes = []
        service.set_on_change(lambda scope, key: service.changes.append((scope, key)))
        service.store, service.audit = store, audit
        return service

    def test_it_starts_on_the_default(self, service):
        assert service.global_allowed() == {"whatsapp"}
        assert service.effective("alpha") == {"whatsapp"}

    def test_a_global_change_is_stored_audited_and_handed_to_the_runtime(self, service):
        service.set_global({"whatsapp", "email"}, actor="root", known={"whatsapp", "email"})
        assert service.global_allowed() == {"whatsapp", "email"}
        assert service.changes == [(GLOBAL_SCOPE, "channels.allowed")]
        (event,) = service.audit.recent()
        assert (event.actor, event.scope, event.key, event.outcome) == (
            "root",
            GLOBAL_SCOPE,
            "channels.allowed",
            "stored",
        )

    def test_a_client_exception_touches_that_client_only(self, service):
        service.set_client_access(
            "alpha", {"email"}, set(), actor="root", known={"whatsapp", "email"}
        )
        assert service.effective("alpha") == {"whatsapp", "email"}
        assert service.effective("beta") == {"whatsapp"}
        assert service.changes == [(client_scope("alpha"), "channels.access")]

    def test_clearing_an_exception_removes_it(self, service):
        known = {"whatsapp", "email"}
        service.set_client_access("alpha", {"email"}, set(), actor="root", known=known)
        service.set_client_access("alpha", set(), set(), actor="root", known=known)
        assert service.store.get(client_scope("alpha"), "channels.access") is None
        assert [e.outcome for e in service.audit.recent()][:1] == ["removed"]

    def test_an_unknown_channel_changes_nothing(self, service):
        with pytest.raises(InvalidChannelPolicyError):
            service.set_global({"carrier_pigeon"}, actor="root", known={"whatsapp", "email"})
        with pytest.raises(InvalidChannelPolicyError):
            service.set_client_access("alpha", set(), {"x"}, actor="root", known={"whatsapp"})
        assert service.changes == [] and service.audit.recent() == []

    def test_a_failing_runtime_hook_never_undoes_the_change(self, service):
        service.set_on_change(lambda scope, key: 1 / 0)
        service.set_global({"email"}, actor="root", known={"email"})
        assert service.global_allowed() == {"email"}


# ---- the health of a mailbox, apart from the sending -------------------------------------------


class TestMailboxHealth:
    def test_the_daily_cycle_succeeding_does_not_hide_a_mailbox_that_fails(self):
        health = ClientHealth()
        health.record_failure("inbox", NOW, "MailUnavailableError: down")
        health.record_success("daily", NOW)
        snapshot = health.snapshot()
        assert snapshot["status"] == "degraded" and snapshot["inbox_failures"] == 1
        assert snapshot["consecutive_failures"] == 0  # the sending's backoff is not inflated

    def test_the_mailbox_recovering_clears_only_the_mailbox(self):
        health = ClientHealth()
        health.record_failure("inbox", NOW, "down")
        health.record_failure("daily", NOW, "boom")
        health.record_success("inbox", NOW)
        snapshot = health.snapshot()
        assert snapshot["status"] == "degraded" and "inbox_failures" not in snapshot
        assert snapshot["consecutive_failures"] == 1
        health.record_success("daily", NOW)
        assert health.snapshot()["status"] == "ready"

    def test_a_client_that_never_had_a_mailbox_reports_exactly_what_it_did_before(self):
        health = ClientHealth()
        health.record_failure("daily", NOW, "boom")
        health.record_success("daily", NOW)
        assert set(health.snapshot()) == {
            "status",
            "last_runs",
            "last_error",
            "last_error_at",
            "consecutive_failures",
        }
