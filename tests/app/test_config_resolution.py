"""Resolving a client's configuration from client.yaml plus the store, and what happens when it is bad."""

import pytest
import yaml
from app_support import (
    client_yaml,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)

from sales_app.bootstrap import build_application
from sales_app.config import resolve_client_config, validate_settings_document
from sales_app.config.resolution import strip_settings
from sales_app.config_store import ConfigCorruptError, FileConfigStore, InMemoryConfigStore
from sales_app.errors import ConfigurationError
from sales_core.approval import ApprovalMode

APPROVAL, AUTO = ApprovalMode.APPROVAL, ApprovalMode.AUTONOMOUS


def write_wiring_only(root, cid="alpha", **overrides):
    """A client.yaml holding only what the operator owns."""
    data = strip_settings(client_yaml(cid, **overrides))
    (root / cid).mkdir(parents=True, exist_ok=True)
    (root / cid / "client.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def stored(cid="alpha", **overrides):
    """A valid, normalized settings document for `cid`."""
    document = {
        "schema_version": 1,
        "display_name": f"{cid.title()} (stored)",
        "timezone": "Africa/Tunis",
        "sheets": {"spreadsheet_id": f"sheet-{cid}"},
        "llm": {"model": "stored-model"},
        "brain": {"settings": {"tone": "stored"}},
    }
    document.update(overrides)
    check = validate_settings_document(cid, client_yaml(cid), document)
    assert not check.errors, check.errors
    return check.document


def publish(store, cid="alpha", document=None, parent=None):
    return store.publish(cid, document or stored(cid), author="t", expected_parent=parent)


def resolve(root, cid="alpha", store=None, ceiling=AUTO, env=None):
    return resolve_client_config(
        root / cid, env=env or env_for(cid), mode_ceiling=ceiling, store=store
    )


class TestSources:
    def test_without_a_store_or_without_a_version_the_file_is_the_source(self, tmp_path):
        write_client(tmp_path, "alpha")
        for store in (None, InMemoryConfigStore()):
            config = resolve(tmp_path, store=store).config
            assert (config.config_source, config.config_version) == ("file", None)
            assert config.spec.display_name == "Alpha"

    def test_a_published_version_supplies_the_client_settings(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        publish(store)
        config = resolve(tmp_path, store=store).config
        assert (config.config_source, config.config_version) == ("store", 1)
        spec = config.spec
        assert spec.display_name == "Alpha (stored)" and spec.llm.model == "stored-model"
        assert spec.brain.settings == {"tone": "stored"} and spec.sheets.spreadsheet_id
        # the operator's wiring still comes from client.yaml
        assert spec.brain.factory == "app_test_brains:good_brain"
        assert spec.whatsapp.token_env == "ALPHA_TOKEN" and spec.mode is ApprovalMode.DRY_RUN

    def test_the_store_wins_over_settings_left_in_client_yaml(self, tmp_path):
        write_client(tmp_path, "alpha", display_name="From the file", daily_action_limit=7)
        store = InMemoryConfigStore()
        publish(store)
        spec = resolve(tmp_path, store=store).config.spec
        assert spec.display_name == "Alpha (stored)"
        assert spec.daily_action_limit == 20  # the document's default, not the file's 7

    def test_the_document_cannot_change_operator_wiring(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        # A store written by something other than `publish` still cannot reach the wiring.
        store.publish(
            "alpha",
            {**stored(), "whatsapp": {"token_env": "OTHER_CLIENT_TOKEN"}},
            author="t",
            expected_parent=None,
        )
        result = resolve(tmp_path, store=store)
        assert result.config is None
        assert any("configuration version 1: whatsapp:" in e for e in result.errors)

    def test_a_wiring_only_file_without_a_version_says_how_to_fix_it(self, tmp_path):
        write_wiring_only(tmp_path)
        result = resolve(tmp_path, store=InMemoryConfigStore())
        assert result.config is None
        assert any("display_name" in e for e in result.errors)
        assert any("config import alpha" in e for e in result.errors)

    def test_disabled_clients_never_consult_the_store(self, tmp_path):
        write_client(tmp_path, "alpha", raw="enabled: false\n")

        class Exploding(InMemoryConfigStore):
            def current(self, client_id):
                raise AssertionError("must not be called")

        result = resolve(tmp_path, store=Exploding())
        assert result.disabled and result.config is None and result.errors == ()

    def test_a_directory_with_an_invalid_client_id_is_reported_not_crashed(self, tmp_path):
        write_client(tmp_path, "Alpha")  # uppercase directory name
        result = resolve(tmp_path, cid="Alpha", store=InMemoryConfigStore())
        assert result.config is None and result.errors

    def test_the_deployment_ceiling_still_clamps_a_stored_mode(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        publish(store, document=stored(mode="autonomous"))
        config = resolve(tmp_path, store=store, ceiling=APPROVAL).config
        assert config.spec.mode is AUTO
        assert config.effective_mode is APPROVAL and config.mode_clamped

    def test_missing_credentials_leave_the_client_not_ready_not_invalid(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        publish(store)
        missing = dict(env_for("alpha"))
        del missing["OPENAI_API_KEY"]
        result = resolve(tmp_path, store=store, env=missing)
        assert result.errors == ()
        assert result.config is not None and not result.config.ready
        assert result.config.missing_credentials == ("OpenAI API key is not configured",)


class TestARunningConfigurationIsNeverDisturbed:
    def test_a_resolved_config_is_immutable_and_unaffected_by_later_versions(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        publish(store)
        running = resolve(tmp_path, store=store).config
        publish(store, document=stored(display_name="Changed", daily_action_limit=50), parent=1)

        assert (running.config_version, running.spec.display_name) == (1, "Alpha (stored)")
        assert running.spec.daily_action_limit == 20
        with pytest.raises(Exception):  # noqa: B017 - frozen dataclass / frozen model
            running.spec.daily_action_limit = 1
        latest = resolve(tmp_path, store=store).config
        assert (latest.config_version, latest.spec.daily_action_limit) == (2, 50)

    def test_an_invalid_newest_version_yields_errors_and_leaves_the_running_config_alone(
        self, tmp_path
    ):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        publish(store)
        running = resolve(tmp_path, store=store).config
        snapshot = (running.config_version, running.spec.model_dump_json())

        bad = {**stored(), "timezone": "Mars/Olympus", "daily_action_limit": 0}
        store.publish("alpha", bad, author="t", expected_parent=1)  # bypassing publish-time checks
        result = resolve(tmp_path, store=store)

        assert result.config is None
        assert any(e.startswith("alpha: timezone:") for e in result.errors)
        assert any("daily_action_limit" in e for e in result.errors)
        assert any("configuration version 2" in e for e in result.errors)
        assert (running.config_version, running.spec.model_dump_json()) == snapshot

    def test_rolling_back_restores_a_valid_resolution(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        publish(store)
        store.publish("alpha", {**stored(), "timezone": "Nope/Nope"}, author="t", expected_parent=1)
        assert resolve(tmp_path, store=store).config is None
        store.publish("alpha", dict(store.get("alpha", 1).document), author="t", expected_parent=2)
        config = resolve(tmp_path, store=store).config
        assert config.config_version == 3 and config.spec.timezone == "Africa/Tunis"

    def test_a_corrupt_store_is_reported_and_never_guessed_at(self, tmp_path):
        write_wiring_only(tmp_path)
        store = FileConfigStore(tmp_path / "config")
        publish(store)
        (tmp_path / "config" / "alpha" / "versions" / "000001.json").write_text("{", "utf-8")
        with pytest.raises(ConfigCorruptError):
            store.current("alpha")
        result = resolve(tmp_path, store=store)
        assert result.config is None
        assert any(e.startswith("alpha: configuration store:") for e in result.errors)


class TestStartupAndHealth:
    def _build(self, root, store, cid="alpha"):
        builders, made = fake_builders()
        application = build_application(
            make_settings(root),
            env=env_for(cid),
            builders=builders,
            config_store=store,
        )
        return application, made

    def test_the_application_runs_on_the_stored_version_and_reports_it(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        publish(store, document=stored(daily_action_limit=33))
        application, _ = self._build(tmp_path, store)
        runtime = application.registry.get("alpha")
        assert runtime.config.spec.daily_action_limit == 33
        (entry,) = application.registry.health()
        assert (entry["config_version"], entry["config_source"]) == (1, "store")
        assert application.config_store is store

    def test_a_file_client_reports_no_version(self, tmp_path):
        write_client(tmp_path, "alpha")
        application, _ = self._build(tmp_path, InMemoryConfigStore())
        (entry,) = application.registry.health()
        assert (entry["config_version"], entry["config_source"]) == (None, "file")

    def test_the_default_store_is_the_file_store_under_the_config_dir(self, tmp_path):
        write_client(tmp_path / "clients", "alpha")
        settings = make_settings(tmp_path / "clients", config_dir=tmp_path / "cfg")
        builders, _ = fake_builders()
        application = build_application(settings, env=env_for("alpha"), builders=builders)
        assert isinstance(application.config_store, FileConfigStore)
        assert not (tmp_path / "cfg").exists()  # starting up writes nothing

    def test_startup_fails_fast_when_the_current_version_is_invalid(self, tmp_path):
        write_wiring_only(tmp_path)
        store = InMemoryConfigStore()
        store.publish(
            "alpha", {**stored(), "timezone": "Nope/Nope"}, author="t", expected_parent=None
        )
        with pytest.raises(ConfigurationError) as raised:
            self._build(tmp_path, store)
        assert any("configuration version 1" in e for e in raised.value.errors)

    def test_one_clients_bad_version_does_not_hide_the_others_problems(self, tmp_path):
        write_wiring_only(tmp_path, "alpha")
        write_client(tmp_path, "beta", timezone="Mars/Olympus")
        store = InMemoryConfigStore()
        store.publish("alpha", {**stored(), "mode": "yolo"}, author="t", expected_parent=None)
        builders, _ = fake_builders()
        with pytest.raises(ConfigurationError) as raised:
            build_application(
                make_settings(tmp_path),
                env=env_for("alpha", "beta"),
                builders=builders,
                config_store=store,
            )
        prefixes = {e.split(":")[0] for e in raised.value.errors}
        assert prefixes == {"alpha", "beta"}
