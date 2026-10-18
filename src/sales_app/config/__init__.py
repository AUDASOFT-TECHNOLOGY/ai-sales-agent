"""Client configuration: schema, secrets, resolution and loading. Core objects are built elsewhere."""

from sales_app.config.llm_registry import EMPTY_LLM_REGISTRY, LLMRegistry, build_llm_registry
from sales_app.config.loader import (
    ClientConfig,
    LoadedConfig,
    Resolution,
    clamp_mode,
    load_client_configs,
    resolve_client,
    resolve_client_config,
)
from sales_app.config.resolution import (
    SettingsCheck,
    extract_document,
    import_document,
    read_wiring,
    validate_settings_document,
)
from sales_app.config.schema import ClientFile
from sales_app.config.secrets import Secrets

__all__ = [
    "EMPTY_LLM_REGISTRY",
    "ClientConfig",
    "ClientFile",
    "LLMRegistry",
    "LoadedConfig",
    "Resolution",
    "Secrets",
    "SettingsCheck",
    "build_llm_registry",
    "clamp_mode",
    "extract_document",
    "import_document",
    "load_client_configs",
    "read_wiring",
    "resolve_client",
    "resolve_client_config",
    "validate_settings_document",
]
