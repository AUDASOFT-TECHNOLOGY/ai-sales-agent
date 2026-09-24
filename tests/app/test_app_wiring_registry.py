"""Brain loading, per-client runtimes, the registry, and the composition root."""

import sys
from datetime import timedelta

import app_test_brains as brains
import pytest
from app_support import (
    VERIFY_TOKEN,
    InlineExecutor,
    build_test_application,
    env_for,
    fake_builders,
    make_settings,
    phone_id,
    write_client,
)
from engine_harness import inbound, prospect
from factories import NOW

from sales_app.bootstrap import build_application, describe_modes
from sales_app.config import load_client_configs
from sales_app.errors import ConfigurationError, RuntimeBuildError
from sales_app.registry import ClientRegistry
from sales_app.wiring import (
    BrainLoadError,
    build_runtime,
    default_builders,
    load_brain,
)
from sales_core.approval import ApprovalMode
from sales_core.contracts import ClientId
from sales_core.engine import InboundStatus
from sales_core.ports import Brain, LLMProvider
from sales_integrations.fakes import FakeClock, FakeLLMProvider
from sales_integrations.system import ChannelDisabledError, DisabledChannel


@pytest.fixture(autouse=True)
def _reset_created():
    brains.CREATED.clear()


class TestLoadBrain:
    def test_resolves_the_factory_and_hands_it_the_llm_and_settings(self):
        llm, settings = FakeLLMProvider(), {"tone": "warm"}
        brain = load_brain("app_test_brains:good_brain", llm, settings)
        assert isinstance(brain, Brain)
        assert brains.CREATED == [(llm, settings)]

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("no_such_module_xyz:good_brain", "cannot import"),
            ("app_test_brains:missing", "no callable"),
            ("app_test_brains:NOT_CALLABLE", "no callable"),
            ("app_test_brains:raising_brain", "failed while building"),
            ("app_test_brains:not_a_brain", "has no decide"),
        ],
    )
    def test_every_way_to_get_it_wrong_is_a_clear_error(self, path, message):
        with pytest.raises(BrainLoadError, match=message):
            load_brain(path, FakeLLMProvider(), {})


def _config(tmp_path, cid="alpha", *, ceiling=ApprovalMode.AUTONOMOUS, **overrides):
    write_client(tmp_path, cid, **overrides)
    loaded = load_client_configs(tmp_path, env=env_for(cid), mode_ceiling=ceiling)
    assert loaded.errors == ()
    return loaded.clients[0]


class TestBuildRuntime:
    def test_engine_is_built_from_the_clients_own_config(self, tmp_path):
        config = _config(tmp_path, daily_action_limit=2, brain={"settings": {"tone": "warm"}})
        builders, made = fake_builders()
        runtime = build_runtime(config, builders, FakeClock(NOW))
        repo = made.repositories["alpha"]
        repo.add_prospects(*(prospect(f"p{i}", client_id=ClientId("alpha")) for i in range(1, 6)))
        runtime.engine.prepare()
        assert len(repo.list_actions()) == 2  # daily_action_limit came from the file
        assert (
            "[warm] hello" in repo.list_actions()[0].draft.text
        )  # brain settings reached the Brain
        assert isinstance(brains.CREATED[0][0], LLMProvider)

    def test_the_clients_inbound_policy_reaches_the_engine(self, tmp_path):
        engage = {
            "spontaneous_conversations": {"enabled": True},
            "unknown_contacts": {"policy": "engage"},
        }
        for overrides, created in (({}, False), ({"inbound": engage}, True)):
            config = _config(tmp_path, **overrides)
            builders, made = fake_builders()
            runtime = build_runtime(config, builders, FakeClock(NOW))
            receipt = runtime.engine.receive_inbound(inbound(sender="+19998887777"))
            assert (receipt.status is InboundStatus.STORED) is created
            assert bool(made.repositories["alpha"].list_prospects()) is created

    def test_live_modes_get_the_real_channel(self, tmp_path):
        config = _config(tmp_path)
        builders, made = fake_builders()
        runtime = build_runtime(config, builders, FakeClock(NOW))
        assert "alpha" in made.channels
        assert runtime.config.effective_mode is ApprovalMode.APPROVAL

    def test_a_dry_run_can_never_send_by_construction(self, tmp_path):
        config = _config(tmp_path, mode="dry_run")
        builders, made = fake_builders()
        runtime = build_runtime(config, builders, FakeClock(NOW))
        assert made.channels == {}  # the real channel was never even built
        channel = runtime.engine._channel
        assert isinstance(channel, DisabledChannel)
        with pytest.raises(ChannelDisabledError):
            channel.send(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("override", "message"),
        [
            ({"sheets": {"provider": "notion"}}, "unknown sheets provider 'notion'"),
            ({"llm": {"provider": "acme"}}, "unknown llm provider 'acme'"),
            ({"whatsapp": {"provider": "carrier-pigeon"}}, "unknown whatsapp provider"),
            ({"brain": {"factory": "app_test_brains:raising_brain"}}, "brain"),
        ],
    )
    def test_unknown_providers_and_bad_brains_are_build_errors(self, tmp_path, override, message):
        config = _config(tmp_path, **override)
        with pytest.raises(RuntimeBuildError, match=message):
            build_runtime(config, fake_builders()[0], FakeClock(NOW))

    def test_errors_name_the_client(self, tmp_path):
        config = _config(tmp_path, brain={"factory": "app_test_brains:not_a_brain"})
        with pytest.raises(RuntimeBuildError, match="^alpha: brain:"):
            build_runtime(config, fake_builders()[0], FakeClock(NOW))


