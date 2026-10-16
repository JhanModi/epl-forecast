"""Render a backtest report as a Markdown table, so documentation numbers come straight from the artifact.

uv run python scripts/report_table.py                         # demo report, holdout season
uv run python scripts/report_table.py --season 2024 --report data/artifacts/demo/backtest/report.json
"""

import argparse
import io
import json
import sys
from pathlib import Path

LABELS = {
    "oracle": "Oracle (true probabilities)",
    "prior": "Baseline (base rates)",
    "elo_logit": "Elo → ordered logit",
    "logistic_regression": "Logistic regression",
    "gradient_boosting": "Gradient boosting",
    "dixon_coles": "Dixon-Coles",
    "ensemble": "Ensemble",
}


def table(report: dict, season: str, production: str = "dixon_coles") -> str:
    data = report["seasons"][season]
    models = data["models"]
    baseline = models[data["reference"]]["metrics"]["rps"]["mean"]
    ceiling = models[data["ceiling"]]["metrics"]["rps"]["mean"] if data.get("ceiling") else None
    header = (
        "| Model | RPS ↓ | 95% CI | Log loss ↓ | Accuracy | ECE | vs baseline | Share of achievable gain |"
    )
    rows = [header, "| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: |"]
    for name in data["ranking"]:
        m = models[name]
        rps = m["metrics"]["rps"]
        p = m["vs_reference"]["p_value"]
        versus = "—" if name == data["reference"] else ("p < 0.001" if p < 0.001 else f"p = {p:.3f}")
        share = (
            "—"
            if ceiling is None or name == data["ceiling"]
            else f"{(baseline - rps['mean']) / (baseline - ceiling):.0%}"
        )
        label = LABELS.get(name, name) + (" (production)" if name == production else "")
        if name == production:
            label = f"**{label}**"
        rows.append(
            f"| {label} | {rps['mean']:.4f} | {rps['low']:.3f}–{rps['high']:.3f} | "
            f"{m['metrics']['log_loss']['mean']:.3f} | {m['metrics']['accuracy']['mean']:.1%} | "
            f"{m['calibration']['ece']:.3f} | {versus} | {share} |"
        )
    note = (
        f"\n\n{data['n_matches']} matches, season {season}/{(int(season) + 1) % 100:02d}; "
        f"generated {report['generated_at']} from `{report.get('dataset', {}).get('name', '?')}` data."
    )
    return "\n".join(rows) + note


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=Path("data/artifacts/demo/backtest/report.json"))
    parser.add_argument("--season", default=None, help="defaults to the holdout season")
    args = parser.parse_args()
    if isinstance(
        sys.stdout, io.TextIOWrapper
    ):  # tables use arrows and dashes; Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    print(table(report, args.season or str(report["holdout_season"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
