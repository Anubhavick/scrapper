#!/usr/bin/env bash
# One-command local dev environment: Postgres+Redis (docker), migrations,
# the web UI, and the RQ background worker -- everything HOWTO.md's Part A
# start-up steps do by hand, in one shot.
#
# Usage:
#   scripts/dev.sh up      # start everything, print + open the live link
#   scripts/dev.sh down    # stop the web UI + worker, stop the containers
#   scripts/dev.sh status  # what's currently running
#   scripts/dev.sh logs    # tail both background processes' logs
#
# State lives in .dev/ (pid files + logs), gitignored -- safe to delete
# any time everything is stopped.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STATE_DIR=".dev"
LOG_DIR="$STATE_DIR/logs"
UVICORN_PID="$STATE_DIR/uvicorn.pid"
WORKER_PID="$STATE_DIR/worker.pid"
UI_URL="http://127.0.0.1:8000"

mkdir -p "$LOG_DIR"

_is_running() {
  # $1 = pidfile
  [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

_wait_for_docker_health() {
  echo "Waiting for Postgres + Redis to report healthy..."
  local tries=0
  while true; do
    local healthy
    healthy=$(docker compose ps 2>/dev/null | grep -c "(healthy)" || true)
    if [ "$healthy" -ge 2 ]; then
      break
    fi
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
      echo "Still not healthy after 60s -- check 'docker compose ps' / 'docker compose logs'." >&2
      exit 1
    fi
    sleep 1
  done
}

cmd_up() {
  echo "Starting Postgres + Redis..."
  docker compose up -d
  _wait_for_docker_health

  echo "Applying migrations..."
  uv run alembic upgrade head

  if _is_running "$UVICORN_PID"; then
    echo "Web UI already running (pid $(cat "$UVICORN_PID"))."
  else
    echo "Starting web UI..."
    nohup uv run uvicorn leadgen.api.review:app > "$LOG_DIR/uvicorn.log" 2>&1 &
    echo $! > "$UVICORN_PID"
  fi

  if _is_running "$WORKER_PID"; then
    echo "Background worker already running (pid $(cat "$WORKER_PID"))."
  else
    echo "Starting background worker (for the campaigns page's Send button)..."
    # See docs/13: rq's fork-based work-horse crashes on macOS without this.
    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES nohup uv run rq worker leadgen \
      -u redis://localhost:6379/0 > "$LOG_DIR/worker.log" 2>&1 &
    echo $! > "$WORKER_PID"
  fi

  echo "Waiting for the web UI to answer..."
  local tries=0
  until curl -s -o /dev/null "$UI_URL"; do
    tries=$((tries + 1))
    if [ "$tries" -ge 30 ]; then
      echo "Web UI didn't come up after 30s -- check $LOG_DIR/uvicorn.log" >&2
      exit 1
    fi
    sleep 1
  done

  echo
  echo "Ready: $UI_URL"
  if command -v open >/dev/null 2>&1; then
    open "$UI_URL"
  fi
}

cmd_down() {
  for name in uvicorn worker; do
    local pidfile="$STATE_DIR/$name.pid"
    if _is_running "$pidfile"; then
      local pid
      pid="$(cat "$pidfile")"
      kill "$pid" 2>/dev/null && echo "Stopped $name (pid $pid)."
    fi
    rm -f "$pidfile"
  done
  echo "Stopping Postgres + Redis..."
  docker compose stop
}

cmd_status() {
  docker compose ps
  echo
  for name in uvicorn worker; do
    local pidfile="$STATE_DIR/$name.pid"
    if _is_running "$pidfile"; then
      echo "$name: running (pid $(cat "$pidfile"))"
    else
      echo "$name: stopped"
    fi
  done
}

cmd_logs() {
  tail -f "$LOG_DIR/uvicorn.log" "$LOG_DIR/worker.log" 2>/dev/null
}

case "${1:-up}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  *)
    echo "usage: $0 {up|down|status|logs}" >&2
    exit 1
    ;;
esac
