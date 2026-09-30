"""Configuration loading and validation."""

from pathlib import Path

import pytest
import yaml
from app_support import client_yaml, env_for, write_client

from sales_app.config import (
    EMPTY_LLM_REGISTRY,
    ClientConfig,
    Secrets,
    build_llm_registry,
    clamp_mode,
    load_client_configs,
)
from sales_core.approval import ApprovalMode

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVAL = ApprovalMode.APPROVAL
DRY = ApprovalMode.DRY_RUN
AUTO = ApprovalMode.AUTONOMOUS


def load(root, *client_ids, ceiling=AUTO, env=None, llm_registry=EMPTY_LLM_REGISTRY):
    return load_client_configs(
        root,
        env=env if env is not None else env_for(*client_ids),
        mode_ceiling=ceiling,
        llm_registry=llm_registry,
    )


class TestValidClient:
    def test_loads_and_exposes_the_validated_spec(self, tmp_path):
        write_client(tmp_path, "alpha")
        loaded = load(tmp_path, "alpha")
        assert loaded.errors == () and loaded.disabled == ()
        (config,) = loaded.clients
        assert isinstance(config, ClientConfig)
        assert config.client_id == "alpha"
        assert config.spec.timezone == "Africa/Tunis"
        assert config.spec.brain.factory == "app_test_brains:good_brain"
        assert config.spec.brain.settings == {"tone": "alpha"}
        assert config.effective_mode is APPROVAL and not config.mode_clamped
        assert config.phone_number_id == "phone-alpha"

    def test_defaults_are_safe_and_match_the_agreed_schedule(self, tmp_path):
        data = client_yaml("alpha")
        del data["mode"]
        write_client(tmp_path, "alpha", raw=yaml.safe_dump(data))
        (config,) = load(tmp_path, "alpha").clients
        spec = config.spec
        assert spec.mode is DRY
        assert spec.daily_action_limit == 20 and spec.max_attempts == 3
        assert spec.opt_out_keywords == ("stop",)
        schedule = spec.schedule
        assert schedule.daily_at == "09:00"
        assert schedule.days == ("mon", "tue", "wed", "thu", "fri", "sat")  # Sunday off
        assert schedule.weekdays == {0, 1, 2, 3, 4, 5}
        assert (schedule.prepare_every_minutes, schedule.dispatch_every_minutes) == (60, 5)
        assert schedule.recover_every_minutes == 15

    def test_schedule_is_configurable_per_client(self, tmp_path):
        write_client(
            tmp_path,
            "alpha",
            schedule={"daily_at": "07:30", "days": ["mon", "sun"], "dispatch_every_minutes": 2},
        )
        (config,) = load(tmp_path, "alpha").clients
        assert config.spec.schedule.daily_time.hour == 7
        assert config.spec.schedule.weekdays == {0, 6}

    def test_two_clients_are_loaded_independently(self, tmp_path):
        write_client(tmp_path, "alpha", brain={"settings": {"tone": "warm"}})
        write_client(tmp_path, "beta", mode="dry_run", brain={"settings": {"tone": "formal"}})
        loaded = load(tmp_path, "alpha", "beta")
        by_id = {c.client_id: c for c in loaded.clients}
        assert by_id["alpha"].spec.brain.settings == {"tone": "warm"}
        assert by_id["beta"].spec.brain.settings == {"tone": "formal"}
        assert by_id["alpha"].secrets.get("ALPHA_TOKEN") == "token-alpha"
        assert "BETA_TOKEN" not in by_id["alpha"].secrets  # nothing leaks between clients


class TestDiscovery:
    def test_template_and_hidden_directories_are_ignored(self, tmp_path):
        write_client(tmp_path, "_template", raw="this: is not even valid config")
        write_client(tmp_path, ".hidden", raw="nope")
        write_client(tmp_path, "alpha")
        loaded = load(tmp_path, "alpha")
        assert loaded.errors == () and [c.client_id for c in loaded.clients] == ["alpha"]

    def test_disabled_clients_are_excluded_without_being_validated(self, tmp_path):
        write_client(
            tmp_path, "beta", raw="enabled: false\nsheets: {spreadsheet_id: TODO}\nbogus: 1\n"
        )
        loaded = load(tmp_path, env={})
        assert loaded.clients == () and loaded.disabled == ("beta",) and loaded.errors == ()

    def test_only_a_literal_false_disables(self, tmp_path):
        write_client(tmp_path, "alpha", enabled="no thanks")
        loaded = load(tmp_path, "alpha")
        assert loaded.disabled == () and any("enabled" in e for e in loaded.errors)

    def test_missing_clients_directory(self, tmp_path):
        loaded = load(tmp_path / "nowhere", env={})
        assert "clients directory not found" in loaded.errors[0]

    def test_directory_without_a_config_file(self, tmp_path):
        (tmp_path / "alpha").mkdir()
        assert load(tmp_path, env={}).errors == ("alpha: client.yaml not found",)

    @pytest.mark.parametrize("raw", ["[1, 2]", "just text", ""])
    def test_config_must_be_a_mapping(self, tmp_path, raw):
        write_client(tmp_path, "alpha", raw=raw)
        assert "must contain a mapping" in load(tmp_path, env={}).errors[0]

    def test_unparsable_yaml(self, tmp_path):
        write_client(tmp_path, "alpha", raw="a: [unclosed")
        assert "cannot read client.yaml" in load(tmp_path, env={}).errors[0]


