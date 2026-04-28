#!/usr/bin/env bash
#
# StockWarren Supervisor - cross-platform auto-restart wrapper
#
# Use this when systemd/launchd aren't available, or for quick testing.
# Automatically restarts the bot if it crashes, with backoff to prevent
# infinite restart loops.
#
# Usage:
#   ./setup/supervisor.sh                  # run in foreground
#   ./setup/supervisor.sh > supervisor.log 2>&1 &   # run in background
#   tail -f logs/supervisor.log            # follow logs
#
# Stop with: pkill -f "supervisor.sh"

set -euo pipefail

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$PROJECT_DIR"

VENV="$PROJECT_DIR/venv"
LOG_DIR="$PROJECT_DIR/logs"
PIDFILE="$PROJECT_DIR/.supervisor.pid"
SUPERVISOR_LOG="$LOG_DIR/supervisor.log"

mkdir -p "$LOG_DIR"

# Settings
MAX_RESTARTS_PER_HOUR=10
MIN_UPTIME_SECONDS=60     # If process exits within 60s, count as failed start
INITIAL_BACKOFF=5
MAX_BACKOFF=300

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$SUPERVISOR_LOG"
}

# Verify venv exists
if [ ! -d "$VENV" ]; then
    log "ERROR: Python venv not found at $VENV"
    log "Create it with: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Verify .env exists
if [ ! -f "$PROJECT_DIR/.env" ]; then
    log "ERROR: .env not found. Copy .env.example to .env and add your API keys."
    exit 1
fi

# Cleanup on exit
cleanup() {
    log "Supervisor shutting down..."
    if [ -n "${BOT_PID:-}" ] && kill -0 "$BOT_PID" 2>/dev/null; then
        log "Sending SIGTERM to bot (PID $BOT_PID)"
        kill -TERM "$BOT_PID"
        # Give it 30 seconds to clean up
        for i in {1..30}; do
            if ! kill -0 "$BOT_PID" 2>/dev/null; then break; fi
            sleep 1
        done
        if kill -0 "$BOT_PID" 2>/dev/null; then
            log "Bot did not exit, sending SIGKILL"
            kill -KILL "$BOT_PID"
        fi
    fi
    rm -f "$PIDFILE"
    exit 0
}
trap cleanup INT TERM

# Track restarts
RESTART_TIMES=()
BACKOFF=$INITIAL_BACKOFF

log "===== StockWarren Supervisor Starting ====="
log "Project: $PROJECT_DIR"
log "Python: $VENV/bin/python"
log "PID: $$"
echo $$ > "$PIDFILE"

while true; do
    # Clean up old restart timestamps (older than 1 hour)
    NOW=$(date +%s)
    NEW_TIMES=()
    for t in "${RESTART_TIMES[@]:-}"; do
        if [ $((NOW - t)) -lt 3600 ]; then
            NEW_TIMES+=("$t")
        fi
    done
    RESTART_TIMES=("${NEW_TIMES[@]:-}")

    # Check restart rate limit
    if [ "${#RESTART_TIMES[@]}" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
        log "ERROR: Too many restarts (${#RESTART_TIMES[@]} in last hour). Sleeping 1h."
        sleep 3600
        RESTART_TIMES=()
    fi

    # Start the bot
    log "Starting bot..."
    START_TIME=$(date +%s)

    "$VENV/bin/python" "$PROJECT_DIR/main.py" &
    BOT_PID=$!
    log "Bot started with PID $BOT_PID"

    # Wait for it to exit
    set +e
    wait "$BOT_PID"
    EXIT_CODE=$?
    set -e

    END_TIME=$(date +%s)
    UPTIME=$((END_TIME - START_TIME))

    log "Bot exited with code $EXIT_CODE after ${UPTIME}s uptime"

    # Clean exit (e.g., user killed it) - stop supervising
    if [ "$EXIT_CODE" -eq 0 ]; then
        log "Clean exit, stopping supervisor"
        cleanup
    fi

    # Reset backoff if it ran successfully for a while
    if [ "$UPTIME" -gt "$MIN_UPTIME_SECONDS" ]; then
        log "Bot ran for ${UPTIME}s, resetting backoff"
        BACKOFF=$INITIAL_BACKOFF
    else
        # Failed quickly - increase backoff
        log "Bot failed within ${UPTIME}s, increasing backoff to ${BACKOFF}s"
    fi

    RESTART_TIMES+=("$NOW")

    log "Waiting ${BACKOFF}s before restart..."
    sleep "$BACKOFF"

    # Exponential backoff (capped)
    BACKOFF=$((BACKOFF * 2))
    if [ "$BACKOFF" -gt "$MAX_BACKOFF" ]; then
        BACKOFF=$MAX_BACKOFF
    fi
done
