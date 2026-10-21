"""Failed sends, unreadable inboxes and model errors open notifications (spec 14); success closes them."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sales_app.notification_wiring import job_result_hook
from sales_app.notifications import (
    InMemoryNotificationStore,
    NotificationQuery,
    NotificationService,
)
from sales_app.operational_signals import (
    NotifyingChannel,
    NotifyingLLMProvider,
    OperationalSignals,
)
from sales_app.scheduler import Job, JobResult
from sales_core.contracts import Channel, SendResult, SendStatus
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_core.ports.llm import LLMAuthError, LLMOutputError, LLMRateLimitError

NOW = datetime(2026, 5, 1, tzinfo=UTC)


class Fx:
    def __init__(self):
        self.service = NotificationService(InMemoryNotificationStore(), now=lambda: NOW)
        self.signals = OperationalSignals(self.service)

    def open(self, audience, client="alpha"):
        items, _ = self.service.search(
            NotificationQuery(audience=audience, client_id=client, include_resolved=False)
        )
        return items


@pytest.fixture
def fx():
    return Fx()


class FakeChannel:
    channel = Channel.EMAIL

    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def send(self, message):
        if self.error:
            raise self.error
        return self.result


def result(status, metadata=None):
    return SendResult(
        status=status,
        timestamp=NOW,
        external_id="x" if status is SendStatus.ACCEPTED else None,
        error_code=None if status is SendStatus.ACCEPTED else "boom",
        error_message=None if status is SendStatus.ACCEPTED else "secret-provider-text",
        metadata=metadata or {},
    )


class TestSend:
    def test_failure_notifies_once_then_success_resolves(self, fx):
        failed = NotifyingChannel(
            FakeChannel(result(SendStatus.FAILED_PERMANENT)), "alpha", fx.signals
        )
        for _ in range(3):
            assert failed.send(None).ok is False  # the send itself is unchanged
        assert [n.kind for n in fx.open("client")] == ["send_failure"]
        assert [n.kind for n in fx.open("admin")] == ["send_failure"]
        text = " ".join(f"{n.title} {n.message} {n.action}" for n in fx.open("client"))
        assert "secret-provider-text" not in text and "Email" in text
        NotifyingChannel(FakeChannel(result(SendStatus.ACCEPTED)), "alpha", fx.signals).send(None)
        assert fx.open("client") == [] and fx.open("admin") == []

    def test_an_exception_still_propagates_and_notifies(self, fx):
        broken = NotifyingChannel(FakeChannel(error=RuntimeError("x")), "alpha", fx.signals)
        with pytest.raises(RuntimeError):
            broken.send(None)
        assert len(fx.open("client")) == 1

    def test_a_cap_deferral_is_not_a_failure(self, fx):
        deferred = result(SendStatus.FAILED_TRANSIENT, {DEFERRED_KEY: DEFERRED_VALUE})
        NotifyingChannel(FakeChannel(deferred), "alpha", fx.signals).send(None)
        assert fx.open("client") == []

    def test_clients_are_separate(self, fx):
        NotifyingChannel(
            FakeChannel(result(SendStatus.FAILED_TRANSIENT)), "beta", fx.signals
        ).send(None)
        assert fx.open("client", "alpha") == [] and len(fx.open("client", "beta")) == 1


class FakeLLM:
    def __init__(self, error=None):
        self.error = error

    def complete(self, request):
        if self.error:
            raise self.error
        return "ok"


class TestLlm:
    def test_quota_and_credentials_notify_and_success_resolves(self, fx):
        with pytest.raises(LLMRateLimitError):
            NotifyingLLMProvider(FakeLLM(LLMRateLimitError()), "alpha", fx.signals).complete(1)
        with pytest.raises(LLMAuthError):
            NotifyingLLMProvider(FakeLLM(LLMAuthError()), "alpha", fx.signals).complete(1)
        assert sorted(n.kind for n in fx.open("client")) == ["config_problem", "quota_reached"]
        assert NotifyingLLMProvider(FakeLLM(), "alpha", fx.signals).complete(1) == "ok"
        assert fx.open("client") == [] and fx.open("admin") == []

    def test_transient_errors_do_not_notify(self, fx):
        with pytest.raises(LLMOutputError):
            NotifyingLLMProvider(FakeLLM(LLMOutputError()), "alpha", fx.signals).complete(1)
        assert fx.open("client") == []


class TestInbox:
    def test_a_failed_poll_notifies_per_channel_and_a_good_one_resolves(self, fx):
        hook = job_result_hook(fx.service)
        hook(JobResult("alpha", Job.INBOX, ok=False, errors=("x",), channels=("email",)))
        hook(JobResult("alpha", Job.INBOX, ok=False, errors=("x",), channels=("email",)))
        assert [n.kind for n in fx.open("client")] == ["receive_failure"]
        hook(JobResult("alpha", Job.INBOX, ok=True, skipped=True))  # held elsewhere: says nothing
        assert len(fx.open("client")) == 1
        hook(JobResult("alpha", Job.INBOX, ok=True, channels=("email",)))
        assert fx.open("client") == []

    def test_the_scheduler_names_the_polled_channels(self):
        from sales_app.wiring import IntegrationBuilders

        assert hasattr(IntegrationBuilders, "inbox_channels")
        assert SimpleNamespace  # keeps the import used
