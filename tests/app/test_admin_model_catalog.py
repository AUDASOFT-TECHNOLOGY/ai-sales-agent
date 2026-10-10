"""The model catalog in Admin Settings: `/admin/v1/settings/llm` lists the models Revansa knows, by
provider, in a `catalog` section of its own. The Model IDs come from one place only -- the models
the Admin configures in Settings -- and a model listed there never becomes runnable by being listed.
`providers` (what a client may be given) is unchanged."""

import json
import re
from pathlib import Path

from app_support import InlineExecutor, env_for, fake_builders
from test_admin_integrations_api import Fixture, user

from sales_app.admin_auth import AdminRole
from sales_app.bootstrap import build_application
from sales_app.config.offered_models import OfferedModel, assign_keys, derive_key
from sales_app.llm_models import catalog_models, settings_catalog
from sales_app.model_catalog import ModelCatalog, ModelDescriptor
from sales_app.secret_store import GLOBAL_SCOPE
from sales_integrations.fakes import FakeClock

URL = "/admin/v1/settings/llm"
MODELS = "/admin/v1/settings/openai/models"
SRC = Path(__file__).resolve().parents[2] / "src" / "sales_app"


def settings_of(tmp_path):
    fixture = Fixture(tmp_path, clients=("alpha",))
    fixture.users.create(user("root", AdminRole.OWNER))
    fixture.login("root")
    return fixture


def application_with(fixture, **settings):
    """Another application over the same directories, with these deployment settings."""
    builders, _ = fake_builders(FakeClock(fixture.application.clock.now()))
    return build_application(
        fixture.settings.model_copy(update=settings),
        env=env_for("alpha"),
        builders=builders,
        clock=fixture.application.clock,
        executor=InlineExecutor(),
        refresh_executor=InlineExecutor(),
    )


def add(fixture, name, model_id, active=True):
    response = fixture.client.post(MODELS, json={"name": name, "id": model_id, "active": active})
    assert response.status_code == 200, response.text
    return response


def catalog_of(fixture):
    return {p["provider"]: p for p in fixture.client.get(URL).json()["catalog"]}


def models_of(fixture, provider):
    return {m["key"]: m for m in catalog_of(fixture)[provider]["models"]}


def stored_list(fixture):
    return json.loads(fixture.application.credentials.reveal(GLOBAL_SCOPE, "openai.models"))


class TestCatalogSection:
    def test_the_declared_models_are_listed_but_not_configured(self, tmp_path):
        fixture = settings_of(tmp_path)
        catalog = catalog_of(fixture)
        assert {k: [m["key"] for m in v["models"]] for k, v in catalog.items()} == {
            "meta": ["muse-spark"],
            "openai": ["gpt-lune", "gpt-sol"],
        }
        models = [m for p in catalog.values() for m in p["models"]]
        assert all(not m["configured"] and not m["active"] and not m["runnable"] for m in models)

    def test_providers_is_unchanged_and_meta_is_not_in_it(self, tmp_path):
        body = settings_of(tmp_path).client.get(URL).json()
        assert body["providers"] == [
            {"provider": "openai", "wired": True, "allowed_models": None, "models": []}
        ]


