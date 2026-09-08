#!/usr/bin/env bash
#
# stop.sh - take uploadrr down.
#
# Usage:
#   ./stop.sh              Stop and remove the container
#   ./stop.sh --volumes    Also remove anonymous volumes (uploadrr declares none)
#   ./stop.sh --yes        Skip the confirmation prompt for --volumes
#   ./stop.sh --images     Also remove this project's ghcr.io/curfew-marathon/* image
#   ./stop.sh --adb        Also stop the host adb server (adb-server.service)
#
#   -h, --help             Show this help
#
# The adb server is left running by default - other tools on the host may use it.
#
# canonical-run-script: v1  (reference: curfew-marathon/hivemind)
# Everything except this header block and the sections fenced
# "# >>> project-specific" ... "# <<< project-specific" is byte-identical across
# hivemind / importrr / uploadrr / prometheus. Edit the reference first, then propagate.
#
set -euo pipefail
cd "$(dirname "$0")"

log()  { printf '\033[1;36m> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31mx %s\033[0m\n' "$*" >&2; }

DROP_VOLUMES=0
DROP_IMAGES=0
ASSUME_YES=0
# >>> project-specific
STOP_ADB=0
# <<< project-specific

for arg in "$@"; do
  case "$arg" in
    --volumes|-v) DROP_VOLUMES=1 ;;
    --images)     DROP_IMAGES=1 ;;
    --yes|-y)     ASSUME_YES=1 ;;
    # >>> project-specific
    --adb)        STOP_ADB=1 ;;
    # <<< project-specific
    -h|--help)    sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "unknown option '$arg' (try --help)"; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1      || { err "docker is not installed or not on PATH."; exit 1; }
docker info >/dev/null 2>&1            || { err "Docker daemon is not running."; exit 1; }
docker compose version >/dev/null 2>&1 || { err "'docker compose' v2 is required."; exit 1; }

# Resolve first-party images before `down` (config works in any state). Only
# ghcr.io/curfew-marathon/* - never the shared base images.
FIRST_PARTY="$(docker compose config --images 2>/dev/null | grep -E '^ghcr\.io/curfew-marathon/' | sort -u || true)"

DOWN_ARGS=(down --remove-orphans)

if [ "$DROP_VOLUMES" = "1" ]; then
  if [ "$ASSUME_YES" != "1" ]; then
    # >>> project-specific
    warn "uploadrr declares no named volumes, so --volumes only prunes anonymous ones."
    warn "Your bind-mounted /config and /data on the host are never touched."
    # <<< project-specific
    read -r -p "Type 'wipe' to confirm: " reply
    [ "$reply" = "wipe" ] || { log "Aborted. Nothing was deleted."; exit 0; }
  fi
  DOWN_ARGS+=(--volumes)
fi

log "Stopping the stack..."
docker compose "${DOWN_ARGS[@]}"

if [ "$DROP_IMAGES" = "1" ]; then
  if [ -n "$FIRST_PARTY" ]; then
    log "Removing first-party images:"
    printf '  %s\n' $FIRST_PARTY
    # shellcheck disable=SC2086
    if ! docker image rm $FIRST_PARTY; then
      warn "some images could not be removed (in use by another container?) - see above."
    fi
  else
    warn "no ghcr.io/curfew-marathon/* image in this project - nothing to remove."
  fi
fi

# >>> project-specific
if [ "$STOP_ADB" = "1" ]; then
  if systemctl --user cat adb-server.service >/dev/null 2>&1; then
    systemctl --user stop adb-server.service && log "adb server: stopped"
  else
    warn "adb server not managed by systemd --user - leaving it alone."
  fi
fi
# <<< project-specific

log "Done."
# >>> project-specific
echo "  Any tar files left in the archive dirs are retried on the next ./start.sh."
# <<< project-specific
