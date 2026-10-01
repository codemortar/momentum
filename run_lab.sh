#!/usr/bin/env bash
#
# Daily paper-lab cycle for cron: trade, then mirror state/ to Postgres.
# Expects ~/momentum.env to be sourced first (for MOMENTUM_DATABASE_URL).

set -uo pipefail
cd "$(dirname "$0")"
py=.venv/bin/python

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') lab run ==="
"$py" -m momentum lab run || echo "Lab run failed; syncing whatever was recorded." >&2
"$py" -m momentum sync || echo "State sync failed; files on disk are unaffected." >&2
