import sys
from pathlib import Path

# Reuse the generic builders: contracts (tests/unit), the Engine harness (tests/unit) and the
# scripted-model helpers of the kit (tests/kit) and the application builders (tests/app).
for name in ("unit", "kit", "app"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / name))
sys.path.insert(0, str(Path(__file__).resolve().parent))
