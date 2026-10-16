# Demo walkthrough (about 10 minutes)

Everything below runs offline on the bundled synthetic demo league. No recording is included; each step lists what
you should see.

## 0. Set up
```bash
uv sync --all-extras
uv run epl build --skip-backtest     # database + models from data/demo (seconds); the backtest report is committed
uv run epl serve                     # http://127.0.0.1:8000
```
Open http://127.0.0.1:8000. A banner says the data is a **synthetic demo league**. The footer names the dataset and
its license.

## 1. Core user flow (dashboard)
| Step | Where | What to look for |
| --- | --- | --- |
| 1 | Overview | Next fixtures with home/draw/away bars, expected goals, likeliest score; the projected table with title, top-4 and relegation odds |
| 2 | Season simulator → Season 2025/26 → *Before matchweek 20* | The model refitted on data before matchweek 20. Outlined cells mark where each club actually finished; the tile shows the champion's title odds at that point |
| 3 | Any chart → **Table** (or focus *Chart* and press →) | The same numbers as an accessible table; the toggle is keyboard-operable |
| 4 | Match lab → pick two clubs → ⇄ Swap | The scoreline grid and markets for the reversed fixture |
| 5 | Model evaluation → 2025/26 · holdout | The leaderboard with 95% CIs. The **oracle** row is the ceiling (true probabilities); calibration, weekly RPS and confusion matrix all have table views |
| 6 | Model evaluation → **Forecast track record** | One row per match: the latest forecast logged before kickoff by `epl build` / `epl predict`. Previous/Next page through it. On the static demo data these rows stay pending (no score, RPS "—"); rows are scored once results are loaded |

## 2. Interfaces (API)
```bash
curl -s localhost:8000/api/v1/meta
curl -s "localhost:8000/api/v1/predictions/matchup?home=Ashbury%20Rovers&away=Calder%20Vale"
```
```bash
curl -s "localhost:8000/api/v1/predictions/log?limit=5"     # the track record, newest kickoff first
curl -s -o /dev/null -w "%{http_code}\n" "localhost:8000/api/v1/predictions/log?limit=0"   # 422
```
Interactive docs are at http://127.0.0.1:8000/api/v1/docs, and the contract is `docs/openapi.json`.

## 3. Bounded expensive work
```bash
curl -s -o /dev/null -w "%{http_code}\n" "localhost:8000/api/v1/seasons/2025/simulation?runs=50000"   # 422: not an allowed size
for w in $(seq 2 25); do curl -s -o /dev/null -w "%{http_code} " "localhost:8000/api/v1/seasons/2025/simulation?runs=1000&as_of_matchweek=$w"; done; echo
```
The second command shows `200` for each new computation until the per-client budget (20 a minute) is spent, then
`429`. Repeating a request that is already cached returns `200` without spending budget.

## 4. Freshness across processes
With the server still running, in a second terminal:
```bash
EPL_DATASET=demo uv run epl load        # or: uv run epl train
curl -s localhost:8000/api/v1/meta      # data_version / model_version reflect the change within about 1 s, no restart
```
An identical reload leaves `data_version` unchanged, because loads are idempotent.

## 5. Admin jobs and secrets
```bash
EPL_ADMIN_TOKEN=change-me uv run epl serve      # refuses to start: weak token
```
Unset, the admin endpoints return 403. With a strong token (`python -c "import secrets; print(secrets.token_urlsafe(32))"`),
`POST /api/v1/admin/refresh` returns 202 with a job id; a second POST while it runs returns 409. Job status is
stored in the database and survives a restart.

## 6. Tests and the distribution guard
```bash
uv run pytest --cov                                # backend: unit, integration, adversarial, reliability
cd frontend && npm test                            # frontend: unit + user-flow tests
uv run python scripts/check_distribution.py --docker   # no restricted data in Git or the Docker build context
```

## 7. Production-like run
```bash
docker compose up --build        # API + dashboard on PostgreSQL, http://localhost:8000
```