class TestTheModelIdsComeFromSettings:
    def test_a_declared_model_takes_the_id_the_admin_enters(self, tmp_path):
        fixture = settings_of(tmp_path)
        add(fixture, "GPT Sol", "the-real-sol-id")
        sol = models_of(fixture, "openai")["gpt-sol"]
        assert sol == {
            "key": "gpt-sol",
            "label": "GPT Sol",
            "configured": True,
            "active": True,
            "runnable": True,
            "last_test": None,
        }
        assert models_of(fixture, "openai")["gpt-lune"]["configured"] is False
        catalog = settings_catalog(
            fixture.application.provider_registry, fixture.application.credentials
        )
        assert catalog.runnable("gpt-sol").upstream_id == "the-real-sol-id"

    def test_a_model_no_code_has_ever_heard_of_is_a_model_of_its_own(self, tmp_path):
        fixture = settings_of(tmp_path)
        add(fixture, "Brand New 7", "bn-7.2:beta")
        added = models_of(fixture, "openai")["brand-new-7"]
        assert added["configured"] and added["runnable"] and added["label"] == "Brand New 7"

    def test_a_deactivated_model_is_configured_but_not_runnable(self, tmp_path):
        fixture = settings_of(tmp_path)
        add(fixture, "GPT Sol", "sol-id", active=False)
        sol = models_of(fixture, "openai")["gpt-sol"]
        assert (sol["configured"], sol["active"], sol["runnable"]) == (True, False, False)

    def test_the_legacy_ids_stand_in_until_the_list_is_saved(self, tmp_path):
        fixture = settings_of(tmp_path)
        fixture.application.credentials.save(
            GLOBAL_SCOPE, "openai.model_sol", "legacy-sol", actor="root"
        )
        assert models_of(fixture, "openai")["gpt-sol"]["configured"] is True

    def test_the_deployments_upstream_ids_setting_is_ignored_with_a_warning(self, tmp_path, caplog):
        fixture = settings_of(tmp_path)
        with caplog.at_level("WARNING"):
            application = application_with(fixture, model_upstream_ids={"gpt-sol": "from-env"})
        assert "SALES_AGENT_MODEL_UPSTREAM_IDS is ignored" in caplog.text
        catalog = settings_catalog(application.provider_registry, application.credentials)
        assert catalog.get("gpt-sol").upstream_id is None

    def test_an_unknown_key_in_that_setting_no_longer_stops_startup(self, tmp_path):
        application = application_with(
            settings_of(tmp_path), model_upstream_ids={"no-such-model": "x"}
        )
        assert application.provider_registry.get("openai") is not None

    def test_no_model_id_is_shown_in_the_catalog(self, tmp_path):
        fixture = settings_of(tmp_path)
        add(fixture, "GPT Sol", "id-that-must-not-leak")
        text = fixture.client.get(URL).text
        catalog_text = json.dumps(fixture.client.get(URL).json()["catalog"])
        assert "id-that-must-not-leak" not in catalog_text
        assert "upstream" not in text and "_env" not in text


class TestMetaStaysAnotherProviderAndNotRunnable:
    def test_muse_spark_is_not_runnable(self, tmp_path):
        meta = catalog_of(settings_of(tmp_path))["meta"]
        assert meta["wired"] is False
        (muse,) = meta["models"]
        assert muse == {
            "key": "muse-spark",
            "label": "Muse Spark",
            "configured": False,
            "active": False,
            "runnable": False,
            "last_test": None,
        }

    def test_an_openai_model_named_like_muse_spark_cannot_take_its_key(self, tmp_path):
        fixture = settings_of(tmp_path)
        add(fixture, "Muse Spark", "some-openai-compatible-id")
        assert [e["key"] for e in stored_list(fixture)] == ["muse-spark-2"]
        catalog = catalog_of(fixture)
        assert catalog["meta"]["models"][0]["configured"] is False
        assert "muse-spark-2" in {m["key"] for m in catalog["openai"]["models"]}

    def test_a_client_cannot_be_given_a_model_of_an_unwired_provider(self, tmp_path):
        fixture = settings_of(tmp_path)
        refused = fixture.client.put(
            "/admin/v1/clients/alpha/llm", json={"provider": "meta", "model": "muse-spark"}
        )
        assert refused.status_code == 400
        assert "not wired" in refused.json()["detail"]


