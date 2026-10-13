"""Typed identifiers. Opaque strings to the Core; adapters decide how they are produced."""

from typing import NewType

ClientId = NewType("ClientId", str)
ProspectId = NewType("ProspectId", str)
ConversationId = NewType("ConversationId", str)
MessageId = NewType("MessageId", str)
ActionId = NewType("ActionId", str)
LaneId = NewType("LaneId", str)
