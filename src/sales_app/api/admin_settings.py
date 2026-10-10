"""Admin API: platform LLM configuration (read-only) and the OpenAI models Settings offers.

Not a second source of truth: `application.llm_providers` (which providers are actually wired,
see `sales_app.bootstrap.Application`) and the deployment's `LLMRegistry` (which provider/model
pairs a client's configuration may request, built from `Settings.allowed_llm_models`, see
`sales_app.config.llm_registry`) are both read straight off `Application` -- nothing here is
re-derived or re-validated.

The OpenAI model list (`/settings/openai/models`) is kept in the existing credential store (see
`sales_app.llm_models`); only an owner may change it, and not so as to take a model away from the
clients that use it: deleting a model or changing its id is refused (409, naming the clients), and
switching one off is allowed with a warning that names them (see `sales_app.model_usage`). No endpoint here can return a secret:
`llm_providers` only ever holds provider *names* (see the comment on that field), and models are
only ever name/id strings -- neither ever carries an API key or other secret value.
"""

from collections.abc import Callable
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import make_current_admin_dependency, require_role
from sales_app.bootstrap import Application
from sales_app.config import build_llm_registry
from sales_app.config.llm_registry import LLMRegistry
from sales_app.config.secrets import OPENAI_MODELS
from sales_app.llm_models import (
    MAX_NAME_LENGTH,
    OfferedModel,
    assign_keys,
    catalog_models,
    configured_models,
    foreign_model_keys,
    model_names,
    model_tests,
    save_models,
    settings_catalog,
    stored_models,
    validate_model,
)
from sales_app.model_usage import clients_using_models, impact, list_owner
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


class OpenAIModelsChangeResponse(OpenAIModelsResponse):
    """The list after a change, and who the change leaves without a usable model."""

    impacted_clients: list[str] = []  # clients using a model that was just switched off
    warning: str | None = None


class LLMProviderResponse(BaseModel):
    provider: str
    wired: bool  # actually implemented in this deployment (application.llm_providers)
    # None: no allow-list is configured, so any model is accepted for this provider. A list: only
    # these model ids are allowed (an empty list means this provider is wired but not allow-listed,
    # so no client may request it while the allow-list is active). See LLMRegistry.check.
    allowed_models: list[str] | None = None
    # The readable names Settings gives to the ids above, for the client's model picker.
    models: list[ModelNameResponse] = []


class ModelTestRecordResponse(BaseModel):
    """How the last real test of a model ended (see `POST /settings/openai/models/{id}/test`)."""

    outcome: str
    at: str


class CatalogModelResponse(BaseModel):
    key: str
    label: str
    configured: bool  # an upstream id is configured (the id itself is never shown)
    active: bool  # configured, and not switched off by the Admin
    runnable: bool  # configured and its provider is wired: only then can a client run on it
    last_test: ModelTestRecordResponse | None = None  # None: never tested


class CatalogProviderResponse(BaseModel):
    provider: str
    wired: bool
    models: list[CatalogModelResponse]


class LLMSettingsResponse(BaseModel):
    read_only: bool = True
    restricted: bool  # whether the deployment has configured an allow-list at all
    providers: list[LLMProviderResponse]
    # The models Revansa knows, by provider, from the model catalog. Separate from `providers`
    # (what a client may be given today): listing a model here does not make it selectable.
    catalog: list[CatalogProviderResponse] = []


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


def _deactivation_warning(clients: list[str]) -> str | None:
    if not clients:
        return None
    return (
        f"{len(clients)} client(s) use a model that is now switched off and will not run until it "
        f"is switched on again or they are given another model: {', '.join(clients)}"
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
        known = catalog_models(
            settings_catalog(application.provider_registry, credentials),
            application.llm_providers,
            model_tests(application.provider_registry, credentials),
        )
        catalog = [
            CatalogProviderResponse(
                provider=name,
                wired=name in application.llm_providers,
                models=[
                    CatalogModelResponse(
                        key=m.key,
                        label=m.label,
                        configured=m.configured,
                        active=m.active,
                        runnable=m.runnable,
                        last_test=(
                            ModelTestRecordResponse(outcome=m.last_test.outcome, at=m.last_test.at)
                            if m.last_test is not None
                            else None
                        ),
                    )
                    for m in views
                ],
            )
            for name, views in known.items()
        ]
        return LLMSettingsResponse(restricted=restricted, providers=providers, catalog=catalog)

    # ---- the OpenAI models Settings offers -----------------------------------------------

    def change(
        admin: AdminUser, edit: Callable[[list[OfferedModel]], list[OfferedModel]]
    ) -> OpenAIModelsChangeResponse:
        """Applies `edit` to the current list and stores the result. Every model keeps its catalog
        key (one is derived from its name the first time it is saved), whatever `edit` does.

        Refused with 409, naming the clients, when the change would take a model away from clients
        that use it (deleted, or its id changed). Switching one off is allowed and answered with
        the clients it leaves without a usable model."""
        reserved = foreign_model_keys(application.provider_registry, OPENAI_MODELS)
        before = assign_keys(list(stored_models(credentials) or []), reserved)
        after = edit(list(before))
        provider = list_owner(application.provider_registry, OPENAI_MODELS)
        used = clients_using_models(application, provider) if provider is not None else {}
        effect = impact(before, after, used)
        if effect.removed:
            raise HTTPException(
                409,
                {
                    "message": "clients use this model: assign them another model first",
                    "client_ids": effect.removed_clients,
                    "models": {model: list(clients) for model, clients in effect.removed.items()},
                },
            )
        try:
            saved = save_models(credentials, after, actor=admin.username, reserved=reserved)
        except InvalidCredentialError as error:
            raise HTTPException(422, str(error)) from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None  # says how to enable the store
        except SecretStoreError:
            raise HTTPException(500, "credential storage failed") from None
        impacted = effect.deactivated_clients
        return OpenAIModelsChangeResponse(
            models=_models_response(saved).models,
            impacted_clients=impacted,
            warning=_deactivation_warning(impacted),
        )

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

    @router.put("/settings/openai/models/{model_id}", response_model=OpenAIModelsChangeResponse)
    async def update_openai_model(
        model_id: str, body: OpenAIModelBody, admin: AdminUser = Depends(require_owner)
    ) -> OpenAIModelsChangeResponse:
        model = _checked(body)

        def edit(models: list[OfferedModel]) -> list[OfferedModel]:
            index = next((i for i, m in enumerate(models) if m.id == model_id), None)
            if index is None:
                raise HTTPException(404, "no such model")
            if model.id != model_id and any(m.id == model.id for m in models):
                raise HTTPException(409, "this model id is already in the list")
            # The key never changes. A test says something about one Model ID, so its record
            # follows the model while the id stays the same.
            models[index] = replace(
                model,
                key=models[index].key,
                last_test=models[index].last_test if model.id == model_id else None,
            )
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