class TestValidation:
    def test_unknown_keys_are_rejected_with_their_location(self, tmp_path):
        write_client(tmp_path, "alpha", schedule={"dayz": ["mon"]})
        errors = load(tmp_path, "alpha").errors
        assert any("schedule.dayz" in e for e in errors)

    def test_client_id_must_match_the_directory(self, tmp_path):
        write_client(tmp_path, "alpha", client_id="gamma")
        assert any("must equal the directory name" in e for e in load(tmp_path, "gamma").errors)

    @pytest.mark.parametrize("bad", ["Not Valid", "UPPER", "-lead", ""])
    def test_client_id_shape(self, tmp_path, bad):
        write_client(tmp_path, "alpha", client_id=bad)
        assert any("client_id" in e for e in load(tmp_path, "alpha").errors)

    def test_unknown_timezone(self, tmp_path):
        write_client(tmp_path, "alpha", timezone="Mars/Olympus")
        assert any("unknown timezone" in e for e in load(tmp_path, "alpha").errors)

    def test_daily_time_must_be_a_quoted_hh_mm(self, tmp_path):
        for bad in ('"9:00"', '"25:00"', '"09:60"', "10:30"):  # 10:30 unquoted is the number 630
            data = yaml.safe_dump(client_yaml("alpha")) + f"schedule:\n  daily_at: {bad}\n"
            write_client(tmp_path, "alpha", raw=data)
            errors = load(tmp_path, "alpha").errors
            assert any("schedule.daily_at" in e for e in errors), bad

    def test_days_must_be_known_unique_and_non_empty(self, tmp_path):
        for bad in (["funday"], [], ["mon", "mon"]):
            write_client(tmp_path, "alpha", schedule={"days": bad})
            assert any("schedule.days" in e for e in load(tmp_path, "alpha").errors), bad

    @pytest.mark.parametrize(
        ("override", "where"),
        [
            ({"daily_action_limit": 0}, "daily_action_limit"),
            ({"max_attempts": 0}, "max_attempts"),
            ({"mode": "yolo"}, "mode"),
            ({"schedule": {"dispatch_every_minutes": 0}}, "dispatch_every_minutes"),
            ({"schedule": {"prepare_every_minutes": 4}}, "prepare_every_minutes"),
            ({"schedule": {"prepare_every_minutes": 1441}}, "prepare_every_minutes"),
            ({"brain": {"factory": "not a path"}}, "brain.factory"),
            ({"whatsapp": {"token_env": "has space"}}, "whatsapp.token_env"),
        ],
    )
    def test_value_ranges_and_shapes(self, tmp_path, override, where):
        write_client(tmp_path, "alpha", **override)
        assert any(where in e for e in load(tmp_path, "alpha").errors)

    @pytest.mark.parametrize(
        ("override", "where"),
        [
            ({"sheets": {"spreadsheet_id": "TODO"}}, "sheets.spreadsheet_id"),
            ({"llm": {"model": "CHANGE_ME"}}, "llm.model"),
            ({"brain": {"factory": "CHANGE_ME_brain.brain:create"}}, "brain.factory"),
            ({"whatsapp": {"token_env": "CHANGE_ME_TOKEN"}}, "whatsapp.token_env"),
        ],
    )
    def test_placeholders_are_rejected_for_enabled_clients(self, tmp_path, override, where):
        write_client(tmp_path, "alpha", **override)
        assert any(where in e and "placeholder" in e for e in load(tmp_path, "alpha").errors)

    def test_missing_required_sections(self, tmp_path):
        data = client_yaml("alpha")
        del data["llm"]
        write_client(tmp_path, "alpha", raw=yaml.safe_dump(data))
        assert any(e.startswith("alpha: llm") for e in load(tmp_path, "alpha").errors)

    def test_every_problem_across_every_client_is_reported_together(self, tmp_path):
        write_client(tmp_path, "alpha", timezone="Mars/Olympus")
        write_client(tmp_path, "beta", daily_action_limit=0)
        write_client(tmp_path, "gamma")  # valid, and still loaded
        loaded = load(tmp_path, "alpha", "beta", "gamma")
        assert any(e.startswith("alpha:") for e in loaded.errors)
        assert any(e.startswith("beta:") for e in loaded.errors)
        assert [c.client_id for c in loaded.clients] == ["gamma"]


