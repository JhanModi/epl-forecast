#!/bin/sh
# Container start: migrate and (re)load the snapshots, train only if no model exists, then serve.
# Every step is idempotent, so restarts are safe and a fresh PostgreSQL gets populated.
set -eu

epl load
epl train --if-missing
exec epl serve --host 0.0.0.0 --port "${PORT:-8000}"
