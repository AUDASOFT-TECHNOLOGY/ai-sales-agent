"""What an operator can do with the consent registry: through the service and through the commands.

The promises: a contact is never an argument and never printed; only an active owner with the right
password can end a block, with a reason, an evidence reference and a typed confirmation; ending a
block changes no prospect; the writing commands refuse to run while the application runs.
"""

import argparse
import hashlib
import inspect
import logging
from dataclasses import dataclass, field

import pytest
from consent_support import (
    CLIENT,
    EMAIL,
    LEAKY,
    PHONE,
    SECOND_PHONE,
    Clock,
    contact,
    make_world,
)
from pydantic import SecretStr

from sales_app.admin_auth import AdminRole, AdminUser, FileAdminUserStore, InMemoryAdminUserStore
from sales_app.admin_auth.passwords import hash_password
from sales_app.cli import main
from sales_app.consent_cli import add_consent_parser, run_consent
from sales_app.lockfile import InstanceLock
from sales_app.secret_store import FileSecretAudit, build_secret_store, generate_master_key
from sales_app.settings import Settings
from sales_app.sourcing.consent import (
    CONFIRMATION_PHRASE,
    RELEASE_QUOTA,
    ConsentAdmin,
    ConsentNotAllowedError,
    ConsentRegistry,
    ConsentSecrets,
    ConsentUnavailableError,
    FileConsentLog,
    Health,
    Origin,
    ReasonCode,
)

PASSWORD = "a long enough password for tests"
PASSWORD_HASH = hash_password(PASSWORD)  # one bcrypt hash for the whole module
EVIDENCE = "ticket #4521"


def owner(**kw):
    values = {
        "id": "owner-1",
        "username": "boss",
        "password_hash": PASSWORD_HASH,
        "role": AdminRole.OWNER,
        "created_at": Clock().current,
    }
    return AdminUser(**(values | kw))


def users_with(*accounts):
    store = InMemoryAdminUserStore()
    for account in accounts:
        store.create(account)
    return store


def release_kwargs(**kw):
    values = {
        "username": "boss",
        "password": PASSWORD,
        "reason": ReasonCode.WRITTEN_CONSENT,
        "evidence_ref": EVIDENCE,
        "confirmation": CONFIRMATION_PHRASE,
    }
    return values | kw


# ---- the service -----------------------------------------------------------------------------


