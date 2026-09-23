"""Loading a client's Brain from the entry point named in its configuration.

The app never imports a Brain package by name: the dotted path comes from `client.yaml`, so
nothing in `sales_app` knows which clients or Brains exist.
"""

import importlib
from collections.abc import Mapping

from sales_core.ports import Brain, LLMProvider


class BrainLoadError(Exception):
    """The configured Brain cannot be imported, built, or does not implement the Brain port."""


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
