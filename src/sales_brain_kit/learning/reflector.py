"""The part of the consultative Brain that learns from results (`sales_core.learning.Reflector`).

Kept out of `consultative/brain.py`: the Brain that decides reaches the Core only through its
contracts and its model port, while this part reads the Core's learning types.
"""

from collections.abc import Sequence
from datetime import datetime

from sales_brain_kit.learning.induction import consolidate
from sales_brain_kit.learning.review import review_case
from sales_brain_kit.settings import ConsultativeSalesSettings
from sales_core.contracts import LearnedNote
from sales_core.learning import CaseReview, LearningCase
from sales_core.ports import LLMProvider


class Reflection:
    """Mixed into the Brain, which holds the model (`_llm`) and the client's settings (`_settings`)."""

    _llm: LLMProvider
    _settings: ConsultativeSalesSettings

    def achieved_stages(self) -> frozenset[str]:
        """The stages that mean the goal was reached: the one marked `won`, and the one that is the
        goal when the client's main goal is a meeting or a lead."""
        settings = self._settings
        milestones = {"won"}
        goals = {g.id: g.kind for g in settings.objectives.goals}
        main = goals.get(settings.objectives.primary or "")
        if main == "meeting":
            milestones.add("appointment")
        elif main == "lead_capture":
            milestones.add("qualified")
        return frozenset(s.id for s in settings.stages if s.milestone in milestones)

    def review(self, case: LearningCase, *, now: datetime) -> CaseReview:
        return review_case(self._llm, case, now=now)

    def consolidate(
        self, reviews: Sequence[CaseReview], notes: Sequence[LearnedNote], *, now: datetime
    ) -> Sequence[LearnedNote]:
        return consolidate(reviews, notes, now=now)
