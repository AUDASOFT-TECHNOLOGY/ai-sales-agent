"""Baseline (golden) tests: the JSON the Admin API returns about clients, agents and their LLM.

Pins the full response bodies of `GET /clients`, `GET /clients/{id}`, `GET /agents`,
`GET /agents/{id}` and `PUT /clients/{id}/llm` for the legacy configuration shapes, together with
what a successful `PUT` stores. The Admin frontend depends on these shapes; a later migration of
the configuration must project the same JSON.

Four clients, written to disk before the application boots: `alpha` (every legacy field written
out), `beta` (the minimal legacy file), `delta` (valid but lacking its WhatsApp access token, so not
ready) and `pholder` (disabled, still full of placeholders, never validated).
"""

import pytest
from app_support import env_for, fake_builders, write_client
from baseline_support import AdminHarness, assert_golden, place_yaml, settings_secret_store
from factories import NOW

from sales_app.wiring import IntegrationBuilders
from sales_integrations.fakes import FakeClock


def _env():
    env = env_for("alpha", "beta", "delta")
    del env["DELTA_TOKEN"]
    return env


def _write_clients(root):
    place_yaml(root, "alpha", "legacy_complete.yaml")
    write_client(root, "beta")
    write_client(root, "delta")
    place_yaml(root, "pholder", "legacy_disabled_placeholders.yaml")


def _with_anthropic(clock: FakeClock):
    """The usual fake builders plus a second wired LLM provider, to exercise a real switch."""
    builders, _ = fake_builders(clock)
    return IntegrationBuilders(
        repositories=builders.repositories,
        llms={**builders.llms, "anthropic": builders.llms["openai"]},
        channels=builders.channels,
    )


@pytest.fixture
def harness(tmp_path):
    _write_clients(tmp_path)
    return AdminHarness(tmp_path, env=_env(), config_dir=tmp_path / "_config")


class TestReads:
    def test_clients_list(self, harness):
        assert_golden("admin_clients_list", harness.get("/clients"))

    @pytest.mark.parametrize("client_id", ["alpha", "beta", "delta", "pholder", "nope"])
    def test_client_detail(self, harness, client_id):
        assert_golden(f"admin_client_detail_{client_id}", harness.get(f"/clients/{client_id}"))

    def test_agents_list(self, harness):
        assert_golden("admin_agents_list", harness.get("/agents"))

    @pytest.mark.parametrize("agent_id", ["alpha", "beta", "delta", "pholder", "nope"])
    def test_agent_detail(self, harness, agent_id):
        assert_golden(f"admin_agent_detail_{agent_id}", harness.get(f"/agents/{agent_id}"))

    def test_a_published_settings_document_shows_in_the_client_detail(self, harness):
        """The newest published version, read before the running config catches up."""
        harness.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-2"})
        assert_golden(
            "admin_client_detail_alpha_after_llm_update",
            {
                "detail": harness.get("/clients/alpha"),
                "list": harness.get("/clients"),
                "agent": harness.get("/agents/alpha"),
            },
        )

    def test_no_response_carries_a_secret_value(self, harness):
        text = repr(
            [harness.get("/clients"), harness.get("/clients/alpha"), harness.get("/agents/alpha")]
        )
        for secret in ("sk-test", "token-alpha", "secret-alpha", "creds.json"):
            assert secret not in text


