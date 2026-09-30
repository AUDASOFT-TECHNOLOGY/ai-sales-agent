"""Admin API: read-only platform LLM configuration.

Not a second source of truth: `application.llm_providers` (which providers are actually wired,
see `sales_app.bootstrap.Application`) and the deployment's `LLMRegistry` (which provider/model
pairs a client's configuration may request, built from `Settings.allowed_llm_models`, see
`sales_app.config.llm_registry`) are both read straight off `Application` -- nothing here is
re-derived or re-validated.

No endpoint here can return a secret: `llm_providers` only ever holds provider *names* (see the
comment on that field), and `LLMRegistry` only ever holds provider/model id strings -- neither
ever carries an API key or other secret value.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sales_app.admin_auth import AdminUser
from sales_app.api.admin import make_current_admin_dependency
from sales_app.bootstrap import Application
from sales_app.config import build_llm_registry


class LLMProviderResponse(BaseModel):
    provider: str
    wired: bool  # actually implemented in this deployment (application.llm_providers)
    # None: no allow-list is configured, so any model is accepted for this provider. A list: only
    # these model ids are allowed (an empty list means this provider is wired but not allow-listed,
    # so no client may request it while the allow-list is active). See LLMRegistry.check.
    allowed_models: list[str] | None = None


class LLMSettingsResponse(BaseModel):
    read_only: bool = True
    restricted: bool  # whether the deployment has configured an allow-list at all
    providers: list[LLMProviderResponse]


def create_admin_settings_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get("/settings/llm", response_model=LLMSettingsResponse)
    def get_llm_settings(_admin: AdminUser = Depends(get_current_admin)) -> LLMSettingsResponse:
        registry = build_llm_registry(application.settings.allowed_llm_models)
        restricted = bool(registry.allowed)
        names = sorted(set(application.llm_providers) | set(registry.allowed))
        providers = [
            LLMProviderResponse(
                provider=name,
                wired=name in application.llm_providers,
                allowed_models=(
                    sorted(registry.allowed[name])
                    if name in registry.allowed
                    else ([] if restricted else None)
                ),
            )
            for name in names
        ]
        return LLMSettingsResponse(restricted=restricted, providers=providers)

    return router
