"""Loading a client's Brain.

A Brain named by key (`brain.agent`) is built by the registry of installed entry points and is
never imported from a path (see `sales_app.brain_registry`). `load_brain` is the legacy way, kept
for the operator's own `brain.factory`: the dotted path comes from the operator's `client.yaml`.
"""

import importlib
from collections.abc import Mapping

from sales_app.brain_registry import BrainLoadError
from sales_core.ports import Brain, LLMProvider

__all__ = ["BrainLoadError", "load_brain"]


def load_brain(factory_path: str, llm: LLMProvider, settings: Mapping[str, object]) -> Brain:
    """Resolve `package.module:callable`, call it as a `BrainFactory`, and check the result."""
    module_name, _, attribute = factory_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        raise BrainLoadError(f"cannot import {module_name!r}: {error}") from error
    factory = getattr(module, attribute, None)
    if factory is None or not callable(factory):
        raise BrainLoadError(f"{module_name!r} has no callable {attribute!r}")
    try:
        brain = factory(llm, settings)
    except Exception as error:
        raise BrainLoadError(f"{factory_path} failed while building the Brain: {error}") from error
    if not isinstance(brain, Brain):
        raise BrainLoadError(
            f"{factory_path} returned {type(brain).__name__}, which has no decide() method"
        )
    return brain
