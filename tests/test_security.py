"""Adversarial and authorization tests: input bounds, resource limits, secret handling."""

import io
import json
import logging
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from epl.api.limits import BusyError, ComputeGate, RateLimitedError, TokenBucket
from epl.api.main import create_app
from epl.core.config import Settings
from epl.core.logging import JsonFormatter
from epl.data.db import session_scope
from epl.services import forecasting, simulation_cache
from tests.conftest import ADMIN_TOKEN

API = "/api/v1"


# --------------------------------------------------------------------------- admin token configuration
@pytest.mark.parametrize("weak", ["change-me", "short", "x" * 31, "SECRET"])
def test_weak_admin_tokens_fail_at_startup(weak: str) -> None:
    with pytest.raises(ValidationError, match="EPL_ADMIN_TOKEN"):
        Settings(admin_token=weak)


@pytest.mark.parametrize("unset", [None, ""])
def test_unset_admin_token_disables_admin(unset: str | None) -> None:
    assert Settings(admin_token=unset).admin_token is None


def test_admin_token_is_never_rendered() -> None:
    settings = Settings(admin_token=ADMIN_TOKEN)

    assert ADMIN_TOKEN not in repr(settings)
    assert ADMIN_TOKEN not in str(settings.model_dump())
    assert settings.admin_token is not None and settings.admin_token.get_secret_value() == ADMIN_TOKEN


def test_admin_token_does_not_reach_the_logs(client: TestClient) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    try:
        client.get(f"{API}/admin/jobs/missing", headers={"X-Admin-Token": ADMIN_TOKEN})
        client.get(f"{API}/admin/jobs/missing", headers={"X-Admin-Token": "wrong"})
    finally:
        logging.getLogger().removeHandler(handler)

    logs = stream.getvalue()
    assert "/admin/jobs/missing" in logs  # the requests were logged...
    assert ADMIN_TOKEN not in logs  # ...but never the credential


def test_admin_rejects_missing_and_wrong_tokens(client: TestClient) -> None:
    assert client.post(f"{API}/admin/refresh").status_code == 401
    assert client.post(f"{API}/admin/refresh", headers={"X-Admin-Token": ADMIN_TOKEN[:-1]}).status_code == 401


# --------------------------------------------------------------------------- simulation inputs and limits
@pytest.mark.parametrize("runs", [1, 999, 1234, 50_000, 10**9])
def test_unsupported_simulation_sizes_are_rejected(client: TestClient, runs: int) -> None:
    response = client.get(f"{API}/seasons/2024/simulation", params={"runs": runs})

    assert response.status_code == 422
    assert "1,000, 10,000" in response.json()["detail"]


def test_repeated_requests_hit_the_cache_without_using_the_rate_limit(seeded: Settings) -> None:
    limited = seeded.model_copy(update={"simulation_rate_per_minute": 1})
    with TestClient(create_app(limited)) as c:
        statuses = [
            c.get(f"{API}/seasons/2024/simulation", params={"runs": 1000}).status_code for _ in range(5)
        ]

    assert statuses == [200] * 5  # one computation, four cache hits


def test_uncached_requests_are_rate_limited_per_client(seeded: Settings) -> None:
    limited = seeded.model_copy(update={"simulation_rate_per_minute": 2})
    with TestClient(create_app(limited)) as c:
        responses = [
            c.get(f"{API}/seasons/2023/simulation", params={"runs": 1000, "as_of_matchweek": week})
            for week in (2, 3, 4)
        ]

    assert [r.status_code for r in responses] == [200, 200, 429]
    assert int(responses[-1].headers["Retry-After"]) >= 1
    assert responses[-1].headers["content-type"].startswith("application/problem+json")


def test_saturated_capacity_returns_503_with_retry_after(client: TestClient) -> None:
    state = client.app.state.epl  # type: ignore[attr-defined]
    state.simulation_gate = ComputeGate(max_concurrency=1, queue_timeout=0.05)
    assert state.simulation_gate._slots.acquire()  # another computation holds the only slot
    try:
        response = client.get(f"{API}/seasons/2024/simulation", params={"runs": 1000})
    finally:
        state.simulation_gate._slots.release()

    assert response.status_code == 503
    assert response.headers["Retry-After"]


