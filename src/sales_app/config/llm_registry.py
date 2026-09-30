"""Platform-level allow-list: which `llm.provider` + `llm.model` combinations a client's
configuration may use.

Set by the deployment (`Settings.allowed_llm_models`), never by a client -- the same "category 1"
guardrail as `mode_ceiling` (see docs/client-configuration-design.md section 3), and enforced the
same way: in `resolve_client_config`, never inside `ClientFile`, which stays deployment-agnostic
and knows nothing of `Settings`.

This only decides whether a provider/model *may be requested*. Whether that provider is actually
implemented is a separate, already-existing concern (`sales_app.wiring.integrations`, checked at
runtime-build time with a clear `RuntimeBuildError`); the two are not merged here, so this module
never imports `sales_app.wiring`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMRegistry:
    """`allowed` maps a provider name to the model ids it may use.

    Empty (the default): no deployment allow-list has been configured, so every provider/model is
    accepted -- today's deployments and tests keep working unchanged until an operator opts in by
    setting `SALES_AGENT_ALLOWED_LLM_MODELS`.
    """

    allowed: Mapping[str, frozenset[str]]

    def check(self, provider: str, model: str) -> str | None:
        """None if `provider`/`model` is allowed; otherwise an explicit reason it is not."""
        if not self.allowed:
            return None
        models = self.allowed.get(provider)
        if models is None:
            return f"llm.provider {provider!r} is not on the deployment's allowed list"
        if model not in models:
            return f"llm.model {model!r} is not allowed for provider {provider!r}"
        return None


EMPTY_LLM_REGISTRY = LLMRegistry({})


def build_llm_registry(raw: Mapping[str, Sequence[str]]) -> LLMRegistry:
    """From `Settings.allowed_llm_models` (provider -> its allowed model ids)."""
    return LLMRegistry({provider: frozenset(models) for provider, models in raw.items()})
