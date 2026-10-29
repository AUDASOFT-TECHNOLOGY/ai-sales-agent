"""What the client taught the agent (`communication.playbook`, `communication.training`) reaches the
Brain's instructions, only the lessons that help the message in hand are shown, and a lesson that cannot
be understood, or that holds a price or a link, is refused when the settings are saved."""

import pytest
from brain_support import REPLY, context_for, settings_doc, turns, with_products

from sales_brain_kit import SettingsInvalidError, parse_settings
from sales_brain_kit.consultative.prompt import build_system_prompt
from sales_brain_kit.consultative.training import SHOWN


def doc_with(*lessons, playbook=None):
    doc = settings_doc()
    doc["communication"]["training"] = list(lessons)
    if playbook is not None:
        doc["communication"]["playbook"] = playbook
    return doc


def system_for(*lessons, playbook=None, **context) -> str:
    return build_system_prompt(context_for(doc_with(*lessons, playbook=playbook), **context))


# ---- habits and examples (unchanged behaviour) ---------------------------------------------------


def test_a_habit_is_an_instruction_of_the_business():
    prompt = system_for({"kind": "habit", "text": "Write like a chat message, no bullet lists"})
    assert "- Habit of the business: Write like a chat message, no bullet lists" in prompt


def test_an_example_gives_the_situation_and_the_clients_own_answer():
    prompt = system_for(
        {
            "kind": "example",
            "when": "the prospect says it is too expensive",
            "text": "I get it. Which part worries you?",
        }
    )
    assert (
        'When the prospect says it is too expensive, the business answers: "I get it. Which part worries you?"'
        in prompt
    )
    assert "never copy it blindly" in prompt


def test_without_training_the_prompt_has_no_lesson():
    prompt = build_system_prompt(context_for())
    assert "Habit of the business" not in prompt and "Taught answer" not in prompt
    assert "Taught by the business" not in prompt


# ---- the other kinds of lesson -------------------------------------------------------------------


def test_a_red_line_is_stated_as_a_never():
    prompt = system_for({"kind": "never", "text": "Promise a result"})
    assert "- Never: Promise a result" in prompt


def test_an_objection_gives_how_it_sounds_the_answer_and_the_reason():
    prompt = system_for(
        {
            "kind": "objection",
            "when": "I need to think about it",
            "text": "Of course. What would help you decide?",
            "why": "Find the real doubt first",
        }
    )
    assert (
        'something like "I need to think about it", the business answers: "Of course. What would help you decide?"'
        in prompt
    )
    assert "Reason: Find the real doubt first" in prompt


def test_a_frequent_question_is_answered_with_the_clients_answer():
    prompt = system_for(
        {"kind": "faq", "when": "Is there a refund?", "text": "Yes, within 14 days."}
    )
    assert (
        'Frequent question: "Is there a refund?" The business answers: "Yes, within 14 days."'
        in prompt
    )


def test_a_proof_is_quoted_as_given_with_its_source():
    prompt = system_for(
        {"kind": "proof", "text": "Doubled my leads in a month", "source": "Leila, bakery owner"}
    )
    assert '(Leila, bakery owner): "Doubled my leads in a month"' in prompt
    assert "Quote it only as given" in prompt


def test_proofs_are_left_out_when_the_client_wants_no_social_proof():
    prompt = system_for(
        {"kind": "proof", "text": "Doubled my leads"}, playbook={"social_proof": "never"}
    )
    assert "Doubled my leads" not in prompt
    assert "Do not bring testimonials" in prompt


# ---- the sales method ----------------------------------------------------------------------------


def test_the_default_method_is_consultative_and_has_the_craft():
    prompt = build_system_prompt(context_for())
    assert "- Approach: Sell by understanding first." in prompt
    assert "- Craft: One idea, one benefit and one next step per message." in prompt
    assert "Never create urgency" in prompt


