"""The fields of a prospect, as a source or the operational store may own them.

A `Prospect` is one object for the Core, but its fields have two homes. What a person is (name,
contact, language) belongs to the source. What Revansa does with them (status, stage, schedule,
conversation) is Revansa's own state and lives in the operational store, unless the source is a
spreadsheet that holds it all, as Google Sheets does. `ProspectField` names each unit so that
ownership, write permission and change detection can be stated field by field.
"""

from enum import StrEnum

from sales_core.contracts import Prospect


class ProspectField(StrEnum):
    DISPLAY_NAME = "display_name"
    PHONE = "phone"
    EMAIL = "email"
    PREFERRED_CHANNEL = "preferred_channel"
    LOCALE = "locale"
    STATUS = "status"
    STAGE = "stage"
    NEXT_ACTION = "next_action"  # type and due date together: the Core requires both or neither
    LAST_CONTACTED_AT = "last_contacted_at"
    LAST_INBOUND_AT = "last_inbound_at"
    FOLLOW_UP_COUNT = "follow_up_count"
    CONVERSATION_ID = "conversation_id"
    TELEGRAM_CHAT_ID = "telegram_chat_id"  # learned by Revansa from the person: never a source's
    LANE_ID = "lane_id"  # which lane the person belongs to: Revansa's own, written once
    SUMMARY = "summary"  # a one-line commercial recap Revansa builds; only ever written back


# What a person is: always the source's, whatever else it owns. A source may leave them empty.
SOURCE_ONLY_FIELDS = frozenset(
    {
        ProspectField.DISPLAY_NAME,
        ProspectField.PHONE,
        ProspectField.EMAIL,
        ProspectField.PREFERRED_CHANNEL,
        ProspectField.LOCALE,
    }
)
# Revansa's own state: the operational store's, unless the source declares it owns the field.
OVERLAY_FIELDS = frozenset(set(ProspectField) - SOURCE_ONLY_FIELDS)


def field_value(prospect: Prospect, field: ProspectField) -> object:
    """The value of one field, in a form that can be compared for equality."""
    schedule = prospect.schedule
    match field:
        case ProspectField.DISPLAY_NAME:
            return prospect.display_name
        case ProspectField.PHONE:
            return prospect.contact.phone
        case ProspectField.EMAIL:
            return prospect.contact.email
        case ProspectField.PREFERRED_CHANNEL:
            return prospect.contact.preferred_channel
        case ProspectField.LOCALE:
            return prospect.locale
        case ProspectField.STATUS:
            return prospect.status
        case ProspectField.STAGE:
            return prospect.stage
        case ProspectField.NEXT_ACTION:
            return (schedule.next_action_type, schedule.next_action_due_at)
        case ProspectField.LAST_CONTACTED_AT:
            return schedule.last_contacted_at
        case ProspectField.LAST_INBOUND_AT:
            return schedule.last_inbound_at
        case ProspectField.FOLLOW_UP_COUNT:
            return schedule.follow_up_count
        case ProspectField.CONVERSATION_ID:
            return prospect.conversation_id
        case ProspectField.TELEGRAM_CHAT_ID:
            return prospect.contact.telegram_chat_id
        case ProspectField.LANE_ID:
            return prospect.lane_id


def changed_fields(before: Prospect, after: Prospect) -> frozenset[ProspectField]:
    """The fields whose value differs between two versions of the same prospect.

    `attributes` are never reported: they are the source's extra columns, opaque and read-only.
    The Core's `update_prospects` hands over a whole prospect, so this is how a write is narrowed
    to what actually changed.
    """
    return frozenset(
        field for field in ProspectField if field_value(before, field) != field_value(after, field)
    )
