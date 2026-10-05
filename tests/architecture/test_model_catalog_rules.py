"""Models and providers live in the application's catalog; the Core and the Brains know neither."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# Model names: Revansa's keys and the vendors' own families. (A vendor's name in a docstring, as in
# the LLM port's "OpenAI first", is a comment, not a model; `sales_core` is not edited for it.)
MODEL_NAMES = re.compile(r"gpt-|muse|\blune\b|\bsol\b|chatgpt", re.IGNORECASE)


def _sources(*roots: Path):
    for root in roots:
        yield from root.rglob("*.py")


def test_the_core_the_kit_and_the_brains_name_no_model():
    roots = [SRC / "sales_core", SRC / "sales_brain_kit", *ROOT.glob("brains/*/*_brain")]
    found = [
        f"{py.relative_to(ROOT)}: {m.group()}"
        for py in _sources(*roots)
        for m in MODEL_NAMES.finditer(py.read_text(encoding="utf-8"))
    ]
    assert not found, found


def test_the_models_are_declared_only_by_their_providers():
    declaring = {
        py.relative_to(SRC).as_posix()
        for py in (SRC / "sales_app").rglob("*.py")
        if re.search(r"gpt-lune|gpt-sol|muse-spark", py.read_text(encoding="utf-8"))
    }
    assert declaring == {
        "sales_app/wiring/providers/openai.py",
        "sales_app/wiring/providers/meta.py",
    }


def test_the_catalog_module_depends_on_nothing_of_the_application():
    text = (SRC / "sales_app" / "model_catalog.py").read_text(encoding="utf-8")
    assert not re.search(r"^(?:from|import)\s+sales_app", text, re.MULTILINE)


def test_the_runtime_mapping_names_no_provider_or_model():
    text = (SRC / "sales_app" / "config" / "runtime_config.py").read_text(encoding="utf-8")
    assert not re.search(r"gpt-|muse|\"openai\"|\"meta\"", text)
