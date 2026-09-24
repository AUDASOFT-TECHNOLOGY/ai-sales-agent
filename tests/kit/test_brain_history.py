"""What the Brain reads from the conversation: who spoke last and how much has been asked."""

from brain_support import turns
from factories import make_conversation

from sales_brain_kit.consultative.history import count_questions, view_history


def view(*items):
    return view_history(make_conversation(messages=turns(*items)))


class TestQuestions:
    def test_a_run_of_question_marks_counts_once(self):
        assert count_questions("Really??? Sure?") == 2
        assert count_questions("No question here.") == 0

    def test_question_marks_of_other_scripts_count_too(self):
        assert count_questions("a؟ b？ c?") == 3


class TestTheView:
    def test_an_empty_conversation(self):
        empty = view()
        assert empty.messages == () and empty.first_direction is None
        assert empty.unanswered == 0 and not empty.has_prior_outbound and not empty.awaiting_reply

    def test_unanswered_messages_are_ours_since_the_prospect_last_wrote(self):
        assert view(("out", "a"), ("in", "b"), ("out", "c"), ("out", "d")).unanswered == 2
        assert view(("out", "a"), ("in", "b")).unanswered == 0

    def test_questions_asked_and_asked_in_a_row(self):
        history = view(
            ("out", "One?"),
            ("in", "x"),
            ("out", "A statement."),
            ("out", "Two?"),
            ("out", "Three?"),
        )
        assert history.questions_asked == 3
        assert history.consecutive_questions == 2  # a statement broke the run before

    def test_who_spoke_first_and_last(self):
        history = view(("in", "hi"), ("out", "hello"), ("in", "more"))
        assert history.first_direction.value == "inbound"
        assert history.last_inbound.text == "more" and history.last_outbound.text == "hello"