class TestRegistry:
    def _registry(self, tmp_path, *ids, **overrides):
        for cid in ids:
            write_client(tmp_path, cid, **overrides)
        loaded = load_client_configs(
            tmp_path, env=env_for(*ids), mode_ceiling=ApprovalMode.AUTONOMOUS
        )
        builders, made = fake_builders()
        return ClientRegistry.build(loaded, builders, FakeClock(NOW)), made

    def test_one_isolated_runtime_per_client(self, tmp_path):
        registry, made = self._registry(tmp_path, "alpha", "beta")
        alpha, beta = registry.get("alpha"), registry.get("beta")
        assert alpha is not beta and alpha.engine is not beta.engine
        assert made.repositories["alpha"] is not made.repositories["beta"]
        assert made.channels["alpha"] is not made.channels["beta"]
        assert made.llms["alpha"] is not made.llms["beta"]
        assert [s["tone"] for _, s in brains.CREATED] == ["alpha", "beta"]  # each Brain got its own
        assert made.repositories["alpha"].client_id == "alpha"

    def test_lookup_by_client_id_and_by_inbound_routing_key(self, tmp_path):
        registry, _ = self._registry(tmp_path, "alpha", "beta")
        assert registry.get("alpha").client_id == "alpha"
        assert registry.get("nobody") is None
        assert registry.by_routing_key("whatsapp", phone_id("beta")).client_id == "beta"
        assert registry.by_routing_key("whatsapp", "unknown") is None
        assert registry.by_routing_key("sms", phone_id("beta")) is None

    def test_clients_are_staggered_to_spare_api_quotas(self, tmp_path):
        registry, _ = self._registry(tmp_path, "alpha", "beta", "gamma")
        assert [r.stagger for r in registry.runtimes()] == [
            timedelta(0),
            timedelta(seconds=5),
            timedelta(seconds=10),
        ]

    def test_health_lists_modes_and_disabled_clients_without_secrets(self, tmp_path):
        write_client(tmp_path, "gamma", raw="enabled: false\n")
        registry, _ = self._registry(tmp_path, "alpha", mode="autonomous")
        loaded = load_client_configs(
            tmp_path, env=env_for("alpha"), mode_ceiling=ApprovalMode.APPROVAL
        )
        registry = ClientRegistry.build(loaded, fake_builders()[0], FakeClock(NOW))
        entries = {e["client_id"]: e for e in registry.health()}
        assert entries["alpha"]["mode"] == "approval"
        assert entries["alpha"]["configured_mode"] == "autonomous"
        assert entries["alpha"]["mode_clamped"] is True
        assert entries["alpha"]["status"] == "ready"
        assert entries["gamma"] == {"client_id": "gamma", "status": "disabled"}
        assert "token-alpha" not in repr(registry.health())

    def test_every_failing_client_is_reported_and_nothing_partial_is_built(self, tmp_path):
        write_client(tmp_path, "alpha", brain={"factory": "app_test_brains:raising_brain"})
        write_client(tmp_path, "beta", sheets={"provider": "notion"})
        write_client(tmp_path, "gamma")
        loaded = load_client_configs(
            tmp_path, env=env_for("alpha", "beta", "gamma"), mode_ceiling=ApprovalMode.APPROVAL
        )
        with pytest.raises(ConfigurationError) as raised:
            ClientRegistry.build(loaded, fake_builders()[0], FakeClock(NOW))
        text = "\n".join(raised.value.errors)
        assert "alpha: brain" in text and "beta: unknown sheets provider" in text
        assert "gamma" not in text
        assert len(raised.value.errors) == 2

    def test_a_client_failing_to_build_leaves_no_trace_in_another_clients_runtime(self, tmp_path):
        write_client(tmp_path, "alpha", brain={"factory": "app_test_brains:raising_brain"})
        write_client(tmp_path, "beta")
        loaded = load_client_configs(
            tmp_path, env=env_for("alpha", "beta"), mode_ceiling=ApprovalMode.APPROVAL
        )
        builders, made = fake_builders()
        with pytest.raises(ConfigurationError):
            ClientRegistry.build(loaded, builders, FakeClock(NOW))
        # beta was built independently; alpha's failure did not touch its objects
        assert made.repositories["beta"].client_id == "beta"


