"""What goes wrong between the model's answer and a `BrainDecision`."""

from collections.abc import Sequence


class BrainOutputError(ValueError):
    """The model's answer cannot become a decision. `problems` lists every reason.

    The Brain never turns such an answer into some other behaviour: it asks once more (with the
    problems) and, if the answer is still unusable, raises this. The Engine records the failure
    against the action and no decision is applied.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))
