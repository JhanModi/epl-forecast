# 0006. Serve a goals model; keep classifiers as challengers

Date: 2026-10-10 · Status: accepted

## Context
The original model classified home/draw/away with logistic regression on rolling features. The dashboard also
needs scoreline probabilities, for markets and for season simulation.

## Options
Feature classifiers (logistic regression, gradient boosting, an Elo ordinal logit) against a Dixon-Coles
bivariate Poisson goals model, or an ensemble of them.

## Decision
Production serves the Dixon-Coles model. It has time decay, a Gaussian prior on team strengths and a learned
promoted-team prior, and it is fitted by penalised maximum likelihood with an analytic gradient. The classifiers
and the ensemble stay in the walk-forward backtest as challengers.

## Consequences
- One fitted object provides result, over/under, both-teams-to-score and clean-sheet probabilities, and feeds the
  Monte Carlo simulator.
- Model rankings differ between datasets. On real Premier League data (measured locally) Dixon-Coles led both the
  tuning and the holdout season, with the top models statistically tied. On the demo league, Dixon-Coles leads the
  tuning season; on the holdout, logistic regression scores slightly better (RPS 0.1973 vs 0.2002, paired
  p = 0.44: a tie), and both capture most of the gap between the baseline and the oracle ceiling. The choice rests on scoreline
  modelling and tuning-season results, not on a claim of superiority; the README reports both datasets
  separately.
- A refit takes milliseconds (warm-started), so weekly walk-forward refits and on-demand historical
  simulations are cheap.
