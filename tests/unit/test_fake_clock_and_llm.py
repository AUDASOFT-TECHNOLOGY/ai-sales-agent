from datetime import UTC, datetime, timedelta

import pytest
from factories import NAIVE_NOW, NOW

from sales_core.contracts import LLMMessage, LLMRequest, LLMResponse, LLMRole
from sales_core.ports import Clock, LLMProvider
from sales_integrations.fakes import FakeClock, FakeLLMProvider
from sales_integrations.fakes.fake_clock import DEFAULT_START


def _request(text: str = "hi") -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role=LLMRole.USER, content=text)])


class TestFakeClock:
    def test_satisfies_the_port(self):
        assert isinstance(FakeClock(), Clock)

    def test_starts_at_a_fixed_aware_time_and_does_not_tick(self):
        clock = FakeClock()
        assert clock.now() == DEFAULT_START
        assert clock.now().tzinfo is not None
        assert clock.now() == clock.now()

    def test_advance_moves_forward_and_returns_the_new_time(self):
        clock = FakeClock(NOW)
        assert clock.advance(timedelta(days=2)) == NOW + timedelta(days=2)
        assert clock.now() == NOW + timedelta(days=2)

    def test_advance_cannot_go_backwards(self):
        with pytest.raises(ValueError, match="backwards"):
            FakeClock(NOW).advance(timedelta(seconds=-1))

    def test_set_jumps_to_any_aware_time(self):
        clock = FakeClock(NOW)
        clock.set(datetime(2025, 6, 1, tzinfo=UTC))
        assert clock.now().year == 2025

    def test_rejects_naive_times(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            FakeClock(NAIVE_NOW)
        with pytest.raises(ValueError, match="timezone-aware"):
            FakeClock(NOW).set(NAIVE_NOW)


class TestFakeLLMProvider:
    def test_satisfies_the_port(self):
        assert isinstance(FakeLLMProvider(), LLMProvider)

    def test_replays_the_script_in_order(self):
        llm = FakeLLMProvider(["one", LLMResponse(text="two", parsed={"k": "v"})])
        assert llm.complete(_request()).text == "one"
        second = llm.complete(_request())
        assert second.text == "two" and second.parsed == {"k": "v"}

    def test_plain_strings_become_responses_from_a_fake_model(self):
        assert FakeLLMProvider(["x"]).complete(_request()).model == "fake-model"

    def test_enqueue_appends_to_the_script(self):
        llm = FakeLLMProvider(["one"])
        llm.enqueue("two", "three")
        assert [llm.complete(_request()).text for _ in range(3)] == ["one", "two", "three"]

    def test_default_answers_once_the_script_is_empty(self):
        llm = FakeLLMProvider(["one"], default="fallback")
        assert llm.complete(_request()).text == "one"
        assert llm.complete(_request()).text == "fallback"
        assert llm.complete(_request()).text == "fallback"

    def test_unplanned_calls_fail_loudly(self):
        with pytest.raises(LookupError, match="no scripted response"):
            FakeLLMProvider().complete(_request())

    def test_scripted_exceptions_are_raised_in_sequence(self):
        llm = FakeLLMProvider([TimeoutError("slow"), "recovered"])
        with pytest.raises(TimeoutError):
            llm.complete(_request())
        assert llm.complete(_request()).text == "recovered"

    def test_records_every_request_including_failed_ones(self):
        llm = FakeLLMProvider([RuntimeError("boom"), "ok"])
        first, second = _request("a"), _request("b")
        with pytest.raises(RuntimeError):
            llm.complete(first)
        llm.complete(second)
        assert llm.calls == (first, second)
        assert llm.last_request is second

    def test_last_request_is_none_before_any_call(self):
        assert FakeLLMProvider().last_request is None