class TestSecrets:
    def test_missing_variables_are_named_not_valued(self, tmp_path):
        write_client(tmp_path, "alpha")
        loaded = load(tmp_path, env={})
        text = "\n".join(loaded.errors)
        for name in (
            "GOOGLE_APPLICATION_CREDENTIALS",
            "OPENAI_API_KEY",
            "ALPHA_TOKEN",
            "ALPHA_APP_SECRET",
            "ALPHA_PHONE_ID",
        ):
            assert name in text
        assert loaded.clients == ()

    def test_blank_values_count_as_missing(self, tmp_path):
        write_client(tmp_path, "alpha")
        env = env_for("alpha") | {"ALPHA_TOKEN": "   "}
        assert any("ALPHA_TOKEN" in e for e in load(tmp_path, env=env).errors)

    def test_dry_run_does_not_need_whatsapp_secrets(self, tmp_path):
        write_client(tmp_path, "alpha", mode="dry_run")
        env = {"GOOGLE_APPLICATION_CREDENTIALS": "c.json", "OPENAI_API_KEY": "sk"}
        loaded = load(tmp_path, env=env)
        assert loaded.errors == () and loaded.clients[0].phone_number_id is None

    def test_a_clamped_client_needs_the_secrets_of_its_effective_mode(self, tmp_path):
        write_client(tmp_path, "alpha", mode="approval")
        env = {"GOOGLE_APPLICATION_CREDENTIALS": "c.json", "OPENAI_API_KEY": "sk"}
        assert load(tmp_path, env=env, ceiling=AUTO).errors  # approval: WhatsApp needed
        assert load(tmp_path, env=env, ceiling=DRY).errors == ()  # clamped to dry_run: not needed

    def test_secrets_never_print_their_values(self, tmp_path):
        write_client(tmp_path, "alpha")
        (config,) = load(tmp_path, "alpha").clients
        for rendering in (repr(config.secrets), str(config.secrets), repr(config)):
            assert "token-alpha" not in rendering and "secret-alpha" not in rendering
        assert isinstance(config.secrets, Secrets)


class TestModeCeiling:
    @pytest.mark.parametrize(
        ("requested", "ceiling", "expected"),
        [
            (DRY, AUTO, DRY),
            (APPROVAL, AUTO, APPROVAL),
            (AUTO, AUTO, AUTO),
            (AUTO, APPROVAL, APPROVAL),
            (AUTO, DRY, DRY),
            (APPROVAL, DRY, DRY),
            (DRY, DRY, DRY),
            (APPROVAL, APPROVAL, APPROVAL),
        ],
    )
    def test_effective_mode_is_the_safer_of_the_two(self, requested, ceiling, expected):
        assert clamp_mode(requested, ceiling) is expected

    def test_autonomous_in_a_file_is_clamped_by_the_default_ceiling(self, tmp_path):
        write_client(tmp_path, "alpha", mode="autonomous")
        (config,) = load(tmp_path, "alpha", ceiling=APPROVAL).clients
        assert config.effective_mode is APPROVAL and config.mode_clamped
        assert config.spec.mode is AUTO  # what the file asked for is still visible

    def test_autonomous_needs_the_deployment_ceiling_to_allow_it(self, tmp_path):
        write_client(tmp_path, "alpha", mode="autonomous")
        (config,) = load(tmp_path, "alpha", ceiling=AUTO).clients
        assert config.effective_mode is AUTO and not config.mode_clamped


