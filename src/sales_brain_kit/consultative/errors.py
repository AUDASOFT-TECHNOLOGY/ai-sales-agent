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


class LaneScopeError(ValueError):
    """The lane of the prospect lists products the client's catalog does not have.

    This is a configuration fault, not a model failure: no model is asked and no decision exists.
    """

    def __init__(self, lane_id: str, missing: Sequence[str]) -> None:
        self.lane_id = lane_id
        self.missing = tuple(missing)
        super().__init__(
            f"lane {lane_id!r} offers products that are not in the catalog: {', '.join(missing)}"
        )
