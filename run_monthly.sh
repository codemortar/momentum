#!/usr/bin/env bash
#
# Monthly pre-flight wrapper. The signal drives real money, so this runs the
# full test suite first and only fetches prices and computes the signal if the
# suite is green. A red suite aborts the run untouched — better a missed month
# than acting on code a test says is broken.
#
# Usage: run_monthly.sh [universe]   (universe defaults to uk)
#
# Expects SENDGRID_* / MOMENTUM_* to already be in the environment (the cron job
# sources ~/momentum.env; launchd supplies them via EnvironmentVariables).

set -euo pipefail

cd "$(dirname "$0")"

universe="${1:-uk}"
py=.venv/bin/python

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') monthly run (universe=$universe) ==="

echo "Pre-flight: running test suite..."
if ! .venv/bin/pytest -q; then
    echo "TESTS FAILED — aborting. Not fetching or signalling this run." >&2
    exit 1
fi

echo "Tests green. Refreshing prices and computing signal..."
"$py" -m momentum fetch  --universe "$universe" --refresh
"$py" -m momentum signal --universe "$universe" --notify