class TestUpdateLlm:
    def test_a_successful_update(self, harness):
        before = harness.current_version("alpha")
        result = harness.put(
            "/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-2"}
        )
        assert result["status"] == 200
        assert_golden(
            "admin_put_llm_success",
            {"before": before, "response": result, "stored": harness.current_version("alpha")},
        )

    def test_the_provider_and_model_are_trimmed(self, harness):
        result = harness.put(
            "/clients/alpha/llm", {"provider": " openai ", "model": " gpt-upstream-2 "}
        )
        assert_golden(
            "admin_put_llm_trimmed",
            {"response": result, "stored": harness.current_version("alpha")},
        )

    def test_a_second_update_chains_to_the_first(self, harness):
        harness.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-2"})
        second = harness.put(
            "/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-3"}
        )
        assert_golden(
            "admin_put_llm_second_update",
            {"response": second, "stored": harness.current_version("alpha")},
        )

    def test_an_update_keeps_every_other_setting(self, harness):
        harness.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-2"})
        document = harness.current_version("alpha")["document"]
        assert document["display_name"] == "Alpha Legacy" and document["daily_action_limit"] == 12
        assert document["sheets"]["actions_tab"] == "ActionsX"
        assert document["brain"]["settings"] == {"tone": "warm"}

    @pytest.mark.parametrize(
        ("name", "client_id", "body"),
        [
            ("unwired_provider", "alpha", {"provider": "anthropic", "model": "claude-x"}),
            ("empty_provider", "alpha", {"provider": "", "model": "m"}),
            ("empty_model", "alpha", {"provider": "openai", "model": ""}),
            ("unknown_client", "nope", {"provider": "openai", "model": "m"}),
            ("disabled_client", "pholder", {"provider": "openai", "model": "m"}),
            ("placeholder_model", "alpha", {"provider": "openai", "model": "TODO"}),
            ("missing_field", "alpha", {"provider": "openai"}),
        ],
    )
    def test_refusals(self, harness, name, client_id, body):
        result = harness.put(f"/clients/{client_id}/llm", body)
        assert_golden(
            f"admin_put_llm_refused_{name}",
            {"response": result, "stored": harness.current_version(client_id)},
        )

    def test_a_model_off_the_deployment_allow_list_is_refused(self, tmp_path):
        _write_clients(tmp_path)
        h = AdminHarness(
            tmp_path,
            env=_env(),
            config_dir=tmp_path / "_config",
            allowed_llm_models={"openai": ("gpt-upstream-1", "test-model")},
        )
        allowed = h.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-1"})
        refused = h.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-2"})
        assert_golden(
            "admin_put_llm_allow_list",
            {"allowed": allowed, "refused": refused, "stored": h.current_version("alpha")},
        )

    def test_a_model_not_offered_in_settings_is_refused(self, tmp_path):
        _write_clients(tmp_path)
        h = AdminHarness(
            tmp_path,
            env=_env(),
            config_dir=tmp_path / "_config",
            secret_store=settings_secret_store(
                [("GPT Lune", "gpt-upstream-1", True), ("GPT Sol", "gpt-upstream-sol", False)]
            ),
        )
        offered = h.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-1"})
        inactive = h.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-sol"})
        unlisted = h.put("/clients/alpha/llm", {"provider": "openai", "model": "gpt-upstream-9"})
        assert_golden(
            "admin_put_llm_settings_list",
            {
                "offered": offered,
                "inactive": inactive,
                "unlisted": unlisted,
                "stored": h.current_version("alpha"),
            },
        )

    def test_switching_to_another_wired_provider(self, tmp_path):
        _write_clients(tmp_path)
        clock = FakeClock(NOW)
        h = AdminHarness(
            tmp_path,
            env=_env(),
            config_dir=tmp_path / "_config",
            builders=_with_anthropic(clock),
        )
        result = h.put("/clients/alpha/llm", {"provider": "anthropic", "model": "claude-x"})
        assert_golden(
            "admin_put_llm_other_provider",
            {
                "response": result,
                "stored": h.current_version("alpha"),
                "detail": h.get("/clients/alpha"),
            },
        )

    def test_the_same_update_twice_is_two_versions(self, harness):
        first = harness.put("/clients/beta/llm", {"provider": "openai", "model": "same-model"})
        second = harness.put("/clients/beta/llm", {"provider": "openai", "model": "same-model"})
        assert_golden(
            "admin_put_llm_same_update_twice",
            {"first": first, "second": second, "stored": harness.current_version("beta")},
        )