class TestLLMAllowList:
    """The deployment's `llm.provider`/`llm.model` allow-list, enforced here exactly like the
    mode ceiling above: in `resolve_client_config`, never in `ClientFile` itself."""

    def test_with_no_registry_configured_any_provider_and_model_load_as_before(self, tmp_path):
        write_client(tmp_path, "alpha", llm={"model": "whatever-model"})
        loaded = load(tmp_path, "alpha")  # default llm_registry: EMPTY_LLM_REGISTRY
        assert loaded.errors == ()
        assert loaded.clients[0].spec.llm.model == "whatever-model"

    def test_an_allowed_provider_and_model_loads_normally(self, tmp_path):
        write_client(tmp_path, "alpha", llm={"model": "gpt-4o"})
        registry = build_llm_registry({"openai": ["gpt-4o"]})
        loaded = load(tmp_path, "alpha", llm_registry=registry)
        assert loaded.errors == ()
        assert loaded.clients[0].spec.llm.model == "gpt-4o"

    def test_a_provider_outside_the_allow_list_is_refused_explicitly(self, tmp_path):
        write_client(tmp_path, "alpha", llm={"provider": "meta", "model": "muse-spark"})
        registry = build_llm_registry({"openai": ["gpt-4o"]})
        loaded = load(tmp_path, "alpha", llm_registry=registry)
        assert loaded.clients == ()
        assert any(
            "meta" in e and "not on the deployment's allowed list" in e for e in loaded.errors
        )

    def test_a_model_not_allowed_for_its_provider_is_refused_explicitly(self, tmp_path):
        write_client(tmp_path, "alpha", llm={"model": "gpt-3.5-turbo"})
        registry = build_llm_registry({"openai": ["gpt-4o"]})
        loaded = load(tmp_path, "alpha", llm_registry=registry)
        assert loaded.clients == ()
        assert any("gpt-3.5-turbo" in e and "openai" in e for e in loaded.errors)

    def test_the_error_is_collected_alongside_every_other_problem(self, tmp_path):
        """Like every other post-schema check in resolve_client_config (missing secrets, the
        brain's own settings): nothing raises, everything is collected, so one run reports every
        problem for this client at once -- here, together with a missing secret."""
        write_client(tmp_path, "alpha", llm={"model": "gpt-3.5-turbo"})
        registry = build_llm_registry({"openai": ["gpt-4o"]})
        loaded = load(tmp_path, "alpha", llm_registry=registry, env={})
        assert loaded.clients == ()
        joined = "\n".join(loaded.errors)
        assert "gpt-3.5-turbo" in joined and "environment variable" in joined


class TestCrossClientIsolation:
    @pytest.mark.parametrize(
        ("override", "what"),
        [
            ({"sheets": {"spreadsheet_id": "shared-sheet"}}, "spreadsheet_id"),
            ({"whatsapp": {"token_env": "SHARED_TOKEN"}}, "whatsapp.token_env"),
            ({"whatsapp": {"phone_number_id_env": "SHARED_PHONE"}}, "whatsapp.phone_number_id_env"),
        ],
    )
    def test_clients_must_not_share_an_account(self, tmp_path, override, what):
        write_client(tmp_path, "alpha", **override)
        write_client(tmp_path, "beta", **override)
        env = env_for("alpha", "beta") | {"SHARED_TOKEN": "t", "SHARED_PHONE": "p"}
        errors = load(tmp_path, env=env).errors
        assert any(what in e and "alpha" in e and "beta" in e for e in errors)

    def test_clients_must_not_share_a_phone_number_id_value(self, tmp_path):
        write_client(tmp_path, "alpha")
        write_client(tmp_path, "beta")
        env = env_for("alpha", "beta") | {"BETA_PHONE_ID": "phone-alpha"}
        assert any("phone_number_id 'phone-alpha'" in e for e in load(tmp_path, env=env).errors)

    def test_the_llm_key_may_be_shared(self, tmp_path):
        write_client(tmp_path, "alpha")
        write_client(tmp_path, "beta")
        assert load(tmp_path, "alpha", "beta").errors == ()


class TestShippedConfiguration:
    def test_the_repository_clients_are_valid_and_disabled_until_filled_in(self):
        loaded = load_client_configs(REPO_ROOT / "clients", env={}, mode_ceiling=APPROVAL)
        assert loaded.errors == ()
        assert loaded.clients == ()
        assert loaded.disabled == ("naga", "tounesimmo")

    @pytest.mark.parametrize("client", ["naga", "tounesimmo"])
    def test_the_shipped_files_become_invalid_if_enabled_with_placeholders(self, tmp_path, client):
        raw = (REPO_ROOT / "clients" / client / "client.yaml").read_text(encoding="utf-8")
        write_client(tmp_path, client, raw=raw.replace("enabled: false", "enabled: true"))
        errors = load(tmp_path, env=env_for(client)).errors
        assert any("spreadsheet_id" in e and "placeholder" in e for e in errors)
        assert any("llm.model" in e for e in errors)

    def test_the_template_parses_but_is_never_loaded(self):
        template = yaml.safe_load((REPO_ROOT / "clients" / "_template" / "client.yaml").read_text())
        assert template["enabled"] is False and template["mode"] == "dry_run"
