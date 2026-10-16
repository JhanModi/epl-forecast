"""Reproducible load benchmark of the API server.

Starts `epl serve` as a separate process on the configured dataset (the demo
league by default), drives three workloads over real HTTP and writes a JSON
report with latency percentiles, throughput, error and rejection rates, the
server's CPU time and memory, and the machine it ran on.

    uv run python scripts/benchmark.py                      # writes docs/benchmarks/<date>-<dataset>.json
    uv run python scripts/benchmark.py --requests 500 --concurrency 4

Workloads
1. **Read mix** (cache-warm): meta, upcoming forecasts, standings, ratings,
   Elo history, what-if matchups and a cached season simulation, from
   `--concurrency` client threads.
2. **Cold simulations**: distinct historical simulations (one per matchweek),
   requested one at a time, so each is computed from scratch and stored.
3. **Adversarial burst**: many concurrent *uncached* simulations from one
   client, to show the rate limit and the concurrency gate bounding work.

Numbers depend on the hardware; compare runs on the same machine only.
"""

import argparse
import json
import os
import platform
import random
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
import requests

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def summarise(samples: list[tuple[str, int, float]], wall_seconds: float) -> dict[str, Any]:
    by_endpoint: dict[str, list[float]] = defaultdict(list)
    statuses: Counter[int] = Counter()
    for endpoint, status, ms in samples:
        by_endpoint[endpoint].append(ms)
        statuses[status] += 1
    all_ms = [ms for _, _, ms in samples]
    errors = sum(n for code, n in statuses.items() if code >= 500 and code != 503)
    return {
        "requests": len(samples),
        "wall_seconds": round(wall_seconds, 3),
        "throughput_rps": round(len(samples) / wall_seconds, 1) if wall_seconds else None,
        "status_counts": {str(k): v for k, v in sorted(statuses.items())},
        "error_rate": round(errors / len(samples), 4) if samples else None,
        "latency_ms": {
            "p50": round(percentile(all_ms, 0.5), 1),
            "p95": round(percentile(all_ms, 0.95), 1),
            "p99": round(percentile(all_ms, 0.99), 1),
            "max": round(max(all_ms), 1) if all_ms else None,
        },
        "by_endpoint": {
            name: {
                "n": len(ms),
                "p50_ms": round(percentile(ms, 0.5), 1),
                "p95_ms": round(percentile(ms, 0.95), 1),
                "mean_ms": round(statistics.fmean(ms), 1),
            }
            for name, ms in sorted(by_endpoint.items())
        },
    }


class Server:
    def __init__(self, port: int, env: dict[str, str]) -> None:
        self.port = port
        command = [sys.executable, "-m", "epl.cli", "serve", "--host", "127.0.0.1", "--port", str(port)]
        self.proc = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT
        )
        self.process = psutil.Process(self.proc.pid)

    def wait_ready(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if requests.get(f"{self.url}/api/v1/ready", timeout=2).json().get("status") == "ready":
                    return
            except requests.RequestException:
                pass
            time.sleep(0.3)
        raise RuntimeError("server did not become ready")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def usage(self) -> dict[str, float]:
        """CPU and memory of the server *including child processes* (on Windows a venv's python.exe is a
        launcher that runs the real interpreter as a child)."""
        processes = [self.process, *self.process.children(recursive=True)]
        cpu = sum(p.cpu_times().user + p.cpu_times().system for p in processes)
        rss = sum(p.memory_info().rss for p in processes)
        return {"cpu_seconds": round(cpu, 2), "rss_mib": round(rss / 2**20, 1), "processes": len(processes)}

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def timed(session: requests.Session, url: str, params: dict[str, Any] | None = None) -> tuple[int, float]:
    start = time.perf_counter()
    response = session.get(url, params=params, timeout=120)
    return response.status_code, (time.perf_counter() - start) * 1000


def read_mix(base: str, n: int, concurrency: int, seed: int) -> dict[str, Any]:
    meta = requests.get(f"{base}/api/v1/meta", timeout=10).json()
    season = meta["current_season"]
    teams = requests.get(f"{base}/api/v1/teams", timeout=10).json()
    current = [r["team"] for r in requests.get(f"{base}/api/v1/ratings", timeout=10).json()]
    ids = [t["id"] for t in teams if t["name"] in current]
    requests.get(f"{base}/api/v1/seasons/{season}/simulation", timeout=120)  # warm the cache
    rng = random.Random(seed)

    def plan() -> tuple[str, str, dict[str, Any] | None]:
        kind = rng.choice(["meta", "upcoming", "standings", "ratings", "elo", "matchup", "simulation"])
        if kind == "matchup":
            home, away = rng.sample(current, 2)
            return kind, f"{base}/api/v1/predictions/matchup", {"home": home, "away": away}
        paths = {
            "meta": "/api/v1/meta",
            "upcoming": "/api/v1/predictions/upcoming",
            "standings": f"/api/v1/seasons/{season}/standings",
            "ratings": "/api/v1/ratings",
            "elo": f"/api/v1/teams/{rng.choice(ids)}/elo",
            "simulation": f"/api/v1/seasons/{season}/simulation",
        }
        return kind, f"{base}{paths[kind]}", None

    jobs = [plan() for _ in range(n)]
    samples: list[tuple[str, int, float]] = []
    lock = threading.Lock()
    local = threading.local()

    def run(job: tuple[str, str, dict[str, Any] | None]) -> None:
        if not hasattr(local, "session"):
            local.session = requests.Session()
        status, ms = timed(local.session, job[1], job[2])
        with lock:
            samples.append((job[0], status, ms))

    start = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(run, jobs))
    return summarise(samples, time.perf_counter() - start)


