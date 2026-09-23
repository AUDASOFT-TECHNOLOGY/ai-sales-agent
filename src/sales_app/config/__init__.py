"""Client configuration: schema, secrets, resolution and loading. Core objects are built elsewhere."""

from sales_app.config.loader import (
    ClientConfig,
    LoadedConfig,
    Resolution,
    clamp_mode,
    load_client_configs,
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
    "ClientConfig",
    "ClientFile",
    "LoadedConfig",
    "Resolution",
    "Secrets",
    "SettingsCheck",
    "clamp_mode",
    "extract_document",
    "import_document",
    "load_client_configs",
    "read_wiring",
    "resolve_client_config",
    "validate_settings_document",
]
