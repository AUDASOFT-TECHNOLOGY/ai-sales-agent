"""On Telegram the message comes from a person's own account to someone who may not expect it: the Brain
is told to personalise it strongly. Other channels do not get that instruction."""

from brain_support import context_for

from sales_brain_kit.consultative.prompt import build_system_prompt
from sales_core.contracts import Channel, LaneRoute


def system_for(channel: Channel) -> str:
    return build_system_prompt(context_for(lane=LaneRoute(lane_id="l1", channel=channel)))


def test_a_telegram_message_must_be_strongly_personalised():
    prompt = system_for(Channel.TELEGRAM)
    assert "Telegram. The message is written from a person's own Telegram account" in prompt
    assert "strongly personalised" in prompt
    assert "never invent a detail" in prompt  # personal, but never invented


def test_the_telegram_instruction_is_one_more_numbered_rule():
    lines = system_for(Channel.TELEGRAM).splitlines()
    rule = next(line for line in lines if line.split(". ", 1)[-1].startswith("Telegram."))
    assert rule.split(".", 1)[0].isdigit()


def test_other_channels_get_no_such_instruction():
    for channel in (Channel.EMAIL, Channel.WHATSAPP):
        assert "Telegram." not in system_for(channel)