class TestReleasingABlock:
    def admin(self, world, *accounts):
        return ConsentAdmin(
            world.registry, users_with(*(accounts or (owner(),))), world.audit, now=world.clock
        )

    def test_an_owner_with_the_right_password_ends_a_block(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        result = self.admin(world).release(contact(), **release_kwargs())
        assert result.released == 1
        assert not world.registry.snapshot().blocks(contact())
        (_, release) = world.registry.events()
        assert release.actor == "admin:owner-1" and release.reason is ReasonCode.WRITTEN_CONSENT
        assert release.evidence_ref == EVIDENCE and release.origin is Origin.OPERATOR

    def test_it_says_that_nothing_else_was_changed_and_that_a_source_may_block_again(
        self, tmp_path
    ):
        world = make_world(tmp_path)
        world.block()
        notice = self.admin(world).release(contact(), **release_kwargs()).notice
        assert "No prospect was changed" in notice and "block the contact again" in notice

    def test_it_is_given_no_prospect_no_store_and_no_source_so_it_cannot_reactivate_anyone(self):
        parameters = set(inspect.signature(ConsentAdmin.__init__).parameters) - {"self"}
        assert parameters == {"registry", "users", "audit", "now"}

    @pytest.mark.parametrize(
        "account",
        [
            owner(role=AdminRole.OPERATOR, client_ids=frozenset({CLIENT})),
            owner(role=AdminRole.EDITOR, client_ids=frozenset({CLIENT})),
            owner(role=AdminRole.VIEWER, client_ids=frozenset({CLIENT})),
            owner(active=False),
        ],
        ids=["operator", "editor", "viewer", "inactive owner"],
    )
    def test_only_an_active_owner_may_release(self, tmp_path, account):
        world = make_world(tmp_path)
        world.block()
        with pytest.raises(ConsentNotAllowedError) as raised:
            self.admin(world, account).release(contact(), **release_kwargs())
        assert raised.value.code == "not_authorised"
        assert world.registry.snapshot().blocks(contact())

    def test_a_wrong_password_an_unknown_name_and_a_wrong_role_are_the_same_refusal(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        admin = self.admin(world, owner(), owner(id="op", username="op", role=AdminRole.OPERATOR))
        messages = set()
        for attempt in (
            release_kwargs(password="wrong password"),
            release_kwargs(username="nobody"),
            release_kwargs(username="op"),
        ):
            with pytest.raises(ConsentNotAllowedError) as raised:
                admin.release(contact(), **attempt)
            messages.add(str(raised.value))
        assert len(messages) == 1

    def test_the_name_is_matched_ignoring_case_and_blanks(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        admin = self.admin(world)
        assert admin.release(contact(), **release_kwargs(username="  BOSS ")).released == 1

    @pytest.mark.parametrize(
        "phrase", ["", "release", "RELEASE OPT-OUT", " release opt-out", "yes"]
    )
    def test_the_confirmation_must_be_exactly_the_phrase(self, tmp_path, phrase):
        world = make_world(tmp_path)
        world.block()
        with pytest.raises(ConsentNotAllowedError) as raised:
            self.admin(world).release(contact(), **release_kwargs(confirmation=phrase))
        assert raised.value.code == "bad_confirmation"
        assert world.registry.snapshot().blocks(contact())

    @pytest.mark.parametrize("evidence", ["", "   ", "x" * 81, "name=Ana", "a\nb"])
    def test_an_evidence_reference_is_required_and_must_be_plain(self, tmp_path, evidence):
        world = make_world(tmp_path)
        world.block()
        with pytest.raises(ConsentNotAllowedError) as raised:
            self.admin(world).release(contact(), **release_kwargs(evidence_ref=evidence))
        assert raised.value.code == "evidence_required"
        assert world.registry.snapshot().blocks(contact())

    @pytest.mark.parametrize(
        "reason", [ReasonCode.KEYWORD, ReasonCode.SOURCE_SIGNAL, ReasonCode.IMPORTED]
    )
    def test_a_reason_that_is_not_for_a_release_is_refused(self, tmp_path, reason):
        world = make_world(tmp_path)
        world.block()
        with pytest.raises(ConsentNotAllowedError) as raised:
            self.admin(world).release(contact(), **release_kwargs(reason=reason))
        assert raised.value.code == "invalid_reason"

    def test_a_refusal_that_came_from_the_contact_itself_needs_written_consent(self, tmp_path):
        world = make_world(tmp_path)
        world.block(origin=Origin.INBOUND_KEYWORD, reason=ReasonCode.KEYWORD)
        admin = self.admin(world)
        for reason in (ReasonCode.REGISTRATION_ERROR,):
            with pytest.raises(ConsentNotAllowedError) as raised:
                admin.release(contact(), **release_kwargs(reason=reason))
            assert raised.value.code == "origin_rule"
        assert world.registry.snapshot().blocks(contact())
        assert admin.release(contact(), **release_kwargs()).released == 1

    def test_a_registration_error_may_lift_a_refusal_that_was_not_the_contacts_own(self, tmp_path):
        world = make_world(tmp_path)
        world.block(origin=Origin.SOURCE, reason=ReasonCode.SOURCE_SIGNAL)
        result = self.admin(world).release(
            contact(), **release_kwargs(reason=ReasonCode.REGISTRATION_ERROR)
        )
        assert result.released == 1

    def test_an_owner_may_release_only_a_few_times_a_day(self, tmp_path):
        world = make_world(tmp_path)
        admin = self.admin(world)
        numbers = [f"+216 97 000 00{i}" for i in range(RELEASE_QUOTA + 1)]
        for number in numbers:
            world.block(contact(number))
        for number in numbers[:RELEASE_QUOTA]:
            assert admin.release(contact(number), **release_kwargs()).released == 1
        with pytest.raises(ConsentNotAllowedError) as raised:
            admin.release(contact(numbers[-1]), **release_kwargs())
        assert raised.value.code == "quota_exceeded"
        assert world.registry.snapshot().blocks(contact(numbers[-1]))
        world.clock.advance(hours=24, seconds=1)  # the day has passed
        assert admin.release(contact(numbers[-1]), **release_kwargs()).released == 1

    def test_the_quota_belongs_to_the_account_not_to_the_registry(self, tmp_path):
        world = make_world(tmp_path)
        second = owner(id="owner-2", username="chief")
        admin = self.admin(world, owner(), second)
        numbers = [f"+216 97 000 00{i}" for i in range(RELEASE_QUOTA + 1)]
        for number in numbers:
            world.block(contact(number))
        for number in numbers[:RELEASE_QUOTA]:
            admin.release(contact(number), **release_kwargs())
        result = admin.release(contact(numbers[-1]), **release_kwargs(username="chief"))
        assert result.released == 1

    def test_releasing_a_contact_that_is_not_blocked_does_nothing(self, tmp_path):
        world = make_world(tmp_path)
        assert self.admin(world).release(contact(), **release_kwargs()).released == 0
        assert world.registry.events() == ()

    def test_every_identity_of_a_contact_is_released(self, tmp_path):
        world = make_world(tmp_path)
        world.block(contact(PHONE, EMAIL))
        assert self.admin(world).release(contact(PHONE, EMAIL), **release_kwargs()).released == 2
        snapshot = world.registry.snapshot()
        assert not snapshot.blocks(contact(PHONE)) and not snapshot.blocks(contact(None, EMAIL))

    def test_a_block_that_comes_back_after_a_release_is_a_new_refusal_in_force(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        self.admin(world).release(contact(), **release_kwargs())
        assert not world.registry.snapshot().blocks(contact())
        world.block(
            origin=Origin.SOURCE, reason=ReasonCode.SOURCE_SIGNAL
        )  # the source still says no
        assert world.registry.snapshot().blocks(contact())

    def test_an_unhealthy_registry_cannot_be_released_from(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        with pytest.raises(ConsentUnavailableError):
            self.admin(world).release(contact(), **release_kwargs())

    def test_every_refusal_to_act_and_every_release_is_audited_without_a_contact(self, tmp_path):
        world = make_world(tmp_path)
        world.block(contact(PHONE, EMAIL))
        admin = self.admin(world)
        for bad in (
            release_kwargs(password="nope"),
            release_kwargs(confirmation="no"),
            release_kwargs(evidence_ref=""),
            release_kwargs(reason=ReasonCode.KEYWORD),
        ):
            with pytest.raises(ConsentNotAllowedError):
                admin.release(contact(PHONE, EMAIL), **bad)
        admin.release(contact(PHONE, EMAIL), **release_kwargs())
        mine = [e for e in world.audit.recent() if e.key == "release"]
        assert sorted(e.outcome for e in mine) == ["rejected"] * 4 + ["released"]
        assert {e.reason for e in mine if e.outcome == "rejected"} == {
            "not_authorised",
            "bad_confirmation",
            "evidence_required",
            "invalid_reason",
        }
        text = repr(world.audit.recent())
        for leaky in LEAKY + (PASSWORD,):
            assert leaky not in text


class TestOtherOperatorActions:
    def admin(self, world):
        return ConsentAdmin(world.registry, users_with(owner()), world.audit, now=world.clock)

    def test_a_refusal_an_operator_was_told_of_is_recorded(self, tmp_path):
        world = make_world(tmp_path)
        recorded = self.admin(world).record(
            contact(PHONE, EMAIL), actor="cli:tester", reason=ReasonCode.OPERATOR_REQUEST,
            evidence_ref="call 12 Mar",
        )  # fmt: skip
        assert recorded == 2
        assert {e.origin for e in world.registry.events()} == {Origin.OPERATOR}

    @pytest.mark.parametrize("reason", [ReasonCode.WRITTEN_CONSENT, ReasonCode.REGISTRATION_ERROR])
    def test_a_release_reason_cannot_record_a_refusal(self, tmp_path, reason):
        world = make_world(tmp_path)
        with pytest.raises(ConsentNotAllowedError):
            self.admin(world).record(contact(), actor="cli:tester", reason=reason)

    def test_checking_a_contact_tells_the_state_of_each_identity_and_never_the_contact(
        self, tmp_path
    ):
        world = make_world(tmp_path)
        world.clock.advance(days=1)
        world.block(contact(PHONE), origin=Origin.SOURCE, reason=ReasonCode.SOURCE_SIGNAL)
        result = self.admin(world).check(contact(PHONE, EMAIL))
        states = {i.kind: i for i in result.identities}
        assert states["phone"].blocked and states["phone"].origin is Origin.SOURCE
        assert states["phone"].reason is ReasonCode.SOURCE_SIGNAL and states["phone"].events == 1
        assert states["phone"].since == world.clock.current
        assert not states["email"].blocked and states["email"].events == 0
        assert result.blocked
        for leaky in LEAKY:
            assert leaky not in repr(result)

    def test_an_unhealthy_registry_reports_every_contact_blocked(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        result = self.admin(world).check(contact())
        assert result.blocked and result.identities == ()
        assert result.report.health is Health.NOT_INITIALISED

    def test_a_released_contact_is_shown_as_not_blocked_with_its_history(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        admin = self.admin(world)
        admin.release(contact(), **release_kwargs())
        (state,) = admin.check(contact()).identities
        assert (
            not state.blocked and state.events == 2 and state.reason is ReasonCode.WRITTEN_CONSENT
        )

    def test_the_service_shows_nothing_of_itself_when_printed(self, tmp_path):
        world = make_world(tmp_path)
        assert "hmac" not in repr(self.admin(world)).lower() and PASSWORD not in repr(
            self.admin(world)
        )


# ---- the commands ------------------------------------------------------------------------------


@dataclass
class Prompts:
    """Scripted answers to the hidden prompts, matched on a fragment of the prompt text."""

    answers: dict[str, list[str]] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)

    def getpass(self, prompt: str = "") -> str:
        self.asked.append(prompt)
        for fragment, queue in self.answers.items():
            if fragment in prompt:
                assert queue, f"no answer left for {fragment!r}"
                return queue.pop(0)
        raise AssertionError(f"unexpected prompt {prompt!r}")

    def input(self, prompt: str = "") -> str:
        self.asked.append(prompt)
        queue = self.answers["confirm"]
        assert queue, "no confirmation left"
        return queue.pop(0)


class Cli:
    def __init__(self, tmp_path, monkeypatch, capsys):
        self.tmp = tmp_path
        self.capsys = capsys
        self.clients = tmp_path / "clients"
        (self.clients / CLIENT).mkdir(parents=True)
        (self.clients / CLIENT / "client.yaml").write_text(f"client_id: {CLIENT}\n")
        self.key = generate_master_key()
        self.settings = Settings(
            clients_dir=self.clients,
            secrets_dir=tmp_path / "secrets",
            secrets_key=self.key,
            lock_file=tmp_path / "app.lock",
            admin_dir=tmp_path / "admin",
            profiles_dir=tmp_path / "profiles",
            config_dir=tmp_path / "config",
            database_url=None,
        )
        self.prompts = Prompts()
        monkeypatch.setattr("getpass.getpass", self.prompts.getpass)
        monkeypatch.setattr("builtins.input", self.prompts.input)
        parser = argparse.ArgumentParser()
        add_consent_parser(parser.add_subparsers(dest="command", required=True))
        self.parser = parser

    def say(self, *, phone=(), email=(), password=(), confirm=()):
        self.prompts.answers = {
            "Phone number": list(phone),
            "E-mail address": list(email),
            "Password": list(password),
            "confirm": list(confirm),
        }

    def run(self, *argv):
        code = run_consent(self.settings, self.parser.parse_args(["consent", *argv]))
        out, err = self.capsys.readouterr()
        return code, out, err

    def contact_prompts(self, number=PHONE, address="", times=1):
        self.say(phone=[number] * times, email=[address] * times)

    def add_owner(self, **kw):
        FileAdminUserStore(self.settings.admin_dir / "users.json").create(owner(**kw))

    def audit_text(self):
        path = self.settings.secrets_dir / "audit.jsonl"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def registry(self):
        store = build_secret_store(self.settings.secrets_dir, self.key)
        audit = FileSecretAudit(self.settings.secrets_dir / "audit.jsonl")
        log = FileConsentLog(self.settings.secrets_dir / "consent" / f"{CLIENT}.jsonl")
        return ConsentRegistry(CLIENT, log, ConsentSecrets(store, audit, CLIENT))

    def tree(self, path):
        return {
            p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*"))
            if p.is_file()
        }


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    return Cli(tmp_path, monkeypatch, capsys)


class TestSettingUpAndLooking:
    def test_setting_up_is_explicit_and_safe_to_repeat(self, cli):
        assert cli.run("init", "--client", CLIENT)[0] == 0
        key = cli.registry()._secrets.get_root_key()
        code, out, _ = cli.run("init", "--client", CLIENT)
        assert code == 0 and "already set up" in out and "nothing changed" in out
        assert cli.registry()._secrets.get_root_key() == key

    def test_nothing_is_set_up_by_just_looking(self, cli):
        code, out, _ = cli.run("status", "--client", CLIENT)
        assert code == 1 and "not_initialised" in out and "every contact is blocked" in out
        assert not list(cli.settings.secrets_dir.rglob("*.jsonl"))  # no registry was created
        assert not (cli.settings.secrets_dir / "secrets.json").exists()

    def test_the_status_of_a_healthy_registry(self, cli):
        cli.run("init", "--client", CLIENT)
        code, out, _ = cli.run("status", "--client", CLIENT)
        assert code == 0 and "health: ok" in out and "events: 0" in out
        assert "does not protect against someone able to restore the log" in out

    def test_verify_chain_is_ok_then_catches_a_deleted_event(self, cli):
        cli.run("init", "--client", CLIENT)
        for number in (PHONE, SECOND_PHONE):
            cli.contact_prompts(number)
            assert cli.run("record", "--client", CLIENT, "--reason", "operator_request")[0] == 0
        assert cli.run("verify-chain", "--client", CLIENT)[0] == 0
        log = cli.settings.secrets_dir / "consent" / f"{CLIENT}.jsonl"
        log.write_text(log.read_text().splitlines()[0] + "\n")
        code, out, _ = cli.run("verify-chain", "--client", CLIENT)
        assert code == 4 and "corrupt" in out and "anchor_ahead" in out

    def test_it_will_not_set_up_over_a_corrupt_registry(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.contact_prompts()
        cli.run("record", "--client", CLIENT, "--reason", "operator_request")
        key = cli.registry()._secrets.get_root_key()
        (cli.settings.secrets_dir / "consent" / f"{CLIENT}.jsonl").unlink()
        code, _, err = cli.run("init", "--client", CLIENT)
        assert code == 4 and "refusing to create a new key" in err
        assert cli.registry()._secrets.get_root_key() == key

    def test_an_unknown_or_malformed_client_is_refused(self, cli):
        assert cli.run("status", "--client", "nobody")[0] == 2
        assert cli.run("status", "--client", "Bad Client")[0] == 2
        assert cli.run("status", "--client", "../x")[0] == 2

    def test_without_a_master_key_it_cannot_be_set_up_and_says_why(self, cli):
        cli.settings = cli.settings.model_copy(update={"secrets_key": None})
        code, _, err = cli.run("init", "--client", CLIENT)
        assert code == 1 and "cannot be written" in err

    def test_a_wrong_master_key_is_reported_not_guessed_around(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.settings = cli.settings.model_copy(
            update={"secrets_key": SecretStr(generate_master_key())}
        )
        code, _, err = cli.run("status", "--client", CLIENT)
        assert code == 1 and "master key" in err

    def test_the_command_is_reachable_from_the_main_program(self, cli, monkeypatch):
        monkeypatch.setattr(
            "sales_app.cli.load_dotenv", lambda *a, **k: None
        )  # never read a developer's .env
        monkeypatch.chdir(cli.tmp)
        for name, value in {
            "SALES_AGENT_CLIENTS_DIR": cli.clients,
            "SALES_AGENT_SECRETS_DIR": cli.settings.secrets_dir,
            "SALES_AGENT_SECRETS_KEY": cli.key,
            "SALES_AGENT_LOCK_FILE": cli.tmp / "app.lock",
        }.items():
            monkeypatch.setenv(name, str(value))
        assert main(["consent", "init", "--client", CLIENT]) == 0
        assert main(["consent", "status", "--client", CLIENT]) == 0


class TestWhileTheApplicationRuns:
    def test_the_commands_that_write_refuse_and_the_ones_that_read_do_not(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.add_owner()
        with InstanceLock(cli.settings.lock_file):
            assert cli.run("status", "--client", CLIENT)[0] == 0
            assert cli.run("verify-chain", "--client", CLIENT)[0] == 0
            cli.contact_prompts()
            assert cli.run("check", "--client", CLIENT)[0] == 0
            code, _, err = cli.run("init", "--client", CLIENT)
            assert code == 3 and "application is running" in err
            cli.contact_prompts()
            assert cli.run("record", "--client", CLIENT, "--reason", "other")[0] == 3
            assert cli.run("release", "--client", CLIENT, "--username", "boss",
                           "--reason", "other", "--evidence", EVIDENCE)[0] == 3  # fmt: skip
        assert cli.registry().report().events == 0  # nothing was written meanwhile
        assert cli.run("init", "--client", CLIENT)[0] == 0  # and once it stops, it works


class TestRecordingAndChecking:
    def test_a_recorded_refusal_is_found_by_check(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.contact_prompts(PHONE, EMAIL)
        code, out, _ = cli.run("record", "--client", CLIENT, "--reason", "operator_request",
                               "--evidence", "call 12 Mar")  # fmt: skip
        assert code == 0 and "2 identity(ies) newly blocked" in out
        cli.contact_prompts(PHONE, "")
        code, out, _ = cli.run("check", "--client", CLIENT)
        assert code == 0 and "phone: BLOCKED since" in out and "origin: operator" in out
        cli.contact_prompts(SECOND_PHONE, "")
        assert "phone: not blocked" in cli.run("check", "--client", CLIENT)[1]

    def test_recording_the_same_refusal_again_changes_nothing(self, cli):
        cli.run("init", "--client", CLIENT)
        for _ in range(2):
            cli.contact_prompts()
            out = cli.run("record", "--client", CLIENT, "--reason", "other")[1]
        assert "already blocked" in out and cli.registry().report().events == 1

    @pytest.mark.parametrize("option", ["--phone", "--email", "--contact", "--number", "--address"])
    def test_a_contact_is_never_taken_as_an_argument(self, cli, option):
        for command in ("record", "check", "release"):
            argv = ["consent", command, "--client", CLIENT, option, PHONE]
            with pytest.raises(SystemExit):
                cli.parser.parse_args(argv)
        cli.capsys.readouterr()

    def test_a_contact_is_asked_for_with_a_hidden_prompt(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.contact_prompts()
        cli.run("check", "--client", CLIENT)
        assert cli.prompts.asked and all("hidden" in prompt for prompt in cli.prompts.asked)

    def test_nothing_to_look_up_is_an_invalid_input(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.contact_prompts("", "")
        code, _, err = cli.run("check", "--client", CLIENT)
        assert code == 2 and "phone number" in err
        cli.contact_prompts("", "")
        assert cli.run("record", "--client", CLIENT, "--reason", "other")[0] == 2

    def test_an_evidence_reference_with_odd_characters_is_refused_before_asking_anything(self, cli):
        cli.run("init", "--client", CLIENT)
        code, _, err = cli.run("record", "--client", CLIENT, "--reason", "other",
                               "--evidence", "name=Ana <b>")  # fmt: skip
        assert code == 2 and "--evidence" in err and cli.prompts.asked == []

    def test_an_unhealthy_registry_says_every_contact_is_blocked(self, cli):
        cli.contact_prompts()
        code, _, err = cli.run("record", "--client", CLIENT, "--reason", "other")
        assert code == 1 and "every contact is blocked" in err
        cli.contact_prompts()
        code, out, _ = cli.run("check", "--client", CLIENT)
        assert code == 1 and "every contact is blocked" in out


class TestReleasingFromTheCommandLine:
    def blocked(self, cli, *numbers):
        cli.run("init", "--client", CLIENT)
        cli.add_owner()
        for number in numbers or (PHONE,):
            cli.contact_prompts(number)
            cli.run("record", "--client", CLIENT, "--reason", "operator_request")

    def release(self, cli, number=PHONE, *, password=PASSWORD, confirm=CONFIRMATION_PHRASE,
                again=None, reason="written_consent", evidence=EVIDENCE, username="boss"):  # fmt: skip
        cli.say(
            phone=[number, again or number], email=["", ""], password=[password], confirm=[confirm]
        )
        return cli.run("release", "--client", CLIENT, "--username", username, "--reason", reason,
                       "--evidence", evidence)  # fmt: skip

    def is_blocked(self, cli, number=PHONE):
        cli.contact_prompts(number)
        return "BLOCKED" in cli.run("check", "--client", CLIENT)[1]

    def test_an_owner_ends_a_block_and_no_prospect_is_touched(self, cli):
        self.blocked(cli)
        before = {name: cli.tree(path) for name, path in (
            ("clients", cli.clients), ("admin", cli.settings.admin_dir)) if path.exists()}  # fmt: skip
        code, out, _ = self.release(cli)
        assert code == 0 and "1 identity(ies) released" in out and "No prospect was changed" in out
        assert not self.is_blocked(cli)
        after = {name: cli.tree(path) for name, path in (
            ("clients", cli.clients), ("admin", cli.settings.admin_dir)) if path.exists()}  # fmt: skip
        assert after == before
        assert not (cli.tmp / "profiles").exists() and not (cli.tmp / "config").exists()

    @pytest.mark.parametrize(
        "change",
        [
            {"password": "wrong password"},
            {"username": "nobody"},
            {"confirm": "yes"},
            {"evidence": ""},
            {"reason": "other", "confirm": ""},
        ],
        ids=["bad password", "unknown owner", "bad confirmation", "no evidence", "no confirmation"],
    )
    def test_a_refused_release_leaves_the_block_in_force(self, cli, change):
        self.blocked(cli)
        code, _, err = self.release(cli, **change)
        assert code == 2 and err.startswith("error:")
        assert self.is_blocked(cli)

    def test_a_non_owner_cannot_release(self, cli):
        self.blocked(cli)
        cli.add_owner(
            id="op", username="op", role=AdminRole.OPERATOR, client_ids=frozenset({CLIENT})
        )
        code, _, err = self.release(cli, username="op")
        assert code == 2 and "not authorised" in err and self.is_blocked(cli)

    def test_two_different_entries_stop_everything_before_the_password_is_asked(self, cli):
        self.blocked(cli)
        cli.say(
            phone=[PHONE, SECOND_PHONE],
            email=["", ""],
            password=[PASSWORD],
            confirm=[CONFIRMATION_PHRASE],
        )
        code, _, err = cli.run("release", "--client", CLIENT, "--username", "boss",
                               "--reason", "written_consent", "--evidence", EVIDENCE)  # fmt: skip
        assert code == 2 and "differ" in err
        assert not any("Password" in p for p in cli.prompts.asked) and self.is_blocked(cli)

    def test_the_sixth_release_of_the_day_is_refused(self, cli):
        numbers = [f"+216 96 000 00{i}" for i in range(RELEASE_QUOTA + 1)]
        self.blocked(cli, *numbers)
        for number in numbers[:RELEASE_QUOTA]:
            assert self.release(cli, number)[0] == 0
        code, _, err = self.release(cli, numbers[-1])
        assert code == 2 and "too many releases" in err and self.is_blocked(cli, numbers[-1])

    def test_a_refusal_from_the_contacts_own_message_needs_written_consent(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.add_owner()
        from sales_app.sourcing.consent import ConsentWriter  # noqa: F401

        cli.registry().record_opt_out(contact(), origin=Origin.INBOUND_KEYWORD,
                                      reason=ReasonCode.KEYWORD, actor="system:engine")  # fmt: skip
        code, _, err = self.release(cli, reason="registration_error")
        assert code == 2 and "only 'written_consent'" in err and self.is_blocked(cli)
        assert self.release(cli, reason="written_consent")[0] == 0 and not self.is_blocked(cli)

    def test_a_release_is_audited_and_so_are_the_refusals(self, cli):
        self.blocked(cli)
        self.release(cli, password="wrong password")
        self.release(cli)
        text = cli.audit_text()
        assert '"outcome": "rejected"' in text and '"reason": "not_authorised"' in text
        assert '"outcome": "released"' in text and "consent:client-a" in text


class TestNothingLeaks:
    def test_no_command_prints_or_logs_a_contact_a_password_a_key_or_a_digest(self, cli, caplog):
        caplog.set_level(logging.DEBUG)
        shown = []
        shown.append(cli.run("init", "--client", CLIENT))
        cli.add_owner()
        cli.contact_prompts(PHONE, EMAIL)
        shown.append(cli.run("record", "--client", CLIENT, "--reason", "operator_request",
                             "--evidence", "ticket 9"))  # fmt: skip
        cli.contact_prompts(PHONE, EMAIL)
        shown.append(cli.run("check", "--client", CLIENT))
        shown.append(cli.run("status", "--client", CLIENT))
        shown.append(cli.run("verify-chain", "--client", CLIENT))
        cli.say(phone=[PHONE, PHONE], email=[EMAIL, EMAIL], password=["wrong password"],
                confirm=[CONFIRMATION_PHRASE])  # fmt: skip
        shown.append(cli.run("release", "--client", CLIENT, "--username", "boss",
                             "--reason", "written_consent", "--evidence", EVIDENCE))  # fmt: skip
        cli.say(phone=[PHONE, PHONE], email=[EMAIL, EMAIL], password=[PASSWORD],
                confirm=[CONFIRMATION_PHRASE])  # fmt: skip
        shown.append(cli.run("release", "--client", CLIENT, "--username", "boss",
                             "--reason", "written_consent", "--evidence", EVIDENCE))  # fmt: skip
        registry = cli.registry()
        digests = {e.key for e in registry.events()}
        everything = "\n".join(
            [text for _, out, err in shown for text in (out, err)]
            + [caplog.text, cli.audit_text()]
            + [p.read_text(encoding="utf-8", errors="replace") for p in cli.tmp.rglob("*.jsonl")]
        )
        assert digests and "BLOCKED" in "".join(out for _, out, _ in shown)
        for secret in (*LEAKY, PASSWORD, "wrong password", cli.key):
            assert secret not in everything.replace("ticket 9", ""), secret
        shown_text = "\n".join(text for _, out, err in shown for text in (out, err))
        assert not any(d in shown_text for d in digests)  # a digest is never shown either
        assert not any(d in cli.audit_text() for d in digests)

    def test_the_prompts_do_not_echo_what_is_typed(self, cli):
        cli.run("init", "--client", CLIENT)
        cli.contact_prompts()
        cli.run("check", "--client", CLIENT)
        # `getpass` is what asks (hidden); `input` is used only for the typed confirmation phrase
        assert cli.prompts.asked and all("hidden" in p for p in cli.prompts.asked)
