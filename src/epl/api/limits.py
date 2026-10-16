"""Resource controls for expensive, uncached work (season simulations).

Three independent protections, applied only when a request would trigger new
computation (cache hits are free):

1. **Single-flight.** Concurrent requests for the same simulation share one
   computation instead of each starting their own.
2. **Per-client token bucket.** Each client may start at most
   `rate_per_minute` new computations per minute (burst = the same number).
3. **Concurrency gate.** At most `max_concurrency` computations run at once
   in this process; a request that cannot start within `queue_timeout`
   seconds is rejected rather than queued indefinitely.

The operating model is a single API process (one uvicorn worker), as in the
Docker image. With N processes the bounds multiply by N; a reverse proxy rate
limit would then be the shared control. See docs/adr/0003-bounded-simulation-work.md.
"""

import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any


class RateLimitedError(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limit exceeded; retry in {retry_after:.0f}s")
        self.retry_after = retry_after


class BusyError(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("simulation capacity is saturated")
        self.retry_after = retry_after


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucket:
    """Per-key token bucket. Thread-safe; idle keys are evicted so memory stays bounded."""

    def __init__(
        self, rate_per_minute: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000
    ):
        self.capacity = float(rate_per_minute)
        self.refill_per_second = rate_per_minute / 60.0
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> None:
        """Consume one token for `key` or raise RateLimitedError."""
        with self._lock:
            now = self.clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_keys:
                    self._evict_full(now)
                bucket = self._buckets[key] = _Bucket(self.capacity, now)
            bucket.tokens = min(
                self.capacity, bucket.tokens + (now - bucket.updated) * self.refill_per_second
            )
            bucket.updated = now
            if bucket.tokens < 1.0:
                raise RateLimitedError((1.0 - bucket.tokens) / self.refill_per_second)
            bucket.tokens -= 1.0

    def _evict_full(self, now: float) -> None:
        """Drop keys whose buckets have refilled completely: forgetting them changes nothing."""
        refill_time = self.capacity / self.refill_per_second
        for key in [k for k, b in self._buckets.items() if now - b.updated >= refill_time]:
            del self._buckets[key]
        if len(self._buckets) >= self.max_keys:  # still full: drop the oldest
            oldest = min(self._buckets, key=lambda k: self._buckets[k].updated)
            del self._buckets[oldest]


class _Flight:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class ComputeGate:
    """Single-flight + bounded concurrency for expensive keyed computations."""

    def __init__(self, max_concurrency: int, queue_timeout: float) -> None:
        self.queue_timeout = queue_timeout
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._flights: dict[Hashable, _Flight] = {}
        self._lock = threading.Lock()
        self.computations = 0  # for tests and diagnostics

    def run(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        """Return `compute()`'s result; concurrent callers with the same key share one call."""
        with self._lock:
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
        assert flight is not None
        if not leader:
            if not flight.done.wait(self.queue_timeout + 60):
                raise BusyError(self.queue_timeout)
            if flight.error is not None:
                raise flight.error
            return flight.result

        try:
            if not self._slots.acquire(timeout=self.queue_timeout):
                raise BusyError(self.queue_timeout)
            try:
                self.computations += 1
                flight.result = compute()
            finally:
                self._slots.release()
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            flight.done.set()
            with self._lock:
                self._flights.pop(key, None)
        return flight.result
