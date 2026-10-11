"""Putting one prospect back together from the source's record and Revansa's own state.

Pure: no I/O, no provider. A source says who the person is; the operational store remembers what
Revansa did and decided. The Core wants one `Prospect`, so this is where the two are joined, field
by field, according to what the source declares it owns.
"""

from collections.abc import Collection

from sales_app.sourcing.fields import ProspectField
from sales_app.sourcing.ports import ProspectState
from sales_app.sourcing.statuses import effective_status
from sales_core.contracts import Prospect, Scheduling


def merge_prospect(
    source: Prospect,
    state: ProspectState | None,
    *,
    owned: Collection[ProspectField],
    public_id: str | None = None,
    contact_opted_out: bool = False,
) -> Prospect:
    """The prospect the Core sees.

    - Identity, contact, language and attributes always come from the source.
    - Each of Revansa's own fields comes from the source if it is in `owned`, otherwise from
      `state`, otherwise it is empty (a prospect Revansa never touched).
    - The status is `effective_status`: the stricter of the source's and Revansa's, and `OPTED_OUT`
      whenever the contact has opted out (`contact_opted_out`, decided by the caller).
    - `public_id` replaces the source's native id (see `identifiers`).

    Raises `ValueError` if `state` belongs to another client or to another prospect.
    """
    owned_fields = frozenset(owned)
    prospect_id = public_id if public_id is not None else source.prospect_id
    if state is not None and (
        state.client_id != source.client_id or state.prospect_id != prospect_id
    ):
        raise ValueError("the operational state belongs to another prospect or client")

    def revansa(field: ProspectField, from_source, from_state, default=None):
        if field in owned_fields:
            return from_source
        return from_state if state is not None else default

    own = source.schedule
    held = state.schedule if state is not None else Scheduling()
    if ProspectField.NEXT_ACTION in owned_fields:
        next_type, next_due = own.next_action_type, own.next_action_due_at
    else:
        next_type, next_due = held.next_action_type, held.next_action_due_at

    schedule = Scheduling(
        next_action_type=next_type,
        next_action_due_at=next_due,
        last_contacted_at=revansa(
            ProspectField.LAST_CONTACTED_AT, own.last_contacted_at, held.last_contacted_at
        ),
        last_inbound_at=revansa(
            ProspectField.LAST_INBOUND_AT, own.last_inbound_at, held.last_inbound_at
        ),
        follow_up_count=revansa(
            ProspectField.FOLLOW_UP_COUNT, own.follow_up_count, held.follow_up_count, 0
        ),
    )
    status = effective_status(
        source.status if ProspectField.STATUS in owned_fields else None,
        state.status if state is not None else None,
        contact_opted_out=contact_opted_out,
    )
    return Prospect(
        client_id=source.client_id,
        prospect_id=prospect_id,  # type: ignore[arg-type]  # a plain str at run time
        display_name=source.display_name,
        contact=source.contact,
        status=status,
        stage=revansa(ProspectField.STAGE, source.stage, state.stage if state else None),
        locale=source.locale,
        conversation_id=revansa(
            ProspectField.CONVERSATION_ID,
            source.conversation_id,
            state.conversation_id if state else None,
        ),
        schedule=schedule,
        attributes=source.attributes,
    )
