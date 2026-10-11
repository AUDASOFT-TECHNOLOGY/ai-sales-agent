"""Feeding the registry with the refusals a source reports.

`ProspectSource.refused_contacts()` lists the contacts of records left out of the commercial cycle
that carry a do-not-contact signal. Recording them here makes the refusal permanent: it outlives the
source's own flag, which a CRM user may clear, and it follows the person to any other record.

The contacts pass through this function and nowhere else: it logs nothing, returns only a count and
keeps nothing.
"""

from collections.abc import Iterable

from sales_app.sourcing.consent.events import Origin, ReasonCode
from sales_app.sourcing.consent.registry import ConsentWriter
from sales_core.contracts import ContactInfo

SOURCE_ACTOR = "system:source"


def record_refusals(
    writer: ConsentWriter,
    contacts: Iterable[ContactInfo],
    *,
    actor: str = SOURCE_ACTOR,
) -> int:
    """Record each contact as opted out, from a source's signal. Idempotent: contacts already
    blocked add nothing. Returns how many identities were newly blocked. Raises
    `ConsentUnavailableError` if the registry is not healthy: the caller reports that, and every
    contact stays blocked meanwhile."""
    recorded = 0
    for contact in contacts:
        recorded += writer.record_opt_out(
            ContactInfo(phone=contact.phone, email=contact.email),  # only these two, as given
            origin=Origin.SOURCE,
            reason=ReasonCode.SOURCE_SIGNAL,
            actor=actor,
        )
    return recorded
