"""Learning from results: what happened to a conversation, and what the agent did along the way.

The Core stays neutral: it names no practice and judges nothing. It derives a *result* for each
conversation from data it already has, keeps an anonymous *trace* of each decision, and hands the
cases to whoever can reflect on them (a Brain). See `results.py`, `trace.py` and `reflection.py`.
"""

from sales_core.learning.notes import NotesProvider
from sales_core.learning.reflection import (
    CaseReview,
    CaseStep,
    CaseText,
    LearningCase,
    Reflector,
)
from sales_core.learning.results import (
    DEFAULT_QUIET_AFTER,
    ConversationSignals,
    Result,
    is_rehearsal,
    resolve_result,
)
from sales_core.learning.trace import (
    NO_TRACE,
    DecisionTrace,
    NoTrace,
    TraceSink,
    anonymous_ref,
    build_trace,
    clean_labels,
    count_questions,
    sends_a_message,
)

__all__ = [
    "DEFAULT_QUIET_AFTER",
    "NO_TRACE",
    "CaseReview",
    "CaseStep",
    "CaseText",
    "ConversationSignals",
    "DecisionTrace",
    "LearningCase",
    "NoTrace",
    "NotesProvider",
    "Reflector",
    "Result",
    "TraceSink",
    "anonymous_ref",
    "build_trace",
    "clean_labels",
    "count_questions",
    "is_rehearsal",
    "resolve_result",
    "sends_a_message",
]