def test_cache_storage_is_bounded(seeded: Settings) -> None:
    bounded = seeded.model_copy(update={"simulation_cache_max_rows": 2, "simulation_rate_per_minute": 100})
    with TestClient(create_app(bounded)) as c:
        for week in (2, 3, 4, 5):
            assert (
                c.get(
                    f"{API}/seasons/2023/simulation", params={"runs": 1000, "as_of_matchweek": week}
                ).status_code
                == 200
            )

    with session_scope(bounded.database_url) as session:
        assert simulation_cache.count(session) == 2


def test_concurrent_identical_requests_compute_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    real = forecasting.season_simulation

    def slow_simulation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        time.sleep(0.3)  # long enough for every request to arrive while it runs
        return real(*args, **kwargs)

    monkeypatch.setattr(forecasting, "season_simulation", slow_simulation)
    results: list[int] = []

    def request() -> None:
        results.append(client.get(f"{API}/seasons/2024/simulation", params={"runs": 1000}).status_code)

    threads = [threading.Thread(target=request) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200] * 6
    assert calls == 1


# --------------------------------------------------------------------------- limiter units
def test_token_bucket_refills_over_time() -> None:
    now = [0.0]
    bucket = TokenBucket(rate_per_minute=2, clock=lambda: now[0])
    bucket.take("a")
    bucket.take("a")
    with pytest.raises(RateLimitedError) as exc:
        bucket.take("a")
    assert exc.value.retry_after == pytest.approx(30)

    bucket.take("b")  # other clients are unaffected
    now[0] += 30
    bucket.take("a")  # one token refilled after 30 s


def test_token_bucket_memory_is_bounded() -> None:
    now = [0.0]
    bucket = TokenBucket(rate_per_minute=10, clock=lambda: now[0], max_keys=100)
    for i in range(1_000):
        now[0] += 0.001
        bucket.take(f"client-{i}")

    assert len(bucket._buckets) <= 100


def test_gate_rejects_work_beyond_capacity() -> None:
    gate = ComputeGate(max_concurrency=1, queue_timeout=0.05)
    started, release = threading.Event(), threading.Event()

    def hold() -> int:
        started.set()
        release.wait(2)
        return 1

    worker = threading.Thread(target=lambda: gate.run("a", hold))
    worker.start()
    started.wait(1)
    with pytest.raises(BusyError):
        gate.run("b", lambda: 2)  # a different key needs a free slot
    release.set()
    worker.join()

    assert gate.run("b", lambda: 2) == 2


def test_gate_propagates_errors_to_waiters() -> None:
    gate = ComputeGate(max_concurrency=2, queue_timeout=1)

    def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        gate.run("k", fail)
    assert gate.run("k", lambda: "recovered") == "recovered"  # a failed flight does not poison the key


def test_problem_responses_preserve_headers(client: TestClient) -> None:
    body = client.get(f"{API}/seasons/2024/simulation", params={"runs": 7}).json()

    assert json.dumps(body)  # valid problem document
    assert body["status"] == 422


def test_waiting_requests_do_not_starve_the_connection_pool(seeded: Settings) -> None:
    """Regression: requests queued for a simulation slot used to hold a pooled DB connection, so the requests
    holding the slots could not get a connection to store their results (a circular wait broken only by the
    queue timeout). Found by scripts/benchmark.py; reproduced here with more waiters than pool connections."""
    tight = seeded.model_copy(
        update={
            "simulation_max_concurrency": 1,
            "simulation_queue_timeout_seconds": 5,
            "simulation_rate_per_minute": 1000,
        }
    )
    statuses: list[int] = []
    lock = threading.Lock()
    with TestClient(create_app(tight)) as c:

        def request(season: int, week: int, runs: int) -> None:
            params = {"runs": runs, "as_of_matchweek": week}
            status = c.get(f"{API}/seasons/{season}/simulation", params=params).status_code
            with lock:
                statuses.append(status)

        jobs = [
            (s, w, r) for s in (2023, 2024) for w in range(2, 6) for r in (1000, 10_000)
        ]  # 16 distinct keys
        threads = [threading.Thread(target=request, args=job) for job in jobs]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start

    assert statuses.count(200) == len(jobs), statuses
    assert elapsed < 5  # well under the queue timeout: nobody waited on a starved pool
