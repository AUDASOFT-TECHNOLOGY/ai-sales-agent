"""Admin API: platform LLM configuration (read-only) and the OpenAI models Settings offers.

Not a second source of truth: `application.llm_providers` (which providers are actually wired,
see `sales_app.bootstrap.Application`) and the deployment's `LLMRegistry` (which provider/model
pairs a client's configuration may request, built from `Settings.allowed_llm_models`, see
`sales_app.config.llm_registry`) are both read straight off `Application` -- nothing here is
re-derived or re-validated.

The OpenAI model list (`/settings/openai/models`) is kept in the existing credential store (see
`sales_app.llm_models`); only an owner may change it. No endpoint here can return a secret:
`llm_providers` only ever holds provider *names* (see the comment on that field), and models are
only ever name/id strings -- neither ever carries an API key or other secret value.
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.config import build_llm_registry
from sales_app.config.llm_registry import LLMRegistry
from sales_app.llm_models import (
    MAX_NAME_LENGTH,
    OfferedModel,
    configured_models,
    model_names,
    save_models,
    stored_models,
    validate_model,
)
from sales_app.secret_store import (
    InvalidCredentialError,
    SecretStoreError,
    SecretStoreUnavailableError,
)


class ModelNameResponse(BaseModel):
    id: str
    name: str


class OpenAIModelBody(BaseModel):
    name: str = Field(max_length=MAX_NAME_LENGTH * 2)
    id: str = Field(max_length=256)
    active: bool = True


class OpenAIModelResponse(BaseModel):
    name: str
    id: str
    active: bool


class OpenAIModelsResponse(BaseModel):
    models: list[OpenAIModelResponse]


class LLMProviderResponse(BaseModel):
    provider: str
    wired: bool  # actually implemented in this deployment (application.llm_providers)
    # None: no allow-list is configured, so any model is accepted for this provider. A list: only
    # these model ids are allowed (an empty list means this provider is wired but not allow-listed,
    # so no client may request it while the allow-list is active). See LLMRegistry.check.
    allowed_models: list[str] | None = None
    # The readable names Settings gives to the ids above, for the client's model picker.
    models: list[ModelNameResponse] = []


class LLMSettingsResponse(BaseModel):
    read_only: bool = True
    restricted: bool  # whether the deployment has configured an allow-list at all
    providers: list[LLMProviderResponse]


def _allowed(
    name: str, registry: LLMRegistry, offered: dict[str, list[str]], restricted: bool
) -> list[str] | None:
    """What a client may pick for this provider: the models configured in Settings (narrowed by
    the deployment allow-list, if any); with none configured, the allow-list alone."""
    from_registry = sorted(registry.allowed[name]) if name in registry.allowed else None
    if name in offered:
        return sorted(m for m in offered[name] if registry.check(name, m) is None)
    if from_registry is not None:
        return from_registry
    return [] if restricted else None


def _models_response(models: list[OfferedModel]) -> OpenAIModelsResponse:
    return OpenAIModelsResponse(
        models=[OpenAIModelResponse(name=m.name, id=m.id, active=m.active) for m in models]
    )


def _checked(body: OpenAIModelBody) -> OfferedModel:
    try:
        return validate_model(OfferedModel(body.name, body.id, body.active))
    except InvalidCredentialError as error:
        raise HTTPException(422, str(error)) from None


def create_admin_settings_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    credentials = application.credentials
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get("/settings/llm", response_model=LLMSettingsResponse)
    def get_llm_settings(_admin: AdminUser = Depends(get_current_admin)) -> LLMSettingsResponse:
        registry = build_llm_registry(application.settings.allowed_llm_models)
        restricted = bool(registry.allowed)
        offered = configured_models(credentials)
        labels = model_names(credentials)
        providers = []
        for name in sorted(set(application.llm_providers) | set(registry.allowed) | set(offered)):
            allowed = _allowed(name, registry, offered, restricted)
            readable = labels.get(name, {})
            providers.append(
                LLMProviderResponse(
                    provider=name,
                    wired=name in application.llm_providers,
                    allowed_models=allowed,
                    models=[
                        ModelNameResponse(id=m, name=readable[m])
                        for m in allowed or []
                        if m in readable
                    ],
                )
            )
        return LLMSettingsResponse(restricted=restricted, providers=providers)

    # ---- the OpenAI models Settings offers -----------------------------------------------

    def change(
        admin: AdminUser, edit: Callable[[list[OfferedModel]], list[OfferedModel]]
    ) -> OpenAIModelsResponse:
        """Applies `edit` to the current list and stores the result."""
        models = edit(list(stored_models(credentials) or []))
        try:
            return _models_response(save_models(credentials, models, actor=admin.username))
        except InvalidCredentialError as error:
            raise HTTPException(422, str(error)) from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None  # says how to enable the store
        except SecretStoreError:
            raise HTTPException(500, "credential storage failed") from None

    @router.get("/settings/openai/models", response_model=OpenAIModelsResponse)
    def list_openai_models(_admin: AdminUser = Depends(get_current_admin)) -> OpenAIModelsResponse:
        return _models_response(stored_models(credentials) or [])

    @router.post("/settings/openai/models", response_model=OpenAIModelsResponse)
    async def add_openai_model(
        body: OpenAIModelBody, admin: AdminUser = Depends(require_owner)
    ) -> OpenAIModelsResponse:
        model = _checked(body)

        def edit(models: list[OfferedModel]) -> list[OfferedModel]:
            if any(m.id == model.id for m in models):
                raise HTTPException(409, "this model id is already in the list")
            return [*models, model]

        return await run_in_threadpool(change, admin, edit)

    @router.put("/settings/openai/models/{model_id}", response_model=OpenAIModelsResponse)
    async def update_openai_model(
        model_id: str, body: OpenAIModelBody, admin: AdminUser = Depends(require_owner)
    ) -> OpenAIModelsResponse:
        model = _checked(body)

        def edit(models: list[OfferedModel]) -> list[OfferedModel]:
            index = next((i for i, m in enumerate(models) if m.id == model_id), None)
            if index is None:
                raise HTTPException(404, "no such model")
            if model.id != model_id and any(m.id == model.id for m in models):
                raise HTTPException(409, "this model id is already in the list")
            models[index] = model
            return models

        return await run_in_threadpool(change, admin, edit)

    @router.delete("/settings/openai/models/{model_id}", response_model=OpenAIModelsResponse)
    async def delete_openai_model(
        model_id: str, admin: AdminUser = Depends(require_owner)
    ) -> OpenAIModelsResponse:
        def edit(models: list[OfferedModel]) -> list[OfferedModel]:
            if all(m.id != model_id for m in models):
                raise HTTPException(404, "no such model")
            return [m for m in models if m.id != model_id]

        return await run_in_threadpool(change, admin, edit)

    return router