def test_each_choice_of_the_method_has_its_own_wording():
    prompt = system_for(
        playbook={
            "approach": "challenger",
            "closing": "assumptive",
            "urgency": "real_reasons_only",
            "pitch": "We make selling easy",
            "ideal_customer": "Small shops",
        }
    )
    assert "Sell by teaching." in prompt
    assert "Close as if the next step is natural" in prompt
    assert "only when the catalog or a lesson truly states it" in prompt
    assert "- The business in the client's own words: We make selling easy" in prompt
    assert "- Who the business serves best: Small shops" in prompt


def test_the_rules_always_win_over_a_lesson():
    prompt = system_for({"kind": "habit", "text": "Be brief"})
    assert "the Rules below always win" in prompt


# ---- only the lessons that help this message -----------------------------------------------------


def objections(n):
    return [
        {"kind": "objection", "when": f"topic{i} worry", "text": f"Answer {i}"} for i in range(n)
    ]


def test_a_message_shows_at_most_a_few_lessons_of_each_kind():
    prompt = system_for(*objections(20))
    assert prompt.count("Taught objection handling") == SHOWN["objection"]


def test_the_closest_lessons_are_the_ones_shown():
    lessons = [
        {"kind": "objection", "when": "too expensive for my budget", "text": "Budget answer"},
        {"kind": "objection", "when": "my partner must agree", "text": "Partner answer"},
        *objections(10),
    ]
    context = context_for(
        doc_with(*lessons),
        action=REPLY,
        messages=turns(("in", "It is too expensive for my budget")),
    )
    prompt = build_system_prompt(context)
    assert "Budget answer" in prompt
    assert prompt.count("Taught objection handling") == SHOWN["objection"]


def test_a_lesson_tied_to_a_product_is_shown_only_with_that_product():
    lessons = [
        {
            "kind": "faq",
            "when": "Does it come with a certificate?",
            "text": "Yes, a certificate.",
            "product": "alpha",
        },
        {"kind": "faq", "when": "Where are you based?", "text": "Everywhere online."},
    ]
    for product, shown, hidden in (
        ("alpha", "a certificate", None),
        ("beta", "Everywhere online.", "a certificate"),
    ):
        doc = with_products(product)
        doc["communication"]["training"] = lessons
        prompt = build_system_prompt(context_for(doc))
        assert shown in prompt
        if hidden:
            assert hidden not in prompt


# ---- refused when saved --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lesson",
    [
        {"kind": "example", "text": "An answer without a situation"},
        {"kind": "objection", "text": "An objection without how it sounds"},
        {"kind": "faq", "text": "An answer without a question"},
        {"kind": "habit", "text": "A habit", "when": "never"},
        {"kind": "never", "text": "A red line", "when": "sometimes"},
        {"kind": "habit", "text": "A habit", "product": "alpha"},
        {"kind": "example", "when": "x", "text": "y", "source": "someone"},
        {"kind": "other", "text": "Unknown kind"},
    ],
)
def test_a_lesson_that_cannot_be_understood_is_refused(lesson):
    with pytest.raises(SettingsInvalidError):
        parse_settings(doc_with(lesson))


@pytest.mark.parametrize(
    "text",
    [
        "It costs 49 EUR a month",
        "Pay at https://example.org/pay",
        "Write to sales@example.org",
        "Send it to FR76 3000 6000 0112 3456 7890 189",
    ],
)
def test_a_lesson_never_holds_a_price_a_link_or_an_account(text):
    with pytest.raises(SettingsInvalidError):
        parse_settings(doc_with({"kind": "faq", "when": "How much?", "text": text}))


def test_the_pitch_never_holds_a_link():
    with pytest.raises(SettingsInvalidError):
        parse_settings(doc_with(playbook={"pitch": "See https://example.org"}))


def test_an_unknown_choice_of_method_is_refused():
    with pytest.raises(SettingsInvalidError):
        parse_settings(doc_with(playbook={"approach": "manipulative"}))


def test_the_agent_keeps_at_most_sixty_lessons():
    parse_settings(doc_with(*[{"kind": "habit", "text": f"Habit {i}"} for i in range(60)]))
    with pytest.raises(SettingsInvalidError):
        parse_settings(doc_with(*[{"kind": "habit", "text": f"Habit {i}"} for i in range(61)]))
