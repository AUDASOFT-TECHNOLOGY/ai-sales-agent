"""The platform-level `llm.provider` / `llm.model` allow-list (sales_app.config.llm_registry)."""

from sales_app.config import EMPTY_LLM_REGISTRY, LLMRegistry, build_llm_registry


class TestUnconfiguredRegistry:
    def test_an_empty_registry_allows_anything(self):
        assert EMPTY_LLM_REGISTRY.check("openai", "whatever-model") is None
        assert EMPTY_LLM_REGISTRY.check("unknown-vendor", "anything") is None

    def test_building_from_an_empty_mapping_is_the_same_as_the_empty_registry(self):
        assert build_llm_registry({}) == EMPTY_LLM_REGISTRY


class TestConfiguredRegistry:
    def test_an_allowed_provider_and_model_passes(self):
        registry = build_llm_registry({"openai": ["gpt-4o", "gpt-4o-mini"]})
        assert registry.check("openai", "gpt-4o") is None
        assert registry.check("openai", "gpt-4o-mini") is None

    def test_an_unlisted_provider_is_refused_explicitly(self):
        registry = build_llm_registry({"openai": ["gpt-4o"]})
        reason = registry.check("meta", "muse-spark")
        assert reason is not None
        assert "meta" in reason and "not on the deployment's allowed list" in reason

    def test_a_disallowed_model_for_a_known_provider_is_refused_explicitly(self):
        registry = build_llm_registry({"openai": ["gpt-4o"]})
        reason = registry.check("openai", "gpt-3.5-turbo")
        assert reason is not None
        assert "gpt-3.5-turbo" in reason and "openai" in reason

    def test_once_configured_only_the_listed_providers_are_allowed_at_all(self):
        """Configuring the registry for one provider does not leave every other provider open."""
        registry = build_llm_registry({"openai": ["gpt-4o"]})
        assert registry.check("openai", "gpt-4o") is None
        assert registry.check("anthropic", "some-model") is not None

    def test_the_registry_is_a_plain_frozen_value(self):
        a = build_llm_registry({"openai": ["gpt-4o", "gpt-4o"]})  # duplicate collapses
        b = LLMRegistry({"openai": frozenset({"gpt-4o"})})
        assert a == b
