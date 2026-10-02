"""A client whose OpenAI model is not active in Admin Settings is not ready, and so never runs.

The rule only applies once Settings holds a list: a deployment that never saved one keeps working
as before. The model id comes from the client's configuration -- nothing here is built in.
"""

import json

from app_support import env_for, write_client

from sales_app.config import load_client_configs
from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore
from sales_core.approval import ApprovalMode

MODEL = "test-model"  # what app_support's clients ask for


def listed(*entries):
    return json.dumps([{"name": n, "id": i, "active": a} for n, i, a in entries])


def load(tmp_path, store):
    write_client(tmp_path, "alpha")
    loaded = load_client_configs(
        tmp_path, env=env_for("alpha"), mode_ceiling=ApprovalMode.AUTONOMOUS, secret_store=store
    )
    assert not loaded.errors
    (config,) = loaded.clients
    return config


def store_with(models=None, **other):
    store = InMemorySecretStore()
    if models is not None:
        store.set(GLOBAL_SCOPE, "openai.models", models, by="r")
    for key, value in other.items():
        store.set(GLOBAL_SCOPE, key.replace("__", "."), value, by="r")
    return store


class TestResolution:
    def test_an_active_model_is_ready(self, tmp_path):
        config = load(tmp_path, store_with(listed(("M", MODEL, True))))
        assert config.ready and config.missing_credentials == ()

    def test_a_deactivated_model_is_not_ready(self, tmp_path):
        config = load(tmp_path, store_with(listed(("M", MODEL, False), ("N", "other", True))))
        assert not config.ready
        assert config.missing_credentials == (f"LLM model '{MODEL}' is not active in Settings",)

    def test_a_model_missing_from_the_list_is_not_ready(self, tmp_path):
        config = load(tmp_path, store_with(listed(("N", "other", True))))
        assert not config.ready and "not active in Settings" in config.missing_credentials[0]

    def test_deactivating_every_model_leaves_no_model_to_run(self, tmp_path):
        assert not load(tmp_path, store_with("[]")).ready

    def test_without_a_list_in_settings_nothing_is_restricted(self, tmp_path):
        assert load(tmp_path, store_with()).ready

    def test_without_a_store_nothing_is_restricted(self, tmp_path):
        write_client(tmp_path, "alpha")
        loaded = load_client_configs(
            tmp_path, env=env_for("alpha"), mode_ceiling=ApprovalMode.AUTONOMOUS
        )
        assert loaded.clients[0].ready

    def test_the_legacy_ids_stand_in_for_a_list_not_yet_saved(self, tmp_path):
        assert not load(tmp_path, store_with(openai__model_sol="another-model")).ready
        assert load(tmp_path, store_with(openai__model_sol=MODEL)).ready

    def test_the_reason_sits_beside_other_missing_credentials(self, tmp_path):
        write_client(tmp_path, "alpha")
        loaded = load_client_configs(
            tmp_path,
            env={"GOOGLE_APPLICATION_CREDENTIALS": "creds.json"},
            mode_ceiling=ApprovalMode.AUTONOMOUS,
            secret_store=store_with(listed(("N", "other", True))),
        )
        reasons = loaded.clients[0].missing_credentials
        assert any("not active in Settings" in r for r in reasons)
        assert any("OpenAI API key" in r for r in reasons)
