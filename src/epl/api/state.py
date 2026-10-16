"""Process-wide state for the API: the match frame, loaded models and derived results.

Loading the data and unpickling models on every request would be wasteful, so
they are cached in the process. Each cached value is tagged with the version of
the *persisted* state it came from:

- the match frame with the latest `data_versions.id`, which every load writes in
  the same transaction as the rows;
- each model with the registry's `latest.json` version.

Before serving from the cache the state re-reads those versions (at most once
per `check_interval` seconds), so data loaded or models trained by *another*
process (`epl load`, `epl train`, a second worker) become visible without a
restart. Endpoints run in a thread pool, so loads are guarded by a lock.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

import pandas as pd
from sqlalchemy import func, select

from epl.api.limits import ComputeGate, TokenBucket
from epl.core.config import Settings
from epl.data.db import session_scope
from epl.data.repository import match_frame
from epl.data.schema import DataVersion
from epl.models.base import Forecaster
from epl.models.registry import ModelCard, ModelRegistry

T = TypeVar("T")

MEMO_SIZE = 512  # derived responses kept per process (LRU), so memory stays bounded whatever the query mix


class AppState:
    def __init__(self, settings: Settings, check_interval: float = 1.0) -> None:
        self.settings = settings
        self.registry = ModelRegistry(settings.artifacts_dir)
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._frame: tuple[int, pd.DataFrame] | None = None
        self._models: dict[str, tuple[Forecaster, ModelCard]] = {}
        self._model_checked_at: dict[str, float] = {}
        self._memo: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._version: int | None = None
        self._checked_at = float("-inf")
        # Resource controls for uncached simulations (see epl.api.limits).
        self.simulation_limiter = TokenBucket(settings.simulation_rate_per_minute)
        self.simulation_gate = ComputeGate(
            settings.simulation_max_concurrency, settings.simulation_queue_timeout_seconds
        )

    # ------------------------------------------------------------------ versions
    def data_version(self) -> int:
        """Latest persisted data version (0 before the first load), re-read at most every `check_interval`."""
        with self._lock:
            now = time.monotonic()
            if self._version is None or now - self._checked_at >= self.check_interval:
                with session_scope(self.settings.database_url) as session:
                    self._version = int(session.scalar(select(func.max(DataVersion.id))) or 0)
                self._checked_at = now
            return self._version

    # ------------------------------------------------------------------ cached values
    def frame(self) -> pd.DataFrame:
        version = self.data_version()
        with self._lock:
            if self._frame is None or self._frame[0] != version:
                with session_scope(self.settings.database_url) as session:
                    self._frame = (version, match_frame(session))
                self._memo.clear()  # derived results belong to the old data
            return self._frame[1]

    def model(self, name: str) -> tuple[Forecaster, ModelCard]:
        """The latest registered model, re-checking the registry pointer at most every `check_interval`."""
        with self._lock:
            now = time.monotonic()
            cached = self._models.get(name)
            if cached is None or now - self._model_checked_at.get(name, float("-inf")) >= self.check_interval:
                latest = self.registry.latest_version(name)  # raises FileNotFoundError if never trained
                if cached is None or cached[1].version != latest:
                    self._models[name] = self.registry.load(name, latest)
                self._model_checked_at[name] = now
            return self._models[name]

    def memo(self, key: tuple[Any, ...], compute: Callable[[], T]) -> T:
        """Cache a value derived from the current data version (cleared whenever the data changes).

        Callers whose value also depends on a model include the model version in `key`.
        """
        self.frame()  # make sure the cached frame (and so this memo) belongs to the current data version
        full_key = (self.data_version(), *key)
        with self._lock:
            if full_key in self._memo:
                self._memo.move_to_end(full_key)
            else:
                self._memo[full_key] = compute()
                if len(self._memo) > MEMO_SIZE:
                    self._memo.popitem(last=False)
            value: T = self._memo[full_key]
            return value

    def invalidate(self) -> None:
        """Drop everything now (the in-process refresh job calls this; other processes rely on versions)."""
        with self._lock:
            self._frame = None
            self._models.clear()
            self._model_checked_at.clear()
            self._memo.clear()
            self._version = None
