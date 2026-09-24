"""A Brain's own settings rules, found through the `SETTINGS_SPEC` attribute of its module.

The Brain module is located from `brain.factory` with `importlib`, so `sales_app` still knows no
Brain by name. A Brain without a spec keeps opaque settings, exactly as before.
"""

import importlib
from dataclasses import dataclass

from sales_app.config.schema import ClientFile
from sales_core.ports import BrainSettingsSpec

SPEC_ATTRIBUTE = "SETTINGS_SPEC"


@dataclass(frozen=True, slots=True)
class SpecLookup:
    spec: BrainSettingsSpec | None = None  # None: this Brain declares no settings rules
    error: str | None = None
    import_failed: bool = False  # the module could not be imported (the runtime build reports it)


def find_settings_spec(factory_path: str) -> SpecLookup:
    module_name = factory_path.partition(":")[0]
    try:
        module = importlib.import_module(module_name)
    except Exception as error:  # noqa: BLE001 - any failure to import the Brain module
        return SpecLookup(error=f"cannot import {module_name!r}: {error}", import_failed=True)
    spec = getattr(module, SPEC_ATTRIBUTE, None)
    if spec is None:
        return SpecLookup()
    if not isinstance(spec, BrainSettingsSpec):
        return SpecLookup(
            error=f"{module_name}.{SPEC_ATTRIBUTE} does not implement BrainSettingsSpec"
        )
    return SpecLookup(spec=spec)


def prepare_brain_settings(
    client: ClientFile, *, require_module: bool
) -> tuple[ClientFile, tuple[str, ...]]:
    """Validate the Brain's settings with its spec and return the client with the normalised
    latest version. `require_module`: an unimportable Brain module is a problem (publishing), not
    something to skip (loading, where the runtime build reports it with more context)."""
    lookup = find_settings_spec(client.brain.factory)
    if lookup.import_failed and not require_module:
        return client, ()
    if lookup.error is not None:
        return client, (f"brain: {lookup.error}",)
    spec = lookup.spec
    if spec is None:
        return client, ()
    settings, version = client.brain.settings, client.brain.settings_version
    try:
        problems = list(spec.validate(settings, version))
        prepared = dict(spec.prepare(settings, version)) if not problems else {}
    except Exception as error:  # noqa: BLE001 - a spec must not take the application down
        return client, (f"brain.settings: the {spec.family} spec failed: {error}",)
    if problems:
        return client, tuple(f"brain.settings.{problem}" for problem in problems)
    brain = client.brain.model_copy(
        update={"settings": prepared, "settings_version": spec.schema_version}
    )
    return client.model_copy(update={"brain": brain}), ()
