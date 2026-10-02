"""`_openai(config)` builds the adapter from the client's configuration and the credential store,
offline; and no OpenAI model id or key is written into the source tree."""

import re
import socket
from pathlib import Path
from typing import ClassVar

import openai
import pytest
from app_support import env_for, write_client

from sales_app.config import load_client_configs
from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore
from sales_app.wiring.integrations import _openai
from sales_core.approval import ApprovalMode
from sales_core.ports import LLMProvider
from sales_integrations.openai import OpenAIProvider

ROOT = Path(__file__).resolve().parents[2]


class RecordingSDK:
    """Stands in for `openai.OpenAI`: records how it was built."""

    built: ClassVar[list[dict]] = []

    def __init__(self, **kwargs):
        RecordingSDK.built.append(kwargs)


@pytest.fixture
def offline(monkeypatch):
    RecordingSDK.built = []
    monkeypatch.setattr(openai, "OpenAI", RecordingSDK)

    def refuse(*args, **kwargs):
        raise AssertionError("the network was used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    return RecordingSDK


def config_for(tmp_path, store, *, model="some-model-id", env=None):
    write_client(tmp_path, "alpha", llm={"model": model})
    loaded = load_client_configs(
        tmp_path,
        env=env_for("alpha") if env is None else env,
        mode_ceiling=ApprovalMode.DRY_RUN,
        secret_store=store,
    )
    assert not loaded.errors
    return loaded.clients[0]


def stored(**values):
    store = InMemorySecretStore()
    for key, value in values.items():
        store.set(GLOBAL_SCOPE, key.replace("__", "."), value, by="r")
    return store


def test_the_key_and_model_are_passed_on_and_the_optional_settings_are_left_out(tmp_path, offline):
    config = config_for(tmp_path, stored(openai__api_key="key-from-store"), model="m-from-config")
    provider = _openai(config)
    assert isinstance(provider, OpenAIProvider) and isinstance(provider, LLMProvider)
    (built,) = offline.built
    assert built["api_key"] == "key-from-store"
    assert built["organization"] is None and built["project"] is None
    assert built["base_url"] is None
    assert provider._model == "m-from-config"


def test_organization_project_and_base_url_are_passed_when_configured(tmp_path, offline):
    store = stored(
        openai__api_key="key-from-store",
        openai__organization="org-1",
        openai__project="proj-1",
        openai__base_url="https://llm.example.test/v1",
    )
    _openai(config_for(tmp_path, store))
    (built,) = offline.built
    assert built["organization"] == "org-1"
    assert built["project"] == "proj-1"
    assert built["base_url"] == "https://llm.example.test/v1"


def test_the_key_comes_from_the_store_before_the_environment(tmp_path, offline):
    _openai(config_for(tmp_path, stored(openai__api_key="from-store")))
    assert offline.built[0]["api_key"] == "from-store"


def test_the_environment_is_only_the_fallback(tmp_path, offline):
    _openai(config_for(tmp_path, InMemorySecretStore(), env=env_for("alpha")))
    assert offline.built[0]["api_key"] == "sk-test"


def test_the_model_is_the_clients_own_never_a_default(tmp_path, offline):
    store = stored(openai__api_key="k")
    for model in ("first-id", "second-id"):
        assert _openai(config_for(tmp_path, store, model=model))._model == model


def test_the_default_timeout_and_retries_are_passed_to_the_sdk(tmp_path, offline):
    _openai(config_for(tmp_path, stored(openai__api_key="k")))
    assert offline.built[0]["timeout"] == 60.0 and offline.built[0]["max_retries"] == 2


def test_building_the_provider_makes_no_network_call(tmp_path, monkeypatch):
    """The real SDK client, with every socket connection forbidden."""

    def refuse(*args, **kwargs):
        raise AssertionError("the network was used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    provider = _openai(config_for(tmp_path, stored(openai__api_key="key-not-real")))
    assert isinstance(provider, OpenAIProvider)


# ---- nothing hardcoded ----------------------------------------------------------------------------

MODEL_ID = re.compile(
    r"\b(?:gpt-[\w.-]+|chatgpt-[\w.-]+|o[1-9](?:-[\w.]+)?\b|text-embedding-[\w.-]+)"
)
API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")


def shipped_files():
    """Source, client definitions and the web app's own code -- tests and dev scripts excluded."""
    for pattern in ("src/**/*.py", "brains/*/*_brain/**/*.py", "web/src/**/*.ts*"):
        for path in ROOT.glob(pattern):
            if not path.name.endswith((".test.ts", ".test.tsx")):
                yield path
    for path in (ROOT / "clients").glob("*/client.yaml"):
        yield path


def test_no_openai_model_id_is_written_into_the_shipped_code_or_client_files():
    found = [
        f"{path.relative_to(ROOT)}: {match.group()}"
        for path in shipped_files()
        for match in MODEL_ID.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not found, found


def test_no_api_key_is_written_into_the_shipped_code_or_client_files():
    found = [
        str(path.relative_to(ROOT))
        for path in shipped_files()
        if API_KEY.search(path.read_text(encoding="utf-8"))
    ]
    assert not found, found
