import sys
from pathlib import Path

# Reuse the generic builders from tests/unit (factories, engine_harness) and tests/kit (settings).
for name in ("unit", "kit"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / name))
