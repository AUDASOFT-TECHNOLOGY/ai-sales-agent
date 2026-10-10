"""From Settings to the model call: the Model ID the Admin configured is the one OpenAI is asked for.

The whole chain runs for real: the credential store and the model list of Settings, the Admin routes
that assign a model, the configuration loader, the runtime refresh, the real OpenAI provider of
Revansa. Only the OpenAI SDK is replaced by a recorder, so nothing is sent and nothing is billed.
No restart happens between the two assignments: it is the same application throughout."""

from types import SimpleNamespace
from typing import ClassVar

import openai
import pytest
from test_admin_client_apply import Harness

from sales_app.llm_models import settings_catalog
from sales_app.wiring.providers import openai as openai_provider
from sales_core.contracts import LLMMessage, LLMRequest, LLMRole
from sales_integrations.openai import OpenAIProvider

M = "/admin/v1/settings/openai/models"
G = "/admin/v1/settings/integrations"
STORED_KEY = "sk-stored-in-settings-for-this-test"
X, Y = "real-id-x", "real-id-y"  # Model IDs as OpenAI knows them: not Revansa's catalog keys


class RecordingSDK:
    """Stands in for `openai.OpenAI`: records how it is built and what it is asked."""

    built: ClassVar[list[dict]] = []
    calls: ClassVar[list[dict]] = []

    def __init__(self, **kwargs):
        RecordingSDK.built.append(kwargs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _create(**arguments):
        RecordingSDK.calls.append(arguments)
        choice = SimpleNamespace(
            message=SimpleNamespace(content="a reply", refusal=None), finish_reason="stop"
        )
        usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
        return SimpleNamespace(choices=[choice], model=arguments["model"], usage=usage)


@pytest.fixture
def sdk(monkeypatch):
    RecordingSDK.built, RecordingSDK.calls = [], []
    monkeypatch.setattr(openai, "OpenAI", RecordingSDK)
    return RecordingSDK


@pytest.fixture
def h(tmp_path, sdk):
    """The real OpenAI provider builds each runtime's model client; no key in the environment."""
    harness = Harness(
        tmp_path, models=(), api_key=False, llm_factory=openai_provider.build_provider
    )
    yield harness
    harness.application.close()


def add(h, name, model_id):
    response = h.client.post(M, json={"name": name, "id": model_id, "active": True})
    assert response.status_code == 200, response.text


def llm_in_force(h):
    """The model client the client's Brain really holds: what its next decision would call."""
    return h.application.registry.get("alpha").engine._brain.llm


def ask(provider):
    request = LLMRequest(messages=(LLMMessage(role=LLMRole.USER, content="Hello"),))
    return provider.complete(request)


def models_asked(sdk):
    return [call["model"] for call in sdk.calls]


class TestFromSettingsToTheModelCall:
    def test_x_then_y_without_a_restart(self, h, sdk):
        application = h.application

        # The Admin configures the OpenAI account and declares two models, in Settings only.
        assert h.client.put(f"{G}/openai.api_key", json={"value": STORED_KEY}).status_code == 200
        add(h, "Model X", X)
        add(h, "Model Y", Y)
        assert sdk.calls == []  # configuring anything calls no model

        # Assigns X to the client: the runtime is rebuilt with exactly that Model ID.
        assert h.assign(X).status_code == 200
        h.wait_for("applied")
        first = llm_in_force(h)
        assert isinstance(first, OpenAIProvider)
        assert ask(first).text == "a reply"
        assert models_asked(sdk) == [X]
        assert sdk.built[-1]["api_key"] == STORED_KEY  # the key of Settings, not the environment

        # Replaces X by Y: the very next call uses Y. Same application, nothing restarted.
        assert h.assign(Y).status_code == 200
        h.wait_for("applied")
        assert h.application is application
        second = llm_in_force(h)
        assert second is not first
        ask(second)
        assert models_asked(sdk) == [X, Y]
        assert h.running_config().llm.model == Y
        assert h.detail()["llm_model"] == Y

    def test_the_model_id_sent_is_the_admins_not_the_catalogs_key(self, h, sdk):
        add(h, "Model X", X)
        catalog = settings_catalog(h.application.provider_registry, h.application.credentials)
        assert catalog.get("model-x").upstream_id == X  # the key Revansa knows it by
        h.client.put(f"{G}/openai.api_key", json={"value": STORED_KEY})
        h.assign(X)
        h.wait_for("applied")
        ask(llm_in_force(h))
        assert models_asked(sdk) == [X]
        assert "model-x" not in models_asked(sdk)

    def test_a_model_that_is_not_in_settings_is_never_called(self, h, sdk):
        h.client.put(f"{G}/openai.api_key", json={"value": STORED_KEY})
        add(h, "Model X", X)
        h.assign(X)
        h.wait_for("applied")
        refused = h.assign("never-declared")
        assert refused.status_code == 400
        assert "not configured in Settings" in refused.json()["detail"]
        ask(llm_in_force(h))
        assert models_asked(sdk) == [X]  # still the model that was assigned

    def test_a_model_added_later_needs_no_change_to_the_code(self, h, sdk):
        # The third Model ID is one no source file has ever heard of.
        h.client.put(f"{G}/openai.api_key", json={"value": STORED_KEY})
        for name, model_id in (("Model X", X), ("Model Y", Y), ("Brand New", "ft:brand-new.9:org")):
            add(h, name, model_id)
        h.assign("ft:brand-new.9:org")
        h.wait_for("applied")
        ask(llm_in_force(h))
        assert models_asked(sdk) == ["ft:brand-new.9:org"]
