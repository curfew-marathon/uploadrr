#!/usr/bin/env bash
#
# start.sh - bring uploadrr up.
#
# Usage:
#   ./start.sh              Ensure the host adb server is running, then start the stack
#   ./start.sh --pull       Pull the latest image before starting
#   ./start.sh --no-adb     Skip the adb server check (it is managed elsewhere)
#   ./start.sh --logs       Follow container logs once it is up
#
# Flags may be combined, e.g. ./start.sh --pull --logs
#
set -euo pipefail
cd "$(dirname "$0")"

log()  { printf '\033[1;36m> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31mx %s\033[0m\n' "$*" >&2; }

ADB_SERVICE="adb-server.service"

# ── args ─────────────────────────────────────────────────────────────────────
PULL=0
CHECK_ADB=1
FOLLOW_LOGS=0

for arg in "$@"; do
  case "$arg" in
    --pull)     PULL=1 ;;
    --no-adb)   CHECK_ADB=0 ;;
    --logs)     FOLLOW_LOGS=1 ;;
    -h|--help)  sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "unknown option '$arg' (try --help)"; exit 2 ;;
  esac
done

# ── preflight ────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1      || { err "docker is not installed or not on PATH."; exit 1; }
docker info >/dev/null 2>&1            || { err "Docker daemon is not running."; exit 1; }
docker compose version >/dev/null 2>&1 || { err "'docker compose' v2 is required."; exit 1; }

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    warn ".env not found - creating it from .env.example. Set UPLOADRR_DATA_DIR for real use."
    cp .env.example .env
  else
    warn ".env and .env.example both missing - creating an empty .env (compose defaults apply)."
    touch .env
  fi
fi

docker compose config --quiet || { err "docker-compose.yml failed validation."; exit 1; }

# ── host adb server ──────────────────────────────────────────────────────────
if [ "$CHECK_ADB" = "1" ]; then
  if systemctl --user cat "$ADB_SERVICE" >/dev/null 2>&1; then
    systemctl --user start "$ADB_SERVICE" || true   # no-op if already running; real check below
    if systemctl --user is-active --quiet "$ADB_SERVICE"; then
      log "adb server: $ADB_SERVICE active"
    else
      err "$ADB_SERVICE failed to start - check: systemctl --user status $ADB_SERVICE"
      exit 1
    fi
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
      warn "linger is off for $USER - the adb server will stop when you log out."
      warn "fix: sudo loginctl enable-linger $USER"
    fi
  elif command -v adb >/dev/null 2>&1 && adb start-server >/dev/null 2>&1; then
    warn "adb server: $ADB_SERVICE not installed; started a plain adb server (not supervised)."
    warn "for unattended use, install deploy/$ADB_SERVICE - see README > ADB Server Setup."
  else
    err "no adb server available and none could be started."
    err "install deploy/$ADB_SERVICE (README > ADB Server Setup) or run: adb start-server"
    exit 1
  fi
fi

# ── up ───────────────────────────────────────────────────────────────────────
[ "$PULL" = "1" ] && { log "Pulling latest image..."; docker compose pull; }

log "Bringing uploadrr up..."
docker compose up -d --remove-orphans

# ── verify it stays up (no healthcheck; catch a startup crash loop) ─────────
log "Checking the container starts cleanly..."
_state()    { docker inspect -f '{{.State.Status}}' uploadrr 2>/dev/null || echo missing; }
_restarts() { docker inspect -f '{{.RestartCount}}' uploadrr 2>/dev/null || echo 0; }

sleep 3
if [ "$(_state)" != "running" ]; then
  err "container is '$(_state)' just after start - check 'docker compose logs'"
  exit 1
fi
r1="$(_restarts)"
sleep 7
if [ "$(_restarts)" -gt "$r1" ] || [ "$(_state)" != "running" ]; then
  err "container is restart-looping (crash on startup) - check 'docker compose logs'"
  exit 1
fi
log "Container up."

# ── status ──────────────────────────────────────────────────────────────────
echo
docker compose ps
metrics_port="$(docker compose exec -T uploadrr printenv METRICS_PORT 2>/dev/null | tr -d '\r' || true)"
adb_line="adb not on PATH"
if command -v adb >/dev/null 2>&1; then
  adb_line="$(adb devices | tail -n +2 | sed '/^[[:space:]]*$/d' | tr -s ' \t' ' ' | paste -sd', ' - || true)"
  [ -n "$adb_line" ] || adb_line="(no devices)"
fi
cat <<EOF

  Metrics    http://localhost:${metrics_port:-9200}/metrics
  adb        ${adb_line}

  Follow logs:     docker compose logs -f
  Stop:            ./stop.sh
EOF

if [ "$FOLLOW_LOGS" = "1" ]; then
  echo
  log "Following logs (Ctrl+C detaches; the container keeps running)..."
  exec docker compose logs -f
fi
