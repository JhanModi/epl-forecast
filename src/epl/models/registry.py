"""File-based model registry.

Each trained model is saved under ``artifacts/models/<name>/<version>/`` with a
*model card* recording what it was trained on (a data fingerprint and the last
kickoff), its hyperparameters, the code version and its backtest metrics. A
``latest.json`` pointer per model is swapped atomically, so a reader never sees
a half-written model.

Artifacts are pickles: only load ones this project produced.
"""

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from epl import __version__
from epl.features.form import fingerprint
from epl.models.base import Forecaster


@dataclass
class ModelCard:
    name: str
    version: str
    description: str
    created_at: str
    trained_through: str
    n_matches: int
    data_fingerprint: str
    params: dict[str, Any]
    package_version: str = __version__
    git_commit: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)  # atomic on POSIX and Windows


class ModelRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root / "models"

    def save(
        self, model: Forecaster, history: pd.DataFrame, metrics: dict[str, Any] | None = None
    ) -> ModelCard:
        data_hash = fingerprint(history[["match_id", "home_goals", "away_goals"]].reset_index(drop=True))
        now = datetime.now(UTC)
        card = ModelCard(
            name=model.name,
            version=f"{now:%Y%m%dT%H%M%S}-{data_hash[:8]}",
            description=model.description,
            created_at=now.isoformat(timespec="seconds"),
            trained_through=str(history["kickoff"].max()),
            n_matches=len(history),
            data_fingerprint=data_hash,
            params=model.params(),
            git_commit=_git_commit(),
            metrics=metrics or {},
        )
        directory = self.root / model.name / card.version
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, directory / "model.joblib", compress=3)
        _atomic_write(directory / "card.json", json.dumps(card.to_dict(), indent=2, default=str))
        _atomic_write(self.root / model.name / "latest.json", json.dumps({"version": card.version}))
        return card

    def latest_version(self, name: str) -> str:
        pointer = self.root / name / "latest.json"
        if not pointer.exists():
            raise FileNotFoundError(f"no trained {name!r} model in {self.root}; run `epl train`")
        return str(json.loads(pointer.read_text(encoding="utf-8"))["version"])

    def card(self, name: str, version: str | None = None) -> ModelCard:
        version = version or self.latest_version(name)
        data = json.loads((self.root / name / version / "card.json").read_text(encoding="utf-8"))
        return ModelCard(**data)

    def load(self, name: str, version: str | None = None) -> tuple[Forecaster, ModelCard]:
        card = self.card(name, version)
        model = joblib.load(self.root / name / card.version / "model.joblib")
        return model, card

    def latest_cards(self) -> list[ModelCard]:
        if not self.root.exists():
            return []
        return [self.card(p.parent.name) for p in sorted(self.root.glob("*/latest.json"))]
