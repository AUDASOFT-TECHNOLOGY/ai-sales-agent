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


# ---- discounts and competitors -------------------------------------------------------------------


def test_by_default_the_agent_never_offers_a_discount_and_never_criticises_a_competitor():
    prompt = build_system_prompt(context_for())
    assert "Never offer or promise a discount" in prompt
    assert "never criticise it" in prompt


def test_the_client_can_have_discount_requests_go_to_a_person_and_competitors_left_alone():
    prompt = system_for(playbook={"discounts": "ask_a_person", "competitors": "avoid"})
    assert "a person will look at it with them" in prompt
    assert "do not discuss it" in prompt


# ---- the same worry in other words ---------------------------------------------------------------


def test_a_lesson_is_found_when_the_prospect_says_the_same_thing_in_other_words():
    lessons = [
        {"kind": "objection", "when": "It is too expensive", "text": "Price answer"},
        *[
            {"kind": "objection", "when": f"Other doubt {i} about topic{i}", "text": f"Other {i}"}
            for i in range(8)
        ],
    ]
    for said in ("Le tarif me dépasse", "I cannot afford this"):
        context = context_for(doc_with(*lessons), action=REPLY, messages=turns(("in", said)))
        prompt = build_system_prompt(context)
        assert "Price answer" in prompt, said


# ---- the red lines are checked ---------------------------------------------------------------------


def test_the_model_is_asked_to_check_its_message_against_the_red_lines_only_when_there_are_some():
    assert "breaks_red_line" in system_for({"kind": "never", "text": "Promise a result"})
    assert "breaks_red_line" not in system_for({"kind": "habit", "text": "Be brief"})


def test_a_message_that_breaks_a_red_line_is_refused_and_written_again():
    from brain_support import answer, make_brain

    doc = doc_with({"kind": "never", "text": "Promise a result"})
    brain, llm = make_brain(
        doc,
        answer(message="We guarantee results!", breaks_red_line=True),
        answer(message="Here is what we offer."),
    )
    decision = brain.decide(__import__("brain_support").request())
    assert decision.message.text == "Here is what we offer."
    assert len(llm.calls) == 2
    assert "the 'Never' lines" in llm.calls[1].messages[-1].content
