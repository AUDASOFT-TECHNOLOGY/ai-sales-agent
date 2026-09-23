"""In-memory messaging channel for tests. Delivers nothing."""

from collections import deque

from sales_core.contracts import Channel, OutboundMessage, SendResult, SendStatus
from sales_core.ports import Clock
from sales_integrations.fakes.fake_clock import FakeClock


class FakeMessagingChannel:
    """Implements `sales_core.ports.MessagingChannel`.

    Accepts every message by default. Failures can be scripted with `fail_next` or
    `queue_results`. Honours the port contract: expected failures are returned, never raised,
    and a message whose idempotency key was already accepted is not delivered a second time
    (the original result is returned).
    """

    def __init__(self, channel: Channel = Channel.WHATSAPP, *, clock: Clock | None = None) -> None:
        self._channel = channel
        self._clock: Clock = clock if clock is not None else FakeClock()
        self._queued: deque[SendResult] = deque()
        self._attempts: list[OutboundMessage] = []
        self._sent: list[OutboundMessage] = []
        self._delivered: dict[str, SendResult] = {}

    @property
    def channel(self) -> Channel:
        return self._channel

    @property
    def attempts(self) -> tuple[OutboundMessage, ...]:
        """Every call to `send`, including failures and idempotent replays."""
        return tuple(self._attempts)

    @property
    def sent(self) -> tuple[OutboundMessage, ...]:
        """Messages actually accepted for delivery (each idempotency key at most once)."""
        return tuple(self._sent)

    def queue_results(self, *results: SendResult) -> None:
        """Use these results, in order, for the next new (non-replayed) sends."""
        self._queued.extend(results)

    def fail_next(
        self,
        *,
        transient: bool = True,
        error_message: str = "simulated failure",
        error_code: str | None = None,
        count: int = 1,
    ) -> None:
        status = SendStatus.FAILED_TRANSIENT if transient else SendStatus.FAILED_PERMANENT
        for _ in range(count):
            self._queued.append(
                SendResult(
                    status=status,
                    timestamp=self._clock.now(),
                    error_code=error_code,
                    error_message=error_message,
                )
            )

    def send(self, message: OutboundMessage) -> SendResult:
        self._attempts.append(message)
        if message.channel is not self._channel:
            return SendResult(
                status=SendStatus.FAILED_PERMANENT,
                timestamp=self._clock.now(),
                error_code="unsupported_channel",
                error_message=f"this channel serves {self._channel}, not {message.channel}",
            )
        previous = self._delivered.get(message.idempotency_key)
        if previous is not None:
            return previous
        result = self._queued.popleft() if self._queued else self._accept()
        if result.ok:
            self._sent.append(message)
            self._delivered[message.idempotency_key] = result
        return result

    def _accept(self) -> SendResult:
        return SendResult(
            status=SendStatus.ACCEPTED,
            timestamp=self._clock.now(),
            external_id=f"fake-{len(self._sent) + 1}",
        )
