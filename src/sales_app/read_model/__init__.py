"""Reading a client's prospects and conversations by the database, independently of the Engine.

See `docs/read-model-and-metrics.md`.
"""

from sales_app.read_model.memory import conversations_in_memory, list_in_memory
from sales_app.read_model.models import (
    DEFAULT_MESSAGE_PAGE,
    DEFAULT_PAGE,
    MAX_MESSAGE_PAGE,
    MAX_PAGE,
    ActionError,
    ConversationDetail,
    ConversationItem,
    LifecycleStatus,
    MessageItem,
    MessageOrder,
    Page,
    ProspectFilter,
    ProspectItem,
    ProspectListQuery,
    ProspectSort,
)
from sales_app.read_model.ports import ProspectReadModel, ReadModelUnavailableError

__all__ = [
    "DEFAULT_MESSAGE_PAGE",
    "DEFAULT_PAGE",
    "MAX_MESSAGE_PAGE",
    "MAX_PAGE",
    "ActionError",
    "ConversationDetail",
    "ConversationItem",
    "LifecycleStatus",
    "MessageItem",
    "MessageOrder",
    "Page",
    "ProspectFilter",
    "ProspectItem",
    "ProspectListQuery",
    "ProspectReadModel",
    "ProspectSort",
    "ReadModelUnavailableError",
    "conversations_in_memory",
    "list_in_memory",
]
