#!/usr/bin/env bash
# Installs Scout's systemd timers on the box, or removes them.
#
#   cd /srv/scout/code
#   sudo ops/systemd/install.sh            install or update, and start the timers
#   sudo ops/systemd/install.sh --remove   stop the timers and remove all Scout units
#
# The units run as the account that called sudo (shaw2012), never as root. Run it
# again after a `git pull` that changes ops/systemd/; units no longer in the repo are
# stopped and removed.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run it with sudo: sudo $0 $*" >&2
  exit 1
fi
RUN_AS=${SUDO_USER:-}
if [[ -z $RUN_AS || $RUN_AS == root ]]; then
  echo "Run it with sudo from your own account (not as root), so the timers run as you." >&2
  exit 1
fi
SCOUT_HOME=${SCOUT_HOME:-/srv/scout}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DEST=${SCOUT_SYSTEMD_DIR:-/etc/systemd/system}  # another folder only for testing
DOCKER=$(command -v docker || true)
if [[ -z $DOCKER ]]; then
  echo "docker not found on PATH" >&2
  exit 1
fi

installed() { find "$DEST" -maxdepth 1 -type f \( -name 'scout-*.service' -o -name 'scout-*.timer' \) -printf '%f\n' | sort; }

stop_and_remove() {  # unit file names
  local u
  for u in "$@"; do
    [[ $u == *.timer ]] && systemctl disable --now "$u" >/dev/null 2>&1 || true
  done
  for u in "$@"; do
    rm -f "$DEST/$u"
    echo "removed $u"
  done
}

if [[ ${1:-} == --remove ]]; then
  mapfile -t units < <(installed)
  stop_and_remove "${units[@]}"
  systemctl daemon-reload
  exit 0
fi

mapfile -t wanted < <(find "$HERE" -maxdepth 1 -type f \( -name 'scout-*.service' -o -name 'scout-*.timer' \) -printf '%f\n' | sort)
mapfile -t stale < <(comm -23 <(installed) <(printf '%s\n' "${wanted[@]}"))
if ((${#stale[@]})); then
  stop_and_remove "${stale[@]}"
fi

for u in "${wanted[@]}"; do
  sed -e "s|@USER@|$RUN_AS|g" -e "s|@SCOUT_HOME@|$SCOUT_HOME|g" -e "s|@DOCKER@|$DOCKER|g" \
    "$HERE/$u" > "$DEST/$u"
  chmod 644 "$DEST/$u"
done
for u in "${wanted[@]}"; do
  systemd-analyze verify "$DEST/$u" 2>&1 | grep -v "^$" || true
done
systemctl daemon-reload
for u in "${wanted[@]}"; do
  if [[ $u == *.timer ]]; then
    systemctl enable "$u" >/dev/null 2>&1
    systemctl restart "$u"
  fi
done
echo "installed: ${wanted[*]} (running as $RUN_AS)"
systemctl list-timers 'scout-*' --no-pager
