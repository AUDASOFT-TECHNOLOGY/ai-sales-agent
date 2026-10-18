"""The generic consultative sales engine, as a Brain every client can name by key.

`consultative` is the platform's engine: no client owns it, and a client's products, languages,
tone, payment and process are all *settings* validated by `ConsultativeSalesSpec`. A client record
names it (`engine: consultative`) instead of a package written for that client. It is declared in the
`revansa.brains` entry-point group of the root `pyproject.toml`, like any other Brain.
"""

from sales_brain_kit.consultative import create_brain
from sales_brain_kit.descriptor import BrainDescriptor
from sales_brain_kit.spec import ConsultativeSalesSpec

BRAIN = BrainDescriptor(
    key="consultative", factory=create_brain, settings_spec=ConsultativeSalesSpec()
)