class TestKeysAreStable:
    def test_the_key_is_stored_and_derived_from_the_name_once(self, tmp_path):
        fixture = settings_of(tmp_path)
        add(fixture, "GPT Sol", "sol-id")
        assert stored_list(fixture)[0]["key"] == "gpt-sol"

    def test_renaming_the_name_or_the_id_keeps_the_key(self, tmp_path):
        fixture = settings_of(tmp_path)
        add(fixture, "GPT Sol", "sol-id")
        renamed = fixture.client.put(
            MODELS + "/sol-id", json={"name": "Totally Different", "id": "sol-id-2", "active": True}
        )
        assert renamed.status_code == 200
        assert stored_list(fixture) == [
            {"name": "Totally Different", "id": "sol-id-2", "active": True, "key": "gpt-sol"}
        ]
        assert models_of(fixture, "openai")["gpt-sol"]["label"] == "Totally Different"

    def test_a_list_saved_before_keys_existed_gets_them_without_changing_anything_else(
        self, tmp_path
    ):
        fixture = settings_of(tmp_path)
        old = [{"name": "GPT Lune", "id": "lune-id", "active": True}]
        fixture.application.credentials.save(
            GLOBAL_SCOPE, "openai.models", json.dumps(old), actor="root"
        )
        assert models_of(fixture, "openai")["gpt-lune"]["configured"] is True
        add(fixture, "Other", "other-id")
        assert [(e["name"], e["id"], e["key"]) for e in stored_list(fixture)] == [
            ("GPT Lune", "lune-id", "gpt-lune"),
            ("Other", "other-id", "other"),
        ]

    def test_the_models_api_does_not_expose_the_key(self, tmp_path):
        fixture = settings_of(tmp_path)
        body = add(fixture, "GPT Sol", "sol-id").json()
        assert body == {"models": [{"name": "GPT Sol", "id": "sol-id", "active": True}]}


class TestKeyDerivation:
    def test_a_key_comes_from_the_name(self):
        assert derive_key("GPT Sol") == "gpt-sol"
        assert derive_key("  Fancy -- Model_3!! ") == "fancy-model-3"

    def test_a_name_with_no_usable_key_falls_back_to_the_id_then_a_default(self):
        assert derive_key("###", "my-id") == "my-id"
        assert derive_key("4o mini") == "model-4o-mini"
        assert derive_key("###", "!!!") == "model"

    def test_keys_are_unique_and_never_change(self):
        models = [
            OfferedModel("A Model", "id-1", key="a-model"),
            OfferedModel("A Model", "id-2"),
            OfferedModel("Other", "id-3", key="a-model"),  # a repeated key: the first keeps it
            OfferedModel("Taken", "id-4"),
        ]
        keys = [m.key for m in assign_keys(models, reserved={"taken"})]
        assert keys == ["a-model", "a-model-2", "other", "taken-2"]
        assert [m.key for m in assign_keys(assign_keys(models, {"taken"}), {"taken"})] == keys


class TestProjectionIsDrivenByTheCatalog:
    def test_a_provider_nobody_hardcoded_appears(self):
        catalog = ModelCatalog(
            [
                ModelDescriptor("fake-one", "fake_llm", "Fake One", upstream_id="u1"),
                ModelDescriptor("fake-two", "fake_llm", "Fake Two"),
                ModelDescriptor("fake-off", "fake_llm", "Fake Off", upstream_id="u3", active=False),
            ]
        )
        shown = catalog_models(catalog, wired={"fake_llm"})
        assert {k: [(m.key, m.runnable) for m in v] for k, v in shown.items()} == {
            "fake_llm": [("fake-off", False), ("fake-one", True), ("fake-two", False)]
        }
        assert catalog_models(catalog, wired=set())["fake_llm"][1].runnable is False

    def test_the_projection_code_names_no_provider_or_model(self):
        text = (SRC / "llm_models.py").read_text(encoding="utf-8")
        body = text.split("def settings_catalog")[1].split("def check_model")[0]
        assert not re.search(r"gpt-|muse|\"meta\"|\"openai\"", body)
        assert not re.search(
            r"gpt-|muse|\"meta\"", (SRC / "api" / "admin_settings.py").read_text("utf-8")
        )
