"""The consultative sales Brain: generic commercial intelligence driven by `ConsultativeSalesSettings`.

Public surface: the Brain and its factory, the error it raises, and the context/plan it builds
(useful for tests and for a future admin preview). Everything else is an implementation detail of
those.
"""

from sales_brain_kit.consultative.brain import ConsultativeSalesBrain, create_brain
from sales_brain_kit.consultative.context import CommercialContext, build_context
from sales_brain_kit.consultative.errors import BrainOutputError

__all__ = [
    "BrainOutputError",
    "CommercialContext",
    "ConsultativeSalesBrain",
    "build_context",
    "create_brain",
]
