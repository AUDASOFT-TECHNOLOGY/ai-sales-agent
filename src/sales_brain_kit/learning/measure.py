"""What the numbers of a case show, with no model: how long, how many questions, how fast.

Each detector reads only the anonymous steps. `observed` says which codes are present anywhere in
the case (so a code can be compared between conversations that went well and ones that did not);
`blamed` says which ones the *last message before things went wrong* shows, which is the only
place where a measured code can be held responsible.
"""

from collections.abc import Sequence

from sales_core.learning import CaseStep

SHORT = 300  # characters
LONG = 500
SHARE = 0.7  # of the messages for a style to count as the agent's habit
SPACED_HOURS = 48.0
QUICK_HOURS = 24.0
FOLLOW_UPS = {"follow_up", "reminder"}


def sent(steps: Sequence[CaseStep]) -> list[CaseStep]:
    return [s for s in steps if s.decision == "send_message" and s.length is not None]


def _is_follow_up(step: CaseStep) -> bool:
    return step.action_type in FOLLOW_UPS and step.unanswered >= 1 and step.gap_hours is not None


def observed(steps: Sequence[CaseStep]) -> frozenset[str]:
    out: set[str] = set()
    messages = sent(steps)
    if not messages:
        return frozenset()
    total = len(messages)
    if sum(1 for s in messages if (s.length or 0) <= SHORT) / total >= SHARE:
        out.add("short_messages")
    if sum(1 for s in messages if (s.length or 0) > LONG) / total >= 0.5:
        out.add("long_messages")
    if sum(1 for s in messages if s.questions == 1) / total >= SHARE:
        out.add("single_question")
    follow_ups = [s for s in messages if _is_follow_up(s)]
    if follow_ups and all((s.gap_hours or 0) >= SPACED_HOURS for s in follow_ups):
        out.add("spaced_follow_ups")
    if any((s.gap_hours or 0) < QUICK_HOURS for s in follow_ups):
        out.add("quick_follow_up")
    return frozenset(out)


def blamed(steps: Sequence[CaseStep]) -> tuple[frozenset[str], int | None]:
    """The measured causes shown by the last message sent, and that message's step index."""
    messages = sent(steps)
    if not messages:
        return frozenset(), None
    last = messages[-1]
    causes: set[str] = set()
    if (last.length or 0) > LONG:
        causes.add("long_messages")
    if _is_follow_up(last) and (last.gap_hours or 0) < QUICK_HOURS:
        causes.add("quick_follow_up")
    return frozenset(causes), (last.index if causes else None)
