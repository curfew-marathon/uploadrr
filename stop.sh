#!/usr/bin/env bash
#
# stop.sh - take uploadrr down.
#
# Usage:
#   ./stop.sh              Stop and remove the container
#   ./stop.sh --adb        Also stop the host adb server (adb-server.service)
#   ./stop.sh --images     Also remove the image pulled for this project
#
# The adb server is left running by default - other tools on the host may use it.
#
set -euo pipefail
cd "$(dirname "$0")"

log()  { printf '\033[1;36m> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31mx %s\033[0m\n' "$*" >&2; }

STOP_ADB=0
DROP_IMAGES=0

for arg in "$@"; do
  case "$arg" in
    --adb)      STOP_ADB=1 ;;
    --images)   DROP_IMAGES=1 ;;
    -h|--help)  sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "unknown option '$arg' (try --help)"; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1      || { err "docker is not installed or not on PATH."; exit 1; }
docker info >/dev/null 2>&1            || { err "Docker daemon is not running."; exit 1; }
docker compose version >/dev/null 2>&1 || { err "'docker compose' v2 is required."; exit 1; }

DOWN_ARGS=(down --remove-orphans)
[ "$DROP_IMAGES" = "1" ] && DOWN_ARGS+=(--rmi local)

log "Stopping uploadrr..."
docker compose "${DOWN_ARGS[@]}"

if [ "$STOP_ADB" = "1" ]; then
  if systemctl --user cat adb-server.service >/dev/null 2>&1; then
    systemctl --user stop adb-server.service && log "adb server: stopped"
  else
    warn "adb server not managed by systemd --user - leaving it alone."
  fi
fi

log "Done. Any tar files left in the archive dirs are retried on the next ./start.sh."