def cold_simulations(base: str, season: int, weeks: list[int]) -> dict[str, Any]:
    session = requests.Session()
    samples = []
    start = time.perf_counter()
    for week in weeks:
        status, ms = timed(
            session, f"{base}/api/v1/seasons/{season}/simulation", {"as_of_matchweek": week, "runs": 10_000}
        )
        samples.append(("cold simulation", status, ms))
    return summarise(samples, time.perf_counter() - start)


def adversarial_burst(base: str, season: int, burst: int) -> dict[str, Any]:
    """Distinct uncached requests (1,000-run size, so they miss the 10,000-run cache) all at once."""
    samples: list[tuple[str, int, float]] = []
    lock = threading.Lock()

    def run(week: int) -> None:
        status, ms = timed(
            requests.Session(),
            f"{base}/api/v1/seasons/{season}/simulation",
            {"as_of_matchweek": week, "runs": 1_000},
        )
        with lock:
            samples.append(("uncached burst", status, ms))

    start = time.perf_counter()
    with ThreadPoolExecutor(burst) as pool:
        list(pool.map(run, list(range(1, burst + 1))))
    return summarise(samples, time.perf_counter() - start)


def machine() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        "memory_gib": round(psutil.virtual_memory().total / 2**30, 1),
        "python": platform.python_version(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requests", type=int, default=2_000)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--burst", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    env = os.environ.copy()
    env.setdefault("EPL_LOG_LEVEL", "WARNING")
    dataset = env.get("EPL_DATASET", "demo")
    # Benchmark a throwaway copy of the database, so every run starts with an empty simulation cache and
    # never modifies the real one.
    scratch = Path(tempfile.mkdtemp(prefix="epl-bench-"))
    source_db = ROOT / "data" / f"{dataset}.db"
    if not source_db.exists():
        raise SystemExit(f"{source_db} not found: run `epl build` first")
    shutil.copy2(source_db, scratch / "bench.db")
    with sqlite3.connect(scratch / "bench.db") as db:  # start cold: no cached simulations
        db.execute("DELETE FROM simulation_runs")
    env["EPL_DATABASE_URL"] = f"sqlite:///{(scratch / 'bench.db').as_posix()}"
    server = Server(free_port(), env)
    try:
        server.wait_ready()
        base = server.url
        meta = requests.get(f"{base}/api/v1/meta", timeout=10).json()
        holdout = meta["holdout_season"]
        before = server.usage()
        mix = read_mix(base, args.requests, args.concurrency, args.seed)
        after_mix = server.usage()
        cold = cold_simulations(base, holdout, list(range(2, 12)))
        burst = adversarial_burst(base, holdout, args.burst)
        after = server.usage()
    finally:
        server.stop()

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": {
            "name": dataset,
            "label": meta["dataset"]["label"],
            "synthetic": meta["dataset"]["synthetic"],
        },
        "server": "one `epl serve` process (single uvicorn worker), SQLite, localhost",
        "machine": machine(),
        "parameters": vars(args) | {"out": str(args.out) if args.out else None},
        "workloads": {
            "read_mix": mix
            | {"server_cpu_seconds": round(after_mix["cpu_seconds"] - before["cpu_seconds"], 2)},
            "cold_simulations": cold,
            "adversarial_burst": burst,
        },
        "server_usage": {"start": before, "end": after},
    }
    out = args.out or ROOT / "docs" / "benchmarks" / f"{datetime.now(UTC):%Y-%m-%d}-{dataset}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"Wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    for name, result in report["workloads"].items():
        lat = result["latency_ms"]
        print(
            f"{name:18s} n={result['requests']:5d}  {result['throughput_rps']} req/s  "
            f"p50 {lat['p50']} ms  p95 {lat['p95']} ms  p99 {lat['p99']} ms  "
            f"statuses {result['status_counts']}"
        )
    print(f"server RSS {before['rss_mib']} -> {after['rss_mib']} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