class TestBootstrap:
    def test_builds_everything_from_the_files(self, tmp_path):
        application, _made, _ = build_test_application(tmp_path, "alpha", "beta")
        assert [r.client_id for r in application.registry.runtimes()] == ["alpha", "beta"]
        assert set(application.parsers) == {"whatsapp"}
        assert (
            application.parsers["whatsapp"].handshake(
                {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "42"}
            )
            == "42"
        )

    def test_describe_modes_reports_clamping(self, tmp_path):
        application, _, _ = build_test_application(
            tmp_path, "alpha", mode="autonomous", ceiling=ApprovalMode.APPROVAL
        )
        (entry,) = describe_modes(application)
        assert entry == {
            "client_id": "alpha",
            "mode": "approval",
            "configured_mode": "autonomous",
            "mode_clamped": True,
        }

    def test_an_explicit_ceiling_can_only_make_things_safer(self, tmp_path):
        write_client(tmp_path, "alpha", mode="approval")
        builders, _ = fake_builders()
        settings = make_settings(tmp_path)  # deployment ceiling: approval (the default)
        lower = build_application(
            settings, env=env_for("alpha"), builders=builders, mode_ceiling=ApprovalMode.DRY_RUN
        )
        assert describe_modes(lower)[0]["mode"] == "dry_run"
        higher = build_application(
            settings, env=env_for("alpha"), builders=builders, mode_ceiling=ApprovalMode.AUTONOMOUS
        )
        assert describe_modes(higher)[0]["mode"] == "approval"  # cannot exceed the deployment

    def test_autonomy_requires_the_deployment_ceiling(self, tmp_path):
        write_client(tmp_path, "alpha", mode="autonomous")
        builders, _ = fake_builders()
        default = build_application(
            make_settings(tmp_path), env=env_for("alpha"), builders=builders
        )
        assert describe_modes(default)[0]["mode"] == "approval"
        allowed = build_application(
            make_settings(tmp_path, mode_ceiling=ApprovalMode.AUTONOMOUS),
            env=env_for("alpha"),
            builders=builders,
        )
        assert describe_modes(allowed)[0]["mode"] == "autonomous"

    def test_startup_is_strict_any_invalid_enabled_client_prevents_it(self, tmp_path):
        write_client(tmp_path, "alpha")
        write_client(tmp_path, "beta", timezone="Mars/Olympus")
        with pytest.raises(ConfigurationError) as raised:
            build_application(
                make_settings(tmp_path), env=env_for("alpha", "beta"), builders=fake_builders()[0]
            )
        assert all(e.startswith("beta:") for e in raised.value.errors)

    def test_config_and_wiring_errors_are_reported_together(self, tmp_path):
        write_client(tmp_path, "alpha", timezone="Mars/Olympus")
        write_client(tmp_path, "beta", brain={"factory": "app_test_brains:raising_brain"})
        with pytest.raises(ConfigurationError) as raised:
            build_application(
                make_settings(tmp_path), env=env_for("alpha", "beta"), builders=fake_builders()[0]
            )
        text = "\n".join(raised.value.errors)
        assert "alpha:" in text and "beta: brain" in text

    def test_disabled_clients_do_not_prevent_startup(self, tmp_path):
        write_client(tmp_path, "alpha")
        write_client(tmp_path, "beta", raw="enabled: false\nnonsense: true\n")
        application = build_application(
            make_settings(tmp_path), env=env_for("alpha"), builders=fake_builders()[0]
        )
        assert [r.client_id for r in application.registry.runtimes()] == ["alpha"]

    def test_no_enabled_clients_is_an_error_for_serving_but_not_for_validating(self, tmp_path):
        write_client(tmp_path, "beta", raw="enabled: false\n")
        with pytest.raises(ConfigurationError, match="no enabled clients"):
            build_application(make_settings(tmp_path), env={}, builders=fake_builders()[0])
        application = build_application(
            make_settings(tmp_path), env={}, builders=fake_builders()[0], require_clients=False
        )
        assert application.registry.runtimes() == []

    def test_the_real_integration_table_reports_unreadable_sheets_credentials(self, tmp_path):
        write_client(tmp_path, "alpha")
        settings = make_settings(tmp_path)
        with pytest.raises(ConfigurationError, match=r"alpha: repository:.*creds\.json"):
            build_application(settings, env=env_for("alpha"), builders=default_builders(settings))

    def test_the_default_integration_table_knows_the_agreed_providers(self, tmp_path):
        table = default_builders(make_settings(tmp_path))
        assert set(table.repositories) == {"google_sheets"}
        assert set(table.llms) == {"openai"}
        assert set(table.channels) == {"whatsapp"}

    def test_close_waits_for_the_scheduler(self, tmp_path):
        application, _, _ = build_test_application(tmp_path, "alpha")
        application.close()  # injected executors are not owned: closing must not fail
        assert isinstance(application.scheduler._executor, InlineExecutor)

    def test_brain_packages_are_not_imported_unless_configured(self, tmp_path):
        before = {"naga_brain", "tounesimmo_brain"} & set(sys.modules)
        build_test_application(tmp_path, "alpha")
        assert {"naga_brain", "tounesimmo_brain"} & set(sys.modules) == before
