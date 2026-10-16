# 0002. Point-in-time features through one code path

Date: 2026-10-10 · Status: accepted

## Context
Rolling features can easily include the match being predicted (leakage). Live features can also be computed
differently from training features (training/serving skew). Either flaw makes a backtest look better than
production would be.

## Options
1. Separate feature code for training (vectorised `shift().rolling()`) and serving (the latest state per team).
2. **One function** that computes each team's state *after* every played match, then attaches to every fixture
   the state strictly before its kickoff with `pandas.merge_asof(..., allow_exact_matches=False)`.

## Decision
Option 2 (`epl.features.form.build_features`). Unplayed fixtures go through the same function; their results are
simply absent.

## Consequences
- Leakage becomes a property of one function, and tests check it directly. Changing a result never changes that
  match's own features, and training and serving features are identical.
- The walk-forward harness adds a second guard: a test double asserts that no model ever sees a match after its
  cutoff.
- Rebuilding features for each refit costs about 0.5 s. An LRU cache keyed by a content hash shares them across
  models within a backtest step.
