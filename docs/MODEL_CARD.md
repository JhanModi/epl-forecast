# Model card: Dixon-Coles production model

## Overview
| | |
| --- | --- |
| Model | Time-weighted Dixon-Coles bivariate Poisson goals model with a Gaussian prior on team strengths and a promoted-team prior |
| Implementation | `src/epl/models/dixon_coles.py` (penalised maximum likelihood, analytic gradient checked against finite differences, L-BFGS-B) |
| Hyperparameters | Per dataset, tuned on its 2024/25 season (`data/artifacts/<dataset>/hyperparameters.json`) |
| Output | A 0–10 × 0–10 scoreline probability matrix per fixture, and everything derived from it (result probabilities, markets, season simulations) |
| Versioning | Model cards with data fingerprints in the registry (`data/artifacts/<dataset>/models/`) |

## Intended use
- Probabilistic, pre-kickoff forecasts of match results and scorelines, and season outcomes via Monte Carlo.
- Demonstrating a reproducible, leakage-free forecasting workflow.

**Out of scope:** betting or any financial decision. The model has not been compared with bookmaker odds.

## Data
| Dataset | Status |
| --- | --- |
| Demo league (default) | Synthetic. Shareable, and the results below are reproducible from the repository. |
| openfootball | Public domain, results only. Evaluated: on the 2025/26 holdout, RPS 0.2130 against a base-rate baseline of 0.2280 (p = 0.006), statistically tied with gradient boosting. Results and limitations are in the README. |
| Premier League site | Evaluated locally only. Data, models and reports are not distributed under the site's terms, and no figures are published (see the README). |

Inputs are the teams, kickoff date and final score of earlier matches only. In-match statistics are used by the
challenger classifiers, not by this model.

## Evaluation on the demo league
Holdout season 2025/26 (380 matches, untouched by tuning):

| Model | RPS ↓ | 95% CI | Log loss ↓ | Accuracy | ECE | vs baseline | Share of achievable gain |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: |
| Oracle (true probabilities) | 0.1949 | 0.182–0.209 | 0.923 | 57.1% | 0.035 | p < 0.001 | — |
| Logistic regression | 0.1973 | 0.183–0.212 | 0.936 | 57.6% | 0.050 | p < 0.001 | 94% |
| Ensemble | 0.1991 | 0.185–0.213 | 0.939 | 59.2% | 0.035 | p < 0.001 | 90% |
| **Dixon-Coles (production)** | 0.2002 | 0.186–0.215 | 0.944 | 57.4% | 0.033 | p < 0.001 | 88% |
| Gradient boosting | 0.2008 | 0.188–0.214 | 0.951 | 57.9% | 0.034 | p < 0.001 | 86% |
| Elo → ordered logit | 0.2063 | 0.191–0.221 | 0.960 | 56.6% | 0.049 | p < 0.001 | 73% |
| Baseline (base rates) | 0.2378 | 0.232–0.244 | 1.045 | 46.1% | 0.011 | — | 0% |

380 matches, season 2025/26; generated 2026-10-11T04:15:55+00:00 from `demo` data.

The tuning season 2024/25 is in `data/artifacts/demo/backtest/report.json`; there Dixon-Coles is the best model and
captures 96% of the achievable gain. On the holdout, its gap to logistic regression (0.003 RPS) is not significant
(paired p = 0.44). Holdout calibration error is 0.033.

The protocol is a weekly walk-forward: the model is refitted before each week on all earlier matches. The CIs
are percentile bootstraps over matches, the p-value is a paired t-test on per-match RPS against the base-rate
baseline, and ECE is the expected calibration error averaged over the three outcomes. The **oracle** row uses the
probabilities the synthetic league was generated from; "share of achievable gain" is
(baseline − model) / (baseline − oracle) in RPS.

## Caveats
- **Synthetic data.** The demo measures the method, not real-world skill. Its generator was calibrated on three
  aggregate Premier League statistics and under-produces draws.
- **One real holdout season.** The openfootball evaluation rests on a single untouched season (380 matches), and
  the gain over the baseline there was well below the tuning season's. There is no bookmaker comparison.
- **Draws.** A calibrated model rarely makes a draw its single most likely outcome. Judge it by RPS and log loss,
  not accuracy.
- **Promoted teams.** Teams with no top-flight history start from a learned promoted-team prior; their early
  forecasts carry the most uncertainty.
- **Static strengths in simulations.** Season simulations hold team strengths fixed at the cutoff.
- **Small samples.** 380 matches per season: differences of a few thousandths of RPS between models are noise
  (see the paired tests in the report).

## Monitoring
Each refresh logs the production model's forecasts for the coming week, with the model version, before kickoff.
`GET /api/v1/predictions/live` scores the latest pre-kickoff forecast of each finished match.
