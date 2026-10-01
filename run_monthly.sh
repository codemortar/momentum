#!/usr/bin/env bash
#
# Monthly pre-flight wrapper. The signal drives real money, so this runs the
# full test suite first and only computes the signal if the suite is green. A
# red suite aborts the run untouched — better a missed month than acting on code
# a test says is broken.
#
# Everything else is deliberately non-fatal. The failure that actually hurt was
# silence: a transient yfinance error aborted the whole run and no email went
# out, so the first sign of trouble was an email that never arrived. Now the
# network steps warn and carry on with cached data, the slow one cannot hang
# forever, and a genuine failure sends a message saying so.
#
# Usage: run_monthly.sh [universe]   (universe defaults to uk)
#
# Expects SENDGRID_* / MOMENTUM_* to already be in the environment (the cron job
# sources ~/momentum.env; launchd supplies them via EnvironmentVariables).

set -uo pipefail

cd "$(dirname "$0")"

universe="${1:-uk}"
py=.venv/bin/python

# Seconds before a network step is assumed hung. Generous: a full screener
# refresh legitimately takes minutes.
FETCH_TIMEOUT=${MOMENTUM_FETCH_TIMEOUT:-600}
SCREEN_TIMEOUT=${MOMENTUM_SCREEN_TIMEOUT:-1800}

# `timeout` is coreutils, so it is present on the Linux server and usually
# absent on macOS. Run unbounded rather than refusing to work locally.
run_bounded() {
    local seconds="$1"; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$seconds" "$@"
    else
        "$@"
    fi
}

# Best-effort shout for help. Uses the same SendGrid config as the signal, so it
# is useless when email itself is broken — but it covers every other failure,
# which is most of them.
notify_failure() {
    local stage="$1"
    "$py" - "$stage" <<'PY' >/dev/null 2>&1 || true
import sys
from momentum.signal import email_config_from_env, send_email_sendgrid

cfg = email_config_from_env()
if cfg is not None:
    send_email_sendgrid(
        cfg,
        "Momentum: monthly run FAILED",
        f"The monthly run failed at: {sys.argv[1]}.\n\n"
        "No signal was produced this month. The reason is in "
        "momentum-cron.log on the server.",
    )
PY
}

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') monthly run (universe=$universe) ==="

echo "Pre-flight: running test suite..."
if ! .venv/bin/pytest -q; then
    echo "TESTS FAILED — aborting. Not fetching or signalling this run." >&2
    notify_failure "the test suite (code is broken; signal deliberately withheld)"
    exit 1
fi

# Refreshing prices is an improvement, not a precondition: the cache already
# holds a full history, and last month's closes are enough to compute a signal.
echo "Tests green. Refreshing prices..."
if ! run_bounded "$FETCH_TIMEOUT" "$py" -m momentum fetch --universe "$universe" --refresh; then
    echo "Price refresh failed or timed out; continuing on cached prices." >&2
fi

# The email's screen section reads the cache, so refresh it here (slow, network)
# rather than in the delivery path, where a multi-minute fetch has no business.
if [ -n "${MOMENTUM_SCREEN:-}" ]; then
    echo "Refreshing screener fundamentals..."
    if ! run_bounded "$SCREEN_TIMEOUT" "$py" -m momentum screen --universe "$universe" --refresh --top 5; then
        echo "Screen refresh failed or timed out; the email will use the previous cache." >&2
    fi
fi

echo "Computing and sending the signal..."
if ! "$py" -m momentum signal --universe "$universe" --notify; then
    echo "SIGNAL FAILED — no email sent this month." >&2
    notify_failure "computing or sending the signal"
    exit 1
fi

# Off-server copy of state/; best-effort, since the files remain the record.
if ! "$py" -m momentum sync; then
    echo "State sync failed; files on disk are unaffected." >&2
fi

echo "=== done $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
